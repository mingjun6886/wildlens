# Cold-start after caching the loaded detector — model v1

Measured 2026-09-30 against `wildlens-dev-process` in `ap-southeast-2`, tagging
`Sus_scrofa_1.JPG` (309 KB) — the same function, region, image and method as
`cold-start-v1.md`, so the two are directly comparable. Configuration is
unchanged: 3008 MB, 900 s, 4096 MB ephemeral, x86_64, image 1.04 GB compressed.

The change measured here is one thing only: `tag_images` receives an
already-loaded MegaDetector instead of a path to its weights, and the module
cache holds that loaded object.

## Results

| Run | Init | Handler | **Billed** | Peak memory | Model load | Inference |
|---|---|---|---|---|---|---|
| First ever (image not yet cached) | *in-invocation* | 42.3 s | **42.3 s** | 2830 MB | 23.6 s | 8.3 s |
| Cold #1 | 4.97 s | 17.3 s | **22.3 s** | 2861 MB | 9.6 s | 7.5 s |
| Cold #2 | 4.75 s | 16.7 s | **21.5 s** | 2335 MB | 9.6 s | 6.9 s |
| Cold #3 | 4.65 s | 14.2 s | **18.8 s** | 2366 MB | 8.0 s | 5.9 s |
| Warm #1 | — | 6.35 s | **6.4 s** | 2454 MB | 0 ms | 6.2 s |
| Warm #2 | — | 6.45 s | **6.5 s** | 2454 MB | 0 ms | 6.3 s |

Cold starts were forced by changing the function description, which discards
every warm execution environment without rewriting the environment block. The
DynamoDB row was deleted between runs, or the idempotency guard would have
returned early and the run would have measured the guard rather than the
pipeline.

## Before and after

| Metric | v1 | v2 | Change |
|---|---|---|---|
| First invocation after deploy | 55.2 s | **42.3 s** | **−12.9 s (−23%)** |
| Cold, p50 | 23.6 s | **21.5 s** | −2.1 s (−9%) |
| Cold, p95 | 24.0 s | **22.3 s** | −1.7 s (−7%) |
| Warm | 8.35 s | **6.40 s** | **−1.95 s (−23%)** |
| Peak memory | 3000 MB | **2861 MB** | −139 MB |

## The prediction was half wrong, and the half that was wrong is instructive

`cold-start-v1.md` predicted this change would save **"~25 s cold, ~1 s warm"**.
The warm figure was right — 1.95 s, slightly better than predicted. The cold
figure was wrong by an order of magnitude, and it was wrong for two separate
reasons worth separating.

### Reason one: a cache cannot help the call that fills it

A cold start is, by definition, the first invocation in a new execution
environment. There is nothing cached yet. Caching converts a **per-request** cost
into a **per-environment** cost; it cannot remove the one request that pays it.

The cost did not disappear on cold runs — it moved. It used to be charged to
`inference`, because the tagger loaded the detector inside the timed inference
section. It is now charged to `modelLoad`, where it belongs:

| | v1 cold | v2 cold |
|---|---|---|
| Model load | 4.2–5.2 s | **8.0–9.6 s** |
| Inference | 12–34 s | **5.9–7.5 s** |

Same work, honest accounting. The small cold gain that does exist (~2 s) comes
from deserialising the detector once instead of twice, plus a shorter init now
that `tagger.py` no longer imports MegaDetector at module scope.

### Reason two: the v1 report mis-attributed a log line

`cold-start-v1.md` presents this as the cold-start evidence:

```
cold:  Loaded model in 25.61 seconds
warm:  Loaded model in 0.69 seconds
```

**25.61 s cannot have come from a cold run.** The cold handlers in that report
ran for 17.1, 16.3 and 19.4 s in total. A single step inside them cannot have
taken 25.61 s. That line came from the first-ever invocation, whose handler ran
39.6 s — regime 1, not regime 2.

The v1 report even gave the right explanation in the next paragraph — "about
twenty-five when the file has just been written to `/tmp`" — and then attached
it to the wrong regime. Everything downstream inherited the error: the ranked
target list, and the Phase 5 plan built from it.

The lesson is narrow and practical: **a log line without a request ID is not
evidence.** The three-regime split was documented in v1 and the log excerpt was
still labelled with a two-regime vocabulary. Correlating by request ID, which
this project already does for application logs, would have caught it — the
platform's own log lines were the ones left uncorrelated.

## What the numbers do justify

**The change was worth making**, on grounds other than the ones predicted:

1. **Warm dropped 23%.** Warm is the common case for any sustained load, and
   this is the figure a queue-driven system spends most of its life at.
2. **Regime 1 dropped 23%,** from 55.2 s to 42.3 s. This is the one a demo hits,
   because a demo follows a deploy. Init no longer exceeds its 10-second budget.
3. **Peak memory fell 139 MB**, from 99.7% of the ceiling to 95.1%. It was
   expected to rise, and the reason it fell is the same reason the cache is now
   capped at one entry: the old code held a freshly-loaded detector while the
   previous one was still awaiting collection, so two were briefly resident. Now
   exactly one ever is.
4. **The accuracy figure is unchanged** — byte-identical evaluation output except
   the timing line. Precision 0.821, recall 1.000, F1 0.901.

## Baking the weights into the image: built, measured, removed

The open question above was answered by building it. The `models` layer went into
the image at `/opt/models/v1`, positioned after the dependency install and before
the application code so that a code-only deploy would not re-pull it.

### The split that decided it

`get_models` was instrumented to time its three stages separately, because the
aggregate could not answer the question — baking removes the fetch and nothing
else.

| Stage | From S3 | From the image | Removed by baking? |
|---|---|---|---|
| Fetch artefacts | **3.45 s** | 0 s | yes |
| Deserialise the detector | 4.86 s | 4.86 s | no |
| Load SpeciesNet and labels | 0.50 s | 0.85 s | no — slightly slower |
| **Total** | **8.83 s** | **5.85 s** | |

A single "model load takes 9 seconds" figure reads as a complete case for baking
whatever the split is. It is 39% of the number.

### The result, which went the other way

| | Not baked | Baked | Change |
|---|---|---|---|
| First invocation after deploy | 24.0 s | **56.7 s** | **+32.7 s** |
| Cold, p50 | 20.1 s | **18.2 s** | −1.9 s |
| Model load, first read | 8.8 s | **42.1 s** | +33.3 s |
| Model load, subsequent | 8.8 s | 5.9 s | −2.9 s |
| Image, compressed | 1.04 GB | 1.57 GB | +458 MB |

**The first read of the baked weights took 42.1 s — nearly five times what
downloading the same bytes from S3 costs.** Lambda fetches container image blocks
on demand over the network and caches them per worker, so a 470 MB file read from
a cold image layer is slower than an S3 GET of the same file. Baking does not
move the bytes closer; it moves them onto a lazier transport.

Once the blocks are cached the saving appears, and it matches the measured fetch
time: 8.8 s to 5.9 s.

### Why it was removed

Roughly **seventeen cold starts per deploy** would be needed to repay 32.7 s at
1.9 s each. This system does not see that, and the first invocation after a
deploy is the one a demo hits, because a demo follows a deploy.

There is also something this measurement could **not** establish: a fresh worker
could not be forced, so how often the 42.1 s is paid is unknown. Lambda caches
image blocks per worker, so every new worker pays it again — on every scale-out,
not once per deploy. An unmeasured cost that can only make the trade worse argues
for not taking it.

The instrumentation stayed. The Dockerfile and build-script changes were reverted,
and the commit that added them is in the history if the trade ever changes — a
larger cold-start volume, or provisioned concurrency, would flip it.

### What the layer ordering was worth knowing anyway

Two deploys of the **same 1.04 GB image** gave first-invocation times of 42.3 s
and 24.0 s. The difference was how much of the image changed: the PyTorch layer in
the first case, three small Python files in the second. Lambda re-pulls changed
layers, not whole images.

That is why the baked layer was positioned before the application code, and the
positioning worked as intended. It was the wrong answer to a different question:
layer ordering controls what a *deploy* costs, and the 42.1 s was paid by the
first *read*, which no ordering avoids.

### An unplanned result: the idempotency guard fired for real

The invocation table showed two runs where the script issued one call:

```
20:20:50  billed  56.7s  mem 2294 MB   completed, wrote DONE
20:21:01  billed   6.5s  mem  484 MB   "skipped: already DONE"
```

The likely cause is a client-side read timeout and retry while the first call was
still running — at-least-once delivery arriving from the client side rather than
from SQS. The duplicate returned in 6.5 s instead of paying 56.7 s again, and no
second record was written.

This was not a test. It is the first time the guard has been exercised by
something other than a deliberate attempt to trigger it.

## Where the remaining time is

| Target | Cost | Status |
|---|---|---|
| Deserialise the detector | **4.86 s** | Untouched. The largest single item |
| Fetch from S3 | 3.45 s | Baking removes it and costs more elsewhere |
| Inference | 5.9–7.5 s | Bounded by ~1.8 vCPU at 3008 MB |
| Load SpeciesNet | 0.50 s | Not worth attention |

The detector deserialisation is now the biggest remaining item and is larger than
the fetch that Phase 5 spent its effort on. `torch.load` on a pickled module is
the slow path; TorchScript, or storing a `state_dict` and rebuilding the module,
are the candidates. Both change how artefacts are produced, so they belong with
the model-versioning work rather than here.


## Swapping the model version, verified

With the weights coming from S3, `MODEL_VERSION` is the whole mechanism. A second
prefix was populated by a server-side copy (`v2`, the same weights as `v1` — this
tests the mechanism, not a different model), and the variable was changed with no
rebuild and no code change:

```
{"msg": "model load breakdown", "version": "v2", "source": "s3",
 "fetchMs": 3421, "detectorMs": 5777, "speciesMs": 380}
Loading models from s3://wildlens-dev-models-5fmld4/v2/
Loaded version v2 from s3 in 9.6s
```

The record written carries `modelVersion: v2`, so a result can be attributed to
the model that produced it. This is the mechanism Phase 10 demonstrates; it is
recorded here because the measurement above is what kept it intact — baking the
weights in would have left this path unexercised for the version people actually
run.

## Memory is no longer the pressing constraint

Peak fell to 2861 MB of 3008, and to 2335–2454 MB on most runs. The headroom
that did not exist in v1 exists now. The memory increase is still worth
requesting — the design calls for 4096 MB — but it has stopped being a
prerequisite for anything.

Note that Lambda function memory is **not** an adjustable Service Quota: all 70
Lambda quotas in this region were checked and none covers it. The new-account
3008 MB cap is raised through an AWS Support case, not `service-quotas`.
