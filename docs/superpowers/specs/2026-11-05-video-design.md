# Phase 12 — Video support

**Date:** 2026-11-05
**Status:** Approved in conversation; awaiting review of this document
**Scope:** `services/process/` (new `video.py`, a video branch in `app.py`, the Dockerfile), `services/api/` (`upload.py`, `search.py`, `status.py`), `web/`, documentation. No Terraform change is expected.

---

## 1. Goal

A signed-in user uploads a short camera-trap video through the web client and, a few minutes later, sees it in search results with species tags, a thumbnail and the number of frames examined. The video's tags trigger the same email notifications an image's do.

Success means three real files, one each of MP4, MOV and AVI, uploaded through the deployed site, reach `DONE` with sensible tags; a corrupt video reaches `FAILED` without being retried; and every existing image behaviour is unchanged.

### Decisions already taken

| # | Decision | Reason |
|---|---|---|
| D1 | Counts are **MaxN**: for each species, the largest count seen in any single frame | The design document said "sum the per-species counts". Summing counts the same animal once per second, so one wallaby standing still for a minute becomes 60 wallabies. MaxN is the standard camera-trap measure and never exceeds what was visibly present at once |
| D2 | Video limit **100 MB**; the image limit stays **25 MB** | A minute of 1080p phone or trap video is 50 to 130 MB. 100 MB bounds what one request can cost |
| D3 | Accepted containers: **MP4, MOV, AVI** | The formats camera traps actually write. Each is tested with a real file |
| D4 | Sample **one frame per second, at most 60 frames** (the first minute) | As the design document fixed. 60 frames at about 7 s each fits in the 900 s timeout with a margin |
| D5 | Video is processed by the **existing `process` function**, on the existing queue | Reuses idempotency, the state machine, the dead-letter queue, notifications, metrics and tracing. Rejected: a separate function and queue (doubles the infrastructure and the cold starts) and Step Functions fan-out (each branch cold-starts and loads the model; not worth it at this scale) |
| D6 | A **time guard** stops tagging when less than 60 s of the invocation remain, and records what was done | Without it a slow video times out, is retried three times and ends in the dead-letter queue with nothing recorded |
| D7 | The thumbnail comes from the **busiest frame** (most animals; the earliest wins a tie; the first frame if none has an animal) | The design document said the first frame. The first second of a trap clip is often empty, because the animal is what triggered the camera |
| D8 | `/search/byfile` stays **image only** | Tagging even a short video takes minutes, far beyond the 29 s API Gateway ceiling (ADR 5) |
| D9 | No in-page video player. The card links to the original, which opens in a new tab | Browsers play MP4 and usually MOV natively; AVI downloads. An in-page `<video>` would also need a `media-src` change to the page CSP |

### Out of scope

Search by an example video; in-page playback; tagging past the first minute; parallel per-frame processing; per-frame results in the record; transcoding.

---

## 2. Components

| Unit | Purpose | Depends on |
|---|---|---|
| `services/process/video.py` (new, flat module) | Pure functions: sample frames from a video file, combine per-frame tags with MaxN, pick the thumbnail frame | `cv2` only. Tested without models or AWS |
| `services/process/app.py` | Routes `.mp4/.mov/.avi` to a video branch; tags frames one at a time under the time guard; records the result | `video.py`, `tagger.tag_image`, `media_utils.make_thumbnail` |
| `services/process/Dockerfile` | One new `COPY` line for `video.py`; the build-time check also decodes a small generated video | — |
| `services/api/upload.py` | Accepts the three video extensions with their content types; size limit per kind | — |
| `services/api/search.py` | Returns `type`, `frameCount` and `durationSec`; `/search/byfile` refuses video | — |
| `services/api/status.py` | Returns `frameCount` and `durationSec` | — |
| `web/` | File picker accepts video; size check per kind before hashing; a "Video · N frames" label on result cards; a longer wait for video | — |

`pyproject.toml`'s `known-first-party` gains `video`.

### 2.1 `video.py` interface

```python
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi"}
MAX_FRAMES = 60
FALLBACK_FPS = 30.0

@dataclass(frozen=True)
class SampledVideo:
    frames: list[Path]           # JPEG files in out_dir, in time order
    duration_sec: float | None   # from the container's metadata; None if it reports none
    fps_assumed: bool            # True if the container reported no usable frame rate

class UnreadableVideo(Exception): ...

def sample_frames(path: Path, out_dir: Path, max_frames: int = MAX_FRAMES) -> SampledVideo
def max_n(per_frame: list[dict[str, int]]) -> dict[str, int]
def busiest_frame(per_frame: list[dict[str, int]]) -> int   # index into per_frame
```

- `sample_frames` reads the file sequentially with `cv2.VideoCapture`, keeping one frame every `round(fps)` frames (`grab()` for the skipped ones, `retrieve()` for the kept), and writes each kept frame as a JPEG. Sequential reading, not seeking, because seeking is inaccurate in some AVI and MOV files.
- A frame rate of 0, negative or NaN uses `FALLBACK_FPS` and sets `fps_assumed`.
- It raises `UnreadableVideo` if the capture does not open or yields no frame at all. A read that fails part-way through ends sampling; the frames already kept are returned.
- `max_n([])` is `{}`. `busiest_frame` requires a non-empty list.

---

## 3. Data flow

1. The upload function reserves the file as today. The object key is `<sha256>.<ext>` and the presigned PUT binds the video content type.
2. `process_object` routes on the suffix: image suffixes take the existing path unchanged; video suffixes take the video branch; anything else is still a permanent failure.
3. Shared with images: download, hash, compare the digest with the key, `claim()` the record.
4. `sample_frames` writes up to 60 JPEGs to `/tmp`. `UnreadableVideo` becomes `PermanentFailure("not a readable video: ...")`.
5. Models are loaded once (`get_models`, inside the `model-load` span, as today).
6. Frames are tagged **one at a time** with `tag_image`, each inside a `tagging` span. Before each frame, if `context.get_remaining_time_in_millis()` is below 60 000, tagging stops and a `video truncated` log event is written with the counts. `tag_images` is not used for the batch because it detects every frame before classifying any, which the guard cannot interrupt.
7. If no frame was tagged (the guard fired before the first), the file is a permanent failure: "ran out of time before tagging any frame".
8. `tags = max_n(per_frame)`; the thumbnail is made from `frames[busiest_frame(per_frame)]` and stored at `<fileId>.jpg`, not `<fileId>.mp4`.
9. `record_success` writes `type="video"`, `frameCount` (frames actually tagged), `durationSec` when known, and every field an image record has.
10. Metrics, then publish, in the same order as images. `InferenceDuration` is the **mean per frame**, so the metric keeps meaning "time to tag one image" and the dashboard stays comparable; `TagsPerFile` is the MaxN total. `ModelLoadDuration` and `ColdStartDuration` are unchanged.
11. The frame directory is removed in `finally`, whatever happened.

The `context` is passed from `handler` into `process_object`; the image path ignores it.

---

## 4. Error handling

| Situation | Outcome |
|---|---|
| OpenCV cannot open the file, or it yields no frame | `FAILED`, "not a readable video", no retry |
| A read fails part-way through | Sampling stops; the frames read so far are tagged |
| No usable frame rate | 30 fps assumed, logged with `fpsAssumed`; not an error |
| Less than 60 s left before a frame | Partial result recorded, `video truncated` logged, `frameCount` is the frames tagged |
| Time ran out before the first frame | `FAILED`, no retry (a retry would meet the same cold start) |
| S3, DynamoDB or model-bucket error | Raised, retried by SQS, as for images |
| An image renamed to `.mp4` | OpenCV cannot read it as video: `FAILED` |
| `.mp4` sent to `/search/byfile` | 400, "search by file accepts images only" |

---

## 5. API and web

### 5.1 Upload

`ALLOWED_EXTENSIONS` gains `mp4: video/mp4`, `mov: video/quicktime`, `avi: video/x-msvideo`. The size limit depends on the kind: 25 MB for images, 100 MB for video, and the 400 names the limit that applied. The 15-minute presigned URL lifetime is unchanged: 100 MB at 1 MB/s takes under two minutes.

### 5.2 Reads

- `search.py`'s projection adds `#t` (an alias, because `type` is a DynamoDB reserved word, as `#s` is for `status`), `frameCount` and `durationSec`; results carry them when present.
- `status.py`'s public fields add `frameCount` and `durationSec`.

### 5.3 Web client

- The picker accepts `.mp4,.mov,.avi` as well; a line under it states the limits (images 25 MB, video 100 MB, only the first minute is examined).
- A pure `checkSize(file)` in `web/src/` rejects an oversized file **before** hashing, so the browser does not spend time hashing a file the API will refuse. It is tested with `node --test`.
- Polling for a video waits up to **16 minutes** (900 s timeout plus cold start and queue time) at a **10 s** interval; images keep 2 minutes at 2 s. The progress message for video says it may take several minutes.
- A result card for a video shows the thumbnail and a "Video · N frames" label; the link opens the original in a new tab.
- No CSP change.

---

## 6. Infrastructure

No Terraform change is expected:

- The S3 notification already sends every `ObjectCreated` event to the queue, with no suffix filter.
- The 900 s timeout, 4 GB of `/tmp` and the 5400 s visibility timeout suffice. A 100 MB video and 60 JPEG frames use under 150 MB.
- IAM, alarms and the concurrency cap of 2 are unchanged. The queue-age alarm (1200 s) still exceeds the longest legitimate run.
- The image uses `opencv-python-headless`, whose wheel bundles FFmpeg. The Dockerfile's verification step gains a decode of a small generated MP4, so a build without video support fails at build time rather than in Lambda.

**Cost:** about 7 minutes at 3 GB, roughly US$0.02 per video, against about US$0.0005 per image.

**Known trade-off:** a video holds one of the two concurrent executions for several minutes, so images uploaded behind two videos wait. Accepted at this scale (D5).

---

## 7. Testing

### 7.1 Automated (no AWS)

- `services/process/tests/test_video.py` generates small videos inside the test with `cv2.VideoWriter`, so no sample file is committed:
  - a 5-second video at 10 fps yields 5 frames;
  - a 90-second video yields 60 frames;
  - a container reporting 0 fps still yields frames and sets `fps_assumed`;
  - a file of random bytes raises `UnreadableVideo`;
  - `max_n` of several frames, of no frames, of frames with disjoint species;
  - `busiest_frame` picks the busiest, the earliest on a tie, and frame 0 when all are empty.
- `services/process/tests/test_app_video.py`, with mocked models:
  - the record has `type: "video"`, `frameCount`, `durationSec`, a `.jpg` thumbnail key, and MaxN tags;
  - a publish happens with the MaxN tags;
  - a corrupt video is recorded `FAILED` and not raised as transient;
  - the time guard stops tagging and records a partial `frameCount`;
  - the frame directory is gone afterwards, on success and on failure;
  - the image path is unchanged (existing tests stay green).
- API tests: upload accepts each video extension with its content type; 100 MB of video is accepted and 101 MB refused; a 26 MB image is still refused; search results include `type` and `frameCount`; `/search/byfile` refuses a video extension.
- Web test: `checkSize` per kind and at the boundaries.
- `test_packaging.py` already fails if `video.py` lacks its `COPY` line.

### 7.2 On AWS (after the owner approves the build and apply)

1. Upload one real MP4, MOV and AVI through the site; each reaches `DONE` with sensible tags.
2. Measure the per-frame and total time. If a 60-frame video exceeds about 10 minutes, report before going further.
3. Check the X-Ray trace shows the per-frame `tagging` subsegments, and that a notification email arrives for a matching subscription.

---

## 8. Documentation

- **ADR 13:** video in the existing function, MaxN, 1 fps capped at 60 frames with a time guard, the busiest frame as thumbnail. Row added to `docs/adr/README.md`.
- **Runbook:** "A video is FAILED or has a partial result", with the log queries for `video truncated` and `not a readable video`.
- **Design document §6.3:** a note that MaxN and the busiest-frame thumbnail supersede "sum" and "first frame", pointing at ADR 13. The original text is kept, per the repository's convention.
- **CLAUDE.md:** the video branch, `video.py`, and the per-kind upload limit.
