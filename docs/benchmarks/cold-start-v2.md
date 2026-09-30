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

## Should the weights be baked into the image?

Cold is still 21.5 s, above the 15 s line the plan set for reconsidering this, so
the case is open rather than closed:

| | For | Against |
|---|---|---|
| Cold start | Removes the S3 fetch, part of the 8–9.6 s model load | |
| Regime 1 | | Adds ~470 MB to a 1.04 GB image, lengthening the pull |
| Frequency | Cold happens on every scale-up and after every idle period | Regime 1 happens once per deploy |

The trade is a recurring saving against a one-off cost, which normally favours
the recurring side. But the 8–9.6 s model load is **download plus
deserialisation**, and only the download part goes away — the 268 MB detector
still has to be deserialised from a local file either way. Without splitting
that number, the expected saving is unknown.

**Next measurement, before any decision: instrument the two halves of
`artefact_dir` and `load_detector` separately.** If the download is 2 s of the 9,
baking is not worth a larger image.

## Memory is no longer the pressing constraint

Peak fell to 2861 MB of 3008, and to 2335–2454 MB on most runs. The headroom
that did not exist in v1 exists now. The memory increase is still worth
requesting — the design calls for 4096 MB — but it has stopped being a
prerequisite for anything.

Note that Lambda function memory is **not** an adjustable Service Quota: all 70
Lambda quotas in this region were checked and none covers it. The new-account
3008 MB cap is raised through an AWS Support case, not `service-quotas`.
