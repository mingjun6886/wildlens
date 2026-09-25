"""Score the two-stage tagger against the hand-checked answer key.

Run with the virtualenv active:

    python ml/eval/run_eval.py

Writes a Markdown report to ml/eval/reports/<version>.md so that two model
versions can be compared as numbers rather than as impressions.

Scoring is per individual, not per species. A prediction of three cattle where
the key says four is neither wholly right nor wholly wrong: it caught three and
missed one. Counts matter because the search API treats them as meaningful —
a query for "at least two wombats" excludes a file the tagger undercounted.
"""

from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import torch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tagger  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MODELS = Path(os.environ.get("WILDLENS_MODELS", Path.home() / "Projects/wildlens-models/v1"))
IMAGES = Path(os.environ["WILDLENS_TEST_IMAGES"])
VERSION = os.environ.get("MODEL_VERSION", "v1")


def score_image(
    predicted: dict[str, int], expected: dict[str, int]
) -> dict[str, tuple[int, int, int]]:
    """Compare one image's predicted tags against its expected tags.

    Returns {species: (tp, fp, fn)} covering every species appearing in either
    dict. Iterating the union matters: a species the model invented is as much
    a result as one it missed, and iterating only the key would hide the former
    entirely.

        tp = min(predicted, expected)   individuals correctly found
        fp = max(0, predicted - expected)   over-counted, or species not present
        fn = max(0, expected - predicted)   under-counted, or species missed
    """
    scores: dict[str, tuple[int, int, int]] = {}
    for species in set(predicted) | set(expected):
        found = predicted.get(species, 0)
        actual = expected.get(species, 0)
        scores[species] = (
            min(found, actual),
            max(0, found - actual),
            max(0, actual - found),
        )
    return scores


def aggregate(
    per_image: dict[str, dict[str, tuple[int, int, int]]],
) -> dict[str, tuple[int, int, int]]:
    """Sum per-image (tp, fp, fn) triples into one triple per species."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for scores in per_image.values():
        for species, (tp, fp, fn) in scores.items():
            running = totals[species]
            running[0] += tp
            running[1] += fp
            running[2] += fn
    return {species: tuple(values) for species, values in totals.items()}


def metrics(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """Return (precision, recall, f1).

    A zero denominator scores 0.0 rather than raising or producing NaN. That
    happens legitimately: a species the model never predicts and the key never
    contains has nothing to divide by, and reporting 0.0 keeps the table
    readable without pretending the score means something.
    """
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return precision, recall, f1


def load_models():
    species_model = torch.load(MODELS / "model.pt", map_location="cpu", weights_only=False)
    species_model.eval()
    label_map = tagger.load_label_map(REPO / "ml" / "labels.txt")
    return species_model, label_map


def write_report(totals, per_species, elapsed, image_count, path: Path) -> None:
    tp, fp, fn = totals
    precision, recall, f1 = metrics(tp, fp, fn)
    lines = [
        f"# Evaluation — model {VERSION}",
        "",
        "Scored per individual against `ml/eval/answer_key.yaml`, whose counts",
        "were checked by eye. See that file for the counting rule and for the two",
        "close-range cattle frames that carry more uncertainty than the rest.",
        "",
        f"- Images: {image_count}",
        f"- Individuals in key: {tp + fn}",
        f"- Mean time per image: {elapsed / image_count:.2f}s",
        "",
        "## Overall",
        "",
        "| Precision | Recall | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|",
        f"| {precision:.3f} | {recall:.3f} | {f1:.3f} | {tp} | {fp} | {fn} |",
        "",
        "## Per species",
        "",
        "| Species | Precision | Recall | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for species in sorted(per_species):
        s_tp, s_fp, s_fn = per_species[species]
        p, r, f = metrics(s_tp, s_fp, s_fn)
        lines.append(f"| {species} | {p:.3f} | {r:.3f} | {f:.3f} | {s_tp} | {s_fp} | {s_fn} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    answer_key = yaml.safe_load(
        (REPO / "ml" / "eval" / "answer_key.yaml").read_text(encoding="utf-8")
    )
    image_paths = [str(IMAGES / name) for name in sorted(answer_key)]

    species_model, label_map = load_models()

    started = time.time()
    tagged = tagger.tag_images(
        image_paths, str(MODELS / "mdv5a.pt"), species_model, tagger.CLASSES, label_map
    )
    elapsed = time.time() - started

    per_image = {}
    for path, predicted in tagged.items():
        expected = answer_key[Path(path).name]
        per_image[path] = score_image(predicted, expected)

    per_species = aggregate(per_image)
    if not per_species:
        sys.stderr.write("No species scored; check the answer key and image paths.\n")
        return 1

    totals = tuple(sum(values) for values in zip(*per_species.values(), strict=True))

    report = REPO / "ml" / "eval" / "reports" / f"{VERSION}.md"
    write_report(totals, per_species, elapsed, len(image_paths), report)
    sys.stderr.write(f"Wrote {report.relative_to(REPO)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
