"""Unit tests for the tagger, with both models faked.

The real weights are 470 MB and take thirty seconds to load, so no test here
touches them. What is worth testing is not whether MegaDetector detects animals
— that is the model's job, measured separately by ml/eval/run_eval.py against
the answer key — but the logic wrapped around it: which detections are kept,
which are discarded, how counts accumulate, and how scientific names become the
tags users search by.

That split is deliberate. These tests must stay fast enough to run on every
commit; accuracy measurement is a separate, slower job.
"""

from pathlib import Path

import pytest
import torch
from PIL import Image

import tagger


class FakeSpeciesModel:
    """Stands in for SpeciesNet, always predicting one chosen class.

    tag_images calls next(model.parameters()) to discover which device to move
    tensors to. Returning an empty iterator raises StopIteration, which the
    caller catches and falls back to CPU — exercising that path as a side
    effect.
    """

    def __init__(self, class_index: int):
        self.class_index = class_index
        self.calls = 0

    def parameters(self):
        return iter(())

    def __call__(self, _tensor):
        self.calls += 1
        logits = torch.zeros(1, len(tagger.CLASSES))
        logits[0, self.class_index] = 10.0
        return logits


@pytest.fixture
def image_file(tmp_path) -> str:
    path = tmp_path / "frame.jpg"
    Image.new("RGB", (200, 150), color=(120, 120, 120)).save(path)
    return str(path)


@pytest.fixture
def label_map() -> dict[str, str]:
    return {"Sus_scrofa": "wild boar", "Felis_catus": "domestic cat"}


def animal(conf: float = 0.9, bbox=None) -> dict:
    return {"category": "1", "conf": conf, "bbox": bbox or [0.1, 0.1, 0.4, 0.4]}


def detections(path: str, items: list[dict]) -> list[dict]:
    return [{"file": path, "detections": items}]


def patch_detector(monkeypatch, records):
    monkeypatch.setattr(tagger, "load_and_run_detector_batch", lambda **_: records)


# --- label parsing ---------------------------------------------------------


def test_load_label_map_reads_genus_species_and_common_name(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text("uuid;mammalia;artiodactyla;suidae;sus;scrofa;wild boar\n", encoding="utf-8")
    assert tagger.load_label_map(path) == {"Sus_scrofa": "wild boar"}


def test_load_label_map_skips_rows_with_no_common_name(tmp_path):
    """The genus-only Rattus row has empty species and common-name columns."""
    path = tmp_path / "labels.txt"
    path.write_text("uuid;mammalia;rodentia;muridae;rattus;;\n", encoding="utf-8")
    assert tagger.load_label_map(path) == {}


def test_load_label_map_skips_malformed_rows(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text("too;few;columns\n", encoding="utf-8")
    assert tagger.load_label_map(path) == {}


def test_real_label_file_covers_every_class_but_the_genus_only_one():
    """Guards the assumption the pipeline rests on: names resolve to tags.

    Rattus is the sole exception and is handled by the fallback in _to_tag.
    """
    mapping = tagger.load_label_map(Path(__file__).resolve().parents[1] / "labels.txt")
    unmapped = [name for name in tagger.CLASSES if name not in mapping]
    assert unmapped == ["Rattus"]


# --- tag normalisation -----------------------------------------------------


def test_to_tag_prefers_the_common_name(label_map):
    assert tagger._to_tag("Sus_scrofa", label_map) == "wild boar"


def test_to_tag_falls_back_to_a_lowercased_scientific_name(label_map):
    """Tags are what users search by, so every tag is lowercase regardless."""
    assert tagger._to_tag("Rattus", label_map) == "rattus"
    assert tagger._to_tag("Canis_lupus", label_map) == "canis lupus"


# --- detection filtering ---------------------------------------------------


def test_counts_one_tag_per_animal_detection(monkeypatch, image_file, label_map):
    boar = tagger.CLASSES.index("Sus_scrofa")
    patch_detector(monkeypatch, detections(image_file, [animal(), animal(), animal()]))

    result = tagger.tag_images(
        [image_file], "md.pt", FakeSpeciesModel(boar), tagger.CLASSES, label_map
    )

    assert result == {image_file: {"wild boar": 3}}


def test_ignores_people_and_vehicles(monkeypatch, image_file, label_map):
    """MegaDetector category 2 is a person and 3 a vehicle; neither is wildlife."""
    boar = tagger.CLASSES.index("Sus_scrofa")
    model = FakeSpeciesModel(boar)
    patch_detector(
        monkeypatch,
        detections(
            image_file,
            [
                animal(),
                {"category": "2", "conf": 0.99, "bbox": [0.1, 0.1, 0.2, 0.2]},
                {"category": "3", "conf": 0.99, "bbox": [0.5, 0.5, 0.2, 0.2]},
            ],
        ),
    )

    result = tagger.tag_images([image_file], "md.pt", model, tagger.CLASSES, label_map)

    assert result == {image_file: {"wild boar": 1}}
    assert model.calls == 1, "the classifier should only see the animal crop"


def test_ignores_detections_below_the_confidence_threshold(monkeypatch, image_file, label_map):
    boar = tagger.CLASSES.index("Sus_scrofa")
    patch_detector(monkeypatch, detections(image_file, [animal(conf=0.9), animal(conf=0.01)]))

    result = tagger.tag_images(
        [image_file], "md.pt", FakeSpeciesModel(boar), tagger.CLASSES, label_map
    )

    assert result == {image_file: {"wild boar": 1}}


def test_threshold_is_configurable(monkeypatch, image_file, label_map):
    boar = tagger.CLASSES.index("Sus_scrofa")
    patch_detector(monkeypatch, detections(image_file, [animal(conf=0.2)]))

    strict = tagger.tag_images(
        [image_file],
        "md.pt",
        FakeSpeciesModel(boar),
        tagger.CLASSES,
        label_map,
        conf_thresh=0.5,
    )
    lenient = tagger.tag_images(
        [image_file],
        "md.pt",
        FakeSpeciesModel(boar),
        tagger.CLASSES,
        label_map,
        conf_thresh=0.1,
    )

    assert strict == {image_file: {}}
    assert lenient == {image_file: {"wild boar": 1}}


# --- edge cases ------------------------------------------------------------


def test_an_image_with_no_detections_scores_an_empty_dict(monkeypatch, image_file, label_map):
    """Empty is a valid result, not an error: the frame held no animals."""
    patch_detector(monkeypatch, detections(image_file, []))

    result = tagger.tag_images(
        [image_file], "md.pt", FakeSpeciesModel(0), tagger.CLASSES, label_map
    )

    assert result == {image_file: {}}


def test_no_input_means_no_detector_run(monkeypatch, label_map):
    def explode(**_):
        raise AssertionError("the detector must not be loaded for an empty batch")

    monkeypatch.setattr(tagger, "load_and_run_detector_batch", explode)

    assert tagger.tag_images([], "md.pt", FakeSpeciesModel(0), tagger.CLASSES, label_map) == {}


def test_an_unreadable_file_yields_no_tags_rather_than_crashing(monkeypatch, tmp_path, label_map):
    """One corrupt file in a batch must not cost the other twenty-five."""
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    patch_detector(monkeypatch, detections(str(broken), [animal()]))

    result = tagger.tag_images(
        [str(broken)], "md.pt", FakeSpeciesModel(0), tagger.CLASSES, label_map
    )

    assert result == {str(broken): {}}


def test_an_unmapped_prediction_still_produces_a_tag(monkeypatch, image_file):
    """With an empty label map every name falls through to the lowercase form."""
    boar = tagger.CLASSES.index("Sus_scrofa")
    patch_detector(monkeypatch, detections(image_file, [animal()]))

    result = tagger.tag_images([image_file], "md.pt", FakeSpeciesModel(boar), tagger.CLASSES, {})

    assert result == {image_file: {"sus scrofa": 1}}


# --- the single-image wrapper ----------------------------------------------


def test_tag_image_unwraps_the_batch_result(monkeypatch, image_file, label_map):
    cat = tagger.CLASSES.index("Felis_catus")
    patch_detector(monkeypatch, detections(image_file, [animal(), animal()]))

    assert tagger.tag_image(
        image_file, "md.pt", FakeSpeciesModel(cat), tagger.CLASSES, label_map
    ) == {"domestic cat": 2}
