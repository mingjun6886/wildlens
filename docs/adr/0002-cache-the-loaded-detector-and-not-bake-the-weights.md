# 2. Cache the loaded detector; do not bake the weights into the image

**Status:** accepted · Phase 5 · 2026-09-30

## Context

The Phase 3 baseline measured a cold start at 23.6 s p50 and a warm invocation at
8.35 s. Two optimisations were on the table, and the build plan ranked baking the
weights into the container image first.

Instrumenting the model load contradicted that ranking:

| Stage | Cost | Share |
|---|---|---|
| Fetch three artefacts from S3 | 3.45 s | 39% |
| Deserialise the 268 MB detector | **4.86 s** | **55%** |
| Load SpeciesNet and the labels | 0.50 s | 6% |

Baking the weights removes the fetch and nothing else. A single aggregate figure —
"the model load takes 9 seconds" — reads as a complete case for baking whatever
the split turns out to be, which is why the split had to be measured before the
decision rather than after.

## Decision

Cache the **loaded detector object** in module state, keyed by version, with the
cache holding at most one entry. Do **not** bake the weights into the image; keep
fetching them from S3.

## Consequences

| Metric | Before | After | Change |
|---|---|---|---|
| Warm | 8.35 s | **6.40 s** | −23% |
| Cold, p50 | 23.6 s | **21.5 s** | −9% |
| First invocation after a deploy | 55.2 s | **42.3 s** | −23% |
| Peak memory | 3000 MB | **2861 MB** | −139 MB |
| Accuracy (F1) | 0.901 | 0.901 | unchanged |

**A cache cannot help the invocation that fills it.** The cold-start gain is 2 s
against a predicted 25, because a cold start is by definition the first call in a
new execution environment. The change converted a per-request cost into a
per-environment one; it did not remove the request that pays it. Warm is the
common case for a queue-driven system, which is where the gain landed.

**Peak memory fell where it was expected to rise.** The previous code loaded a
detector while the preceding one was still awaiting collection, so two were
briefly resident. Exactly one ever is now.

**The version cache is capped at one entry, and that limit is load-bearing.**
While the cache held a *path*, several versions cost a few bytes, and a comment
advertised holding v1 and v2 at once for an A/B comparison. Holding a *loaded*
detector is 560 MB each against a 3008 MB ceiling, so a second version would be
an out-of-memory kill rather than a slower path. Eviction happens **before** the
new load, because holding both during the transition is the one moment two exist.

**The tagger no longer imports MegaDetector.** The detector arrives as an argument
with one method, so swapping detector implementations is `model_loader`'s problem
and the tests supply a fake object instead of patching a module import.

**Accuracy is unchanged and was proved, not assumed.** The 26-image evaluation
output is byte-identical except its timing line. This is what the answer key is
for: a safety net for refactoring, not a number for a CV.

## Why baking was rejected after being built

It was implemented, measured, and removed.

| | Not baked | Baked |
|---|---|---|
| First invocation after a deploy | 24.0 s | **56.7 s** |
| Cold, p50 | 20.1 s | 18.2 s |
| Model load, first read | 8.8 s | **42.1 s** |
| Image, compressed | 1.04 GB | 1.57 GB |

**The first read of the baked weights took 42.1 s — nearly five times what
downloading the same bytes from S3 costs.** Lambda fetches container image blocks
on demand over the network and caches them per worker, so a 470 MB file read from
a cold image layer is slower than an S3 GET. Baking does not move the weights
closer; it moves them onto a lazier transport.

Roughly seventeen cold starts per deploy would repay the 32.7 s cost at 1.9 s
each. This system does not see that, and the first invocation after a deploy is
the one a demo hits.

One cost could not be measured: a fresh worker could not be forced, so how often
the 42.1 s recurs is unknown. Blocks are cached per worker, so a scale-out pays it
again. An unmeasured cost that can only worsen the trade argues against taking it.

Keeping the S3 path has a second benefit that baking would have removed: it stays
exercised for the version actually in use, so `MODEL_VERSION=v2` is a
configuration change rather than an untested branch.

The commit that added baking is in the history. More cold-start volume, or
provisioned concurrency, would flip the decision.

## What remains

Deserialising the detector, at 4.86 s, is now the largest single item and is
larger than the fetch this phase spent its effort on. `torch.load` on a pickled
module is the slow path; TorchScript, or storing a `state_dict` and rebuilding the
module, are the candidates. Both change how artefacts are produced, so they belong
with the model-versioning work.
