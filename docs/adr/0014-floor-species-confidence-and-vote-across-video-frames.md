# 14. Floor species confidence, and vote across video frames

**Status:** accepted · after Phase 13 · 2026-11-14

## Context

The species classifier knows 46 classes, mostly Australian, and the tagger took
its argmax with no confidence floor. An animal outside those classes therefore
got the nearest class it did know, presented as an answer. Video made this
obvious (ADR 13): one clip of North American deer was stored as dingo, fallow
deer, cattle and grey shrike-thrush, because the label changed from frame to
frame and MaxN keeps every label it sees.

A floor cannot make the model recognise a deer. What it can do is stop the
model from naming one: keep the count, and say the species is unknown.

## Measurement

Every animal detection (MegaDetector confidence at least 0.05, as in the tagger)
was classified and its top softmax probability recorded:

- **Known correct:** the 26-image answer key plus `cassowary.jpg`, 39 detections
  whose label matched the key. Lowest 0.970, median 1.000.
- **Known wrong:** four deer clips (the 18 s clip as `.mp4`, `.avi` and `.mov`,
  and a 10 s cut), 131 detections. Lowest 0.33, median 0.72.

| Floor | Correct kept | Deer rejected |
|---|---|---|
| 0.70 | 39/39 | 60/131 |
| 0.80 | 39/39 | 76/131 |
| 0.90 | 39/39 | 95/131 |
| 0.95 | 39/39 | 109/131 |

No floor removes the deer alone. At 0.90 the model still names dingo in 4 or 5
frames of every clip with probability above 0.9, and other labels in 2 or 3.
Across the frames that showed an animal, no label at 0.90 reached half of them
in any clip (the highest was dingo, 5 of 19 frames).

## Decision

- **Species floor 0.90** (`tagger.SPECIES_FLOOR`), for images and video alike. A
  detection below it is counted under the tag `unidentified animal`. 0.95 rejects
  more deer, but sits 0.02 from the lowest correct score on 39 samples; 0.90
  leaves 0.07.
- **A frame vote for video** (`video.vote`, before MaxN): a species is kept only
  if it is named in at least half of the frames that show an animal. Others are
  folded, in each frame, into `unidentified animal`. Frames with no animal are
  not counted, so an animal in shot for two seconds of a ten-second clip is not
  voted out.
- **The count survives.** A folded detection was still an animal, so it is
  counted rather than dropped; a clip that shows two animals is stored as
  `{"unidentified animal": 2}` and not as an empty result that reads as "nothing
  there".

## Results

- Answer key: unchanged at precision 0.821, recall 1.000, F1 0.901
  (`ml/eval/reports/v1-species-floor.md`); every correct detection clears the
  floor.
- Deer clips, before and after, on the real models:

| Clip | Before | After |
|---|---|---|
| 18 s `.mp4` | dingo 2, fallow deer 1, cattle 1, grey shrikethrush 1 | unidentified animal 2 |
| 18 s `.avi` | dingo 2, fallow deer 1, grey shrikethrush 1, cattle 1, eastern yellow robin 1, noisy pitta 1 | unidentified animal 3 |
| 18 s `.mov` | dingo 2, fallow deer 1, cattle 1, grey shrikethrush 1 | unidentified animal 2 |
| 10 s `.mp4` | dingo 2, fallow deer 1 | unidentified animal 2 |

The `.avi` count of 3 is a detector over-count in one frame, not a labelling
error; MaxN reports the busiest frame.

## Consequences

- The direction of failure changes. Before, an unknown animal got a wrong name;
  now a known animal that is only clearly visible in under half the frames of a
  clip gets no name. That is the intended trade, and it is untested: there is no
  video of an in-class species to measure it on. The next clip of a species the
  model knows should be used to check it.
- `unidentified animal` is a searchable tag but not a subscribable species: the
  subscribe API accepts only names from `labels.txt`.
- The floor is a property of this model. A retrained or replaced classifier
  needs the measurement repeated before the number is reused.
- Records tagged before this change keep their old tags; nothing is re-tagged.
