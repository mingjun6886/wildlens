# Runbook

What to do when something is wrong. Each section states the symptom first, because
that is what you have when you arrive.

Sections marked **(Phase 9)** are placeholders for procedures that need the
dashboard and tracing to exist. Everything else is usable now.

## Before anything

```bash
export AWS_PROFILE=wildlens          # omit this and you are in the wrong account
cd ~/Projects/wildlens
terraform -chdir=infra/terraform/envs/dev output
```

Terraform output is the source of truth for every name and URL. Nothing in this
file hard-codes a bucket or a queue, because the random suffix changes whenever the
estate is destroyed and recreated.

A token for calling the API, and what is in it:

```bash
TOKEN=$(./scripts/get-token.sh)
API=$(terraform -chdir=infra/terraform/envs/dev output -raw api_invoke_url)
./scripts/decode-token.sh "$TOKEN"
```

---

## The DLQ alarm fired

**Symptom:** email from `wildlens-dev-dlq-not-empty`. A message failed three times.

Nothing consumes the dead-letter queue, by design: a message there is one a human
needs to look at, and an automatic consumer would hide exactly the failures worth
seeing.

**1. Read the message without removing it.**

```bash
DLQ=$(terraform -chdir=infra/terraform/envs/dev output -raw dlq_url)
aws sqs receive-message --queue-url "$DLQ" --max-number-of-messages 10 \
  --visibility-timeout 0 --query 'Messages[].Body' --output text | python3 -m json.tool
```

`--visibility-timeout 0` returns the message to the queue immediately, so reading
it does not hide it from the next person.

**2. Find out why it failed.** The body carries the bucket and key. The function's
own logs carry the reason:

```bash
aws logs filter-log-events --log-group-name /aws/lambda/wildlens-dev-process \
  --filter-pattern '"<the object key>"' \
  --start-time $(( ($(date +%s) - 86400) * 1000 )) \
  --query 'events[].message' --output text
```

**3. Decide which of three cases it is.**

| What the logs show | What it means | What to do |
|---|---|---|
| `permanent failure, not retrying` | The handler classified it and wrote a `FAILED` record. The DLQ copy is a duplicate of a decision already recorded | Delete the message. Check the record's `errorReason` and see the table below |
| A traceback, three times | A transient failure that was not transient — a bug, or a dependency that stayed down | Fix the cause first. The message can be replayed afterwards |
| `s3:TestEvent` or no `Records` key | S3's notification handshake, not a file | Delete the message. Harmless |

**4. Replay, once the cause is fixed.** There is no automated redrive; this is
deliberate at this scale.

```bash
QUEUE=$(terraform -chdir=infra/terraform/envs/dev output -raw ingest_queue_url)
aws sqs start-message-move-task \
  --source-arn "$(aws sqs get-queue-attributes --queue-url "$DLQ" \
      --attribute-names QueueArn --query Attributes.QueueArn --output text)" \
  --destination-arn "$(aws sqs get-queue-attributes --queue-url "$QUEUE" \
      --attribute-names QueueArn --query Attributes.QueueArn --output text)"
```

**5. Confirm it drained.**

```bash
aws sqs get-queue-attributes --queue-url "$DLQ" \
  --attribute-names ApproximateNumberOfMessages --query Attributes --output json
```

The alarm clears on its own within five minutes of the queue emptying.

---

## A file is stuck at PENDING

**Symptom:** `GET /files/{fileId}` keeps returning `PENDING`.

`PENDING` means the upload function reserved the record and the object has not
been processed. Three causes, distinguishable in order:

**1. The object never arrived.** The client got a URL and did not use it, or the
PUT failed.

```bash
RAW=$(terraform -chdir=infra/terraform/envs/dev output -raw raw_bucket_name)
aws s3api head-object --bucket "$RAW" --key "<fileId>.jpg"
```

A `404` here is the whole answer: there is nothing to process. The record expires
after an hour. If the client reported a `403 SignatureDoesNotMatch`, see
**A PUT is rejected** below.

**2. The object arrived and the queue has not caught up.** Check the depth:

```bash
aws sqs get-queue-attributes --queue-url "$QUEUE" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
  --query Attributes --output json
```

`NotVisible` greater than zero means a worker holds it. With a concurrency ceiling
of 2 and 6–20 s per image, a backlog of 50 takes about eight minutes. This is the
ceiling working, not a fault.

**3. The notification never fired.** If the object exists and the queue is empty,
the S3 notification is missing — which happens if the queue policy was changed by
hand.

```bash
aws s3api get-bucket-notification-configuration --bucket "$RAW"
```

Expect one `QueueConfiguration` for `s3:ObjectCreated:*`. If it is absent,
`terraform apply` restores it.

---

## A file is at FAILED

**Symptom:** the status endpoint returns `FAILED` with an `errorReason`.

The reason is written for this table. A `FAILED` record keeps a seven-day expiry,
so there is time to investigate but it will not sit there forever.

| `errorReason` | Meaning | Action |
|---|---|---|
| `not a readable image: ...` | The bytes are not an image PIL can open. Truncated upload, or a renamed file | Tell the client. Re-uploading the same bytes will fail identically |
| `content does not match the key it was stored under` | The digest the client claimed is not the digest of what arrived. A client-side hashing bug, or a substitution attempt | Check whether the same caller does this repeatedly. See ADR 6 |
| `unsupported file type: .mp4` | Video, which arrives in Phase 12 | Expected until then |
| `object is empty` | Zero bytes | Client-side bug; the upload function rejects `size: 0`, so this means the PUT sent nothing |

Retrying is allowed: the upload function issues a new URL for a `FAILED` record,
because a permanent failure is about those bytes, not about that digest forever.

---

## Every API call returns 401

**Symptom:** `401` with no body, on routes that worked before.

API Gateway's Cognito authoriser gives no reason, ever. Work through these in
order; the first two cover almost every case.

One command answers the first three:

```bash
./scripts/decode-token.sh "$TOKEN"
```

Do not try to decode it with `base64 -d`. A JWT payload is base64url with the
padding stripped, and `base64 -d` rejects it with a parse error that looks like a
malformed token — the first version of this runbook had exactly that bug.

**1. The wrong token.** Cognito issues an **ID token** and an **access token**, and
the authoriser validates the ID token. Sending the access token returns `401` with
no indication that the *kind* was wrong rather than the token invalid. Expect
`token_use id`; the script warns if it is `access`.

**2. Expired.** Tokens last one hour. The script prints the time remaining.

**3. The wrong pool.** If the estate was destroyed and recreated, the client holds
a token from a pool that no longer exists.

```bash
terraform -chdir=infra/terraform/envs/dev output cognito_user_pool_id cognito_client_id
```

`iss` must end with the pool id and `aud` must equal the client id.

**4. The header.** It must be `Authorization: <token>` — the authoriser is
configured for the raw token, not `Bearer <token>`. This is the one the script
cannot see.

---

## Something works from curl and fails in the browser

**Symptom:** a request succeeds from a terminal and fails in the browser.

**It is never the backend, and it is one of two things.** Telling them apart first
saves the most time, because the fixes are in different files.

| Console message | Cause | Where to fix |
|---|---|---|
| `No 'Access-Control-Allow-Origin' header` | CORS: the server did not permit this origin | API Gateway, or the bucket's CORS rule |
| `Failed to fetch`, with `Refused to connect ... Content-Security-Policy` above it | CSP: **this page** refused to make the request | the `<meta>` policy in `web/index.html` |

A CSP refusal is the easier one to misread. The fetch rejects with no status code,
because the request is never sent — so it looks like a network fault or a CORS
problem, and the request does not appear in the Network tab at all. **If there is
no request in Network, it is CSP, not CORS.**

This has already happened once: the policy named Cognito under `connect-src` but
not S3, so the presigned PUT - a fetch, and therefore governed by `connect-src` -
was refused by the page itself while the bucket's CORS configuration was perfectly
correct. Any new cross-origin destination has to be added to the policy.

For the CORS half: a `curl` request sends no `Origin` header, so it never triggers
the check that is failing.

CORS lives in two places, and configuring one and not the other is the hard case:
the preflight succeeds, so the configuration looks complete, while the real
response is blocked.

**1. The preflight.** No `Authorization` header, exactly as a browser sends it:

```bash
curl -s -o /dev/null -D - -X OPTIONS "$API/files/abc" \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: GET" | grep -i 'HTTP\|access-control'
```

Expect `200` and three `access-control-*` headers.

**2. The real response.** The handler adds its own header, from `ALLOWED_ORIGIN`:

```bash
curl -s -o /dev/null -D - -H "Authorization: $TOKEN" "$API/files/abc" \
  | grep -i access-control-allow-origin
```

If the preflight has the header and this does not, the function's `ALLOWED_ORIGIN`
disagrees with the API module's `allowed_origin`. They are set from one variable in
`envs/dev/variables.tf`; a mismatch means one of the two was deployed and the other
was not.

**3. The bucket, for an upload.** The browser PUTs straight to S3, which answers
its own preflight:

```bash
UPLOAD_URL="<uploadUrl from POST /upload>"
curl -s -o /dev/null -D - -X OPTIONS "$UPLOAD_URL" \
  -H 'Origin: http://localhost:3000' \
  -H 'Access-Control-Request-Method: PUT' \
  -H 'Access-Control-Request-Headers: content-type' | grep -i 'HTTP\|access-control'
```

Expect `200` and `Access-Control-Allow-Methods: PUT`. A missing configuration here
is `NoSuchCORSConfiguration` from `get-bucket-cors`, and `terraform apply` restores
it. If this preflight succeeds and the browser still fails, it is the CSP — see the
table above.

---

## A route change had no effect

**Symptom:** a route was edited, `terraform apply` succeeded, and the API still
behaves as before. No error anywhere.

A REST API has a configuration layer and a **deployment** that freezes it. Changing
the configuration does nothing until a new deployment exists, and nothing reports
the gap.

Terraform handles this through a hash over every method, integration and CORS
resource in `modules/api/main.tf`. A correct apply shows:

```
module.api.aws_api_gateway_deployment.api must be replaced
```

If that line is **absent** from the plan after a route change, the hash is missing
an entry. Add the omitted resource to `triggers.redeploy` — the symptom is one
route that never deploys while everything else works.

As an immediate workaround:

```bash
aws apigateway create-deployment \
  --rest-api-id "$(terraform -chdir=infra/terraform/envs/dev output -raw rest_api_id)" \
  --stage-name v1
```

---

## A PUT to a presigned URL is rejected

**Symptom:** `403 SignatureDoesNotMatch` on the upload.

The URL binds both the content type and the exact length, which is what makes the
declared size enforceable. Both must match what `/upload` returned in
`requiredHeaders`.

| Cause | Check |
|---|---|
| Length differs from the declared `size` | The client must hash and measure the same bytes, in one pass |
| `Content-Type` differs | Send exactly the value from `requiredHeaders`, not the browser's guess |
| URL older than 15 minutes | Request a new one |
| Extra signed header added | The client must send only the two required headers |

A `403` here is the size limit working. See ADR 6.

---

## The model is returning wrong tags

**Symptom:** a tag is clearly wrong for the photograph.

**First, check whether it is a known limitation rather than a fault.**

| Observation | Status |
|---|---|
| A `human` tag on a photograph with no person | Known. MegaDetector filters its own *person* category, but SpeciesNet has a `Homo_sapiens` class and can apply it to a cropped animal. A second-stage error that the first stage cannot prevent. Recorded in `ml/eval/answer_key.yaml` |
| Over-counting on a close-range frame | Known. The two cattle frames in the test set account for five of the seven false positives, and the answer key itself carries real uncertainty there |
| A species that is not in `ml/labels.txt` | Impossible. The classifier has 46 classes and can only return one of them |

**Then verify the model version that produced it.** The record stores it:

```bash
aws dynamodb get-item --table-name wildlens-dev-files \
  --key '{"fileId":{"S":"<fileId>"}}' \
  --query 'Item.{version:modelVersion.S,tags:tags}' --output json
```

**Then re-score the whole test set** rather than reasoning from one image:

```bash
source .venv/bin/activate
export WILDLENS_TEST_IMAGES="<path to the 26 test images>"
make eval
```

The expected figures are Precision 0.821, Recall 1.000, F1 0.901, with TP 32,
FP 7, FN 0. A change in any of them means the ML path changed; unchanged figures
mean the complaint is about one image rather than about the model.

---

## Costs look wrong

```bash
aws budgets describe-budget --account-id 895770859102 --budget-name wildlens-monthly \
  --query 'Budget.CalculatedSpend' --output json
```

In order of plausibility:

| Suspect | Check |
|---|---|
| The tagging function ran far more than expected | Invocation count on `wildlens-dev-process`. The concurrency ceiling of 2 bounds the rate, not the total |
| A retry loop | DLQ depth, and whether the same `fileId` appears repeatedly in the logs |
| Untagged ECR images accumulating | `aws ecr describe-images`. The lifecycle policy keeps 3 |
| Logs | Retention is 14 days on every group. A group with no retention means something created it outside Terraform |

Nothing in this estate is billed per hour except storage, so a cost surprise is
almost always invocations.

---

## Stopping everything

```bash
terraform -chdir=infra/terraform/envs/dev destroy
```

This removes every resource and every uploaded object — the buckets carry
`force_destroy`. It does **not** remove the Terraform state bucket, which was
created outside Terraform on purpose, nor the SSM parameter holding the test user's
password.

The bucket name suffix is regenerated on the next apply. Nothing hard-codes it;
every script reads it from Terraform output.

---

## (Phase 9) Tracing one request end to end

Needs X-Ray. Until then, the correlation id does the same job less conveniently:
every function logs `correlationId`, and for a queued file it is the SQS message
id.

```bash
aws logs filter-log-events --log-group-name /aws/lambda/wildlens-dev-process \
  --filter-pattern '"<correlationId>"' --query 'events[].message' --output text
```

## (Phase 9) The dashboard

Needs the dashboard.
