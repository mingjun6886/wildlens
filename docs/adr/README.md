# Architecture decision records

One file per decision, numbered in the order they were taken. Each records the
context at the time, what was decided, what that costs, and what was rejected.

They are written when the decision is made, not at the end. The reasoning and the
measurements are available then; three months later they would be reconstructed
from a git log, which is the thing these documents exist to avoid.

| # | Decision | Phase |
|---|---|---|
| [1](0001-queue-between-s3-and-the-tagger.md) | Put a queue between S3 and the tagging function | 4 |
| [2](0002-cache-the-loaded-detector-and-not-bake-the-weights.md) | Cache the loaded detector; do not bake the weights into the image | 5 |
| [3](0003-rest-api-rather-than-http-api.md) | REST API rather than HTTP API | 6 |
| [4](0004-scan-and-filter-in-memory.md) | Scan the table and filter in memory | 6 |
| [5](0005-accept-the-29-second-ceiling-on-query-by-file.md) | Accept the 29-second ceiling on query-by-file | 6 |
| [6](0006-verify-content-against-the-key-it-was-stored-under.md) | Verify content against the key it was stored under | 6 |
| [7](0007-one-iam-role-per-function.md) | One IAM role per function | 4–6 |
| [8](0008-objects-outlive-nothing-they-describe.md) | Records and the objects they describe expire together | 6 |
| [9](0009-keep-tokens-in-sessionstorage.md) | Keep tokens in sessionStorage, and defend with CSP instead | 7 |

Three of these record a decision that was **reversed or corrected**: baking the
weights into the image was built and removed, HTTP API was recommended and then
rejected, and the 30-day object expiry was a guard that became a defect. Those are
kept rather than tidied away, because the measurement that overturned each one is
the useful part.
