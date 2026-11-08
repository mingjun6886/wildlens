# 13. Tag video in the existing function, frame by frame, with MaxN

**Status:** accepted · Phase 12 · 2026-11-05

## Context

Until Phase 12 the upload API refused video, and the tagger failed any file that
was not an image. The design document's §6.3 fixed the outline: sample one frame
per second, at most 60 frames, tag each, sum the per-species counts, and take the
thumbnail from the first frame.

The constraints are the tagger's. A frame takes about 7 s to tag warm, and loading
the models cold takes about 41 s. The `process` function has a 900 s timeout, and
the event source mapping caps concurrency at 2 (ADR 1). `/search/byfile` tags its
sample synchronously inside API Gateway's 29 s limit (ADR 5).

## Decision

- **Counts are MaxN**: for each species, the largest count seen in any single
  frame. Summing counts the same animal once per second, so one wallaby standing
  still for a minute became 60 wallabies. MaxN is the standard camera-trap measure
  and never exceeds what was visible at once.
- **One frame per second, at most 60** (the first minute), read sequentially with
  OpenCV in `services/process/video.py`. 60 frames at about 7 s is about 7 minutes,
  inside the 900 s timeout with room for a cold start.
- **A time guard** stops tagging when less than 60 s of the invocation remains, and
  the record is written from the frames tagged so far; `frameCount` says how many.
  Frames are tagged one at a time with `tag_image`, because `tag_images` detects
  every frame before classifying any and could not be interrupted. If not even one
  frame could be tagged, the file is `FAILED` rather than retried: a retry meets
  the same clock.
- **The existing `process` function** handles video, on the existing queue. A
  `.mp4`, `.mov` or `.avi` key takes a branch; idempotency, the state machine,
  the dead-letter queue, notifications, metrics and tracing are reused unchanged.
- **The thumbnail comes from the busiest frame**: the most animals, the earliest
  winning a tie, the first frame if none has an animal. The first second of a trap
  clip is often empty, because the animal is what triggered the camera.
- **`/search/byfile` stays image only**, and says so with a 400. Even a short
  video takes minutes, far beyond the 29 s ceiling.
- **Limits per kind**: 25 MB for an image, as before, and 100 MB for a video. A
  minute of 1080p trap or phone video is 50 to 130 MB, and only the first minute
  is examined.

## Alternatives considered

- **A separate video function and queue.** Isolates the slow work from images,
  but doubles the infrastructure, the IAM roles and the cold starts, for a load
  this system does not have.
- **Step Functions fan-out, one branch per frame.** Faster wall-clock time, but
  each branch cold-starts and loads the models (about 41 s), so it costs more and
  is not faster for a one-minute clip at concurrency 2.
- **Summing counts across frames**, as the design document said. Rejected for the
  reason above: it counts duration, not animals.
- **The first frame as the thumbnail**, as the design document said. Often a
  picture of an empty clearing.

## Consequences

- A video holds one of the two concurrent executions for several minutes, so
  images uploaded behind two videos wait. Accepted at this scale.
- About 7 minutes at 3 GB is roughly US$0.02 per video, against about US$0.0005
  per image.
- `InferenceDuration` is the mean time per frame for a video, so the dashboard's
  percentiles stay comparable with images. A video emits it once, not once per
  frame.
- OpenCV, through the FFmpeg bundled in its wheel, probes content rather than the
  name. A JPEG renamed to `.mp4` opens as a one-frame video with no duration and
  is tagged correctly; it is not `FAILED`. Harmless, and pinned by a test.
- The web client waits up to 16 minutes for a video, polling every 10 s, instead
  of 2 minutes at 2 s.
- The headless OpenCV wheel is pinned (5.0.0.93) in both the image and CI, and
  the image build decodes a generated MP4, so a wheel without FFmpeg fails the
  build instead of every video upload.
