# Cold-start baseline — model v1, weights loaded from S3

Measured 2026-09-25 against `wildlens-dev-process` in `ap-southeast-2`, tagging
`Sus_scrofa_1.JPG` (309 KB). This is the **before** figure. Phase 5 bakes the
weights into the image and re-measures against it.

## Configuration

| Setting | Value | Note |
|---|---|---|
| Memory | 3008 MB | Design calls for 4096. This account is new and capped at 3008 |
| Timeout | 900 s | |
| Ephemeral storage | 4096 MB | Holds 470 MB of weights plus the image being processed |
| Architecture | x86_64 | |
| Image size | 1.04 GB compressed | CPU-only PyTorch; the CUDA build would be roughly 3× |
| Weights | Downloaded from S3 per execution environment | |

## Results

| Run | Init | Handler | **Billed** | Peak memory |
|---|---|---|---|---|
| First ever (image not yet cached) | 10.00 s *(timed out)* | 39.6 s | **55.2 s** | 2846 MB |
| Cold #1 | 6.53 s | 17.1 s | **23.6 s** | 2841 MB |
| Cold #2 | 4.58 s | 16.3 s | **20.9 s** | 2825 MB |
| Cold #3 | 4.67 s | 19.4 s | **24.0 s** | 2836 MB |
| Warm #1 | — | 8.3 s | **8.3 s** | 2863 MB |
| Warm #2 | — | 8.4 s | **8.4 s** | 3000 MB |

Cold starts were forced by updating an environment variable, which discards
every warm execution environment.

**Summary, steady state:** p50 cold 23.6 s · p95 cold 24.0 s · warm 8.35 s.

## Three latency regimes, not two

The usual cold/warm split hides a third case that only shows up on a first
deployment:

1. **First invocation after a deploy — 55 s.** Lambda must pull the 1.04 GB
   image before anything runs. Init exceeded its 10-second budget and Lambda
   fell back to running initialisation inside the invocation, so the whole cost
   lands on one request.
2. **Cold, image already cached — 21–24 s.** A fresh execution environment, but
   the image layers are local to the worker. Init drops to 4.6–6.5 s.
3. **Warm — 8.3 s.** Same environment, models already in memory.

Regime 1 happens once per deployment and is invisible in any average. It is
also the one a demo hits, because a demo follows a deploy.

## Where the time goes

Handler-level timings, from the response body:

| Phase | Cold | Warm |
|---|---|---|
| Download the object from S3 | 95 ms | ~90 ms |
| Fetch weights from S3, load SpeciesNet | 4.2–5.2 s | **0 ms** |
| Inference | 12–34 s | 8.1 s |
| Thumbnail and upload | 90 ms | ~90 ms |

Two things stand out.

**The version cache works exactly as designed.** Weight loading costs 0 ms on
every warm invocation. That part needs no optimisation.

**Inference is the whole problem, and it is not all inference.** CloudWatch
shows MegaDetector reporting its own load time on *every* invocation:

```
cold:  Loaded model in 25.61 seconds
warm:  Loaded model in 0.69 seconds
warm:  Loaded model in 0.96 seconds
```

`load_and_run_detector_batch` takes a *path* and deserialises the 268 MB
detector each time it is called. The module-level cache holds the path, not the
loaded object, so this cost is paid per request — about a second when warm,
about twenty-five when the file has just been written to `/tmp`.

Actual inference is therefore roughly 7 s warm. Locally the same image takes
about 1 s, on a machine with Metal acceleration and more CPU. At 3008 MB Lambda
allocates roughly 1.8 vCPU.

## Memory is the pressing constraint

Peak usage reached **3000 MB of 3008**, or 99.7%. There is no headroom: a
larger image, or one with several detections to classify, will exhaust it and
the invocation will be killed with no useful error.

The design specifies 4096 MB. That was not a round number chosen for comfort —
the measurement shows it is what the workload needs. This account carries the
reduced quotas AWS applies to new accounts, capping Lambda memory at 3008 MB
and total concurrent executions at 10, against defaults of 10240 and 1000. Both
are adjustable through Service Quotas.

Until the increase lands, every figure here should be read as measured under a
memory ceiling the workload is already touching.

## What Phase 5 should attack, in order of evidence

| Target | Expected saving | Basis |
|---|---|---|
| Cache the loaded detector object, not its path | ~25 s cold, ~1 s warm | Measured directly in the logs above |
| Bake weights into the image | 4.2–5.2 s cold | `modelLoadMs` on cold runs |
| Raise memory to 4096 MB | Unknown, and removes the OOM risk | Peak at 99.7% of the current ceiling |

Baking the weights enlarges the image, which lengthens regime 1. Whether that
trade is worth it is a question the next measurement answers rather than one to
argue about now.
