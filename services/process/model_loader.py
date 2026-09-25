"""Fetch model artefacts from S3 and cache them for the life of the container.

The Lambda keeps its execution environment alive between invocations, so module
level state survives a warm call. That is the whole basis of the caching here:
the first invocation pays roughly three minutes to download and deserialise
470 MB, and every warm invocation afterwards pays nothing.

Loading from S3 rather than baking the weights into the image is what lets a
model be swapped by changing one environment variable — no rebuild, no deploy.
Phase 5 will bake version v1 into the image for speed while keeping this path
as the override, so that the capability survives the optimisation.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import torch

from tagger import CLASSES, load_label_map

logger = logging.getLogger(__name__)

ARTEFACTS = ("mdv5a.pt", "model.pt", "labels.txt")

# Keyed by version string, so one warm container can hold v1 and v2 at once —
# which is what makes an A/B comparison possible without a second function.
_CACHE: dict[str, tuple] = {}


def get_models(s3_client, bucket: str, version: str) -> tuple:
    """Return (detector_path, species_model, classes, label_map) for a version.

    Cached after the first call. The detector is returned as a path rather than
    a loaded object because MegaDetector's entry point takes a file path and
    manages its own loading.
    """
    if version in _CACHE:
        logger.info("Model cache hit for version %s", version)
        return _CACHE[version]

    # This line is the evidence that swapping models needs no code change: the
    # S3 prefix it prints comes entirely from an environment variable.
    logger.info("Loading models from s3://%s/%s/", bucket, version)
    started = time.time()

    local_dir = Path("/tmp") / version  # noqa: S108 - Lambda's only writable path
    local_dir.mkdir(parents=True, exist_ok=True)

    for artefact in ARTEFACTS:
        destination = local_dir / artefact
        # A container can be reused after its module state was discarded, so the
        # file may already be on disk even when the cache is empty.
        if destination.exists():
            logger.info("%s already present, skipping download", artefact)
            continue
        logger.info("Downloading s3://%s/%s/%s", bucket, version, artefact)
        s3_client.download_file(bucket, f"{version}/{artefact}", str(destination))

    species_model = torch.load(
        local_dir / "model.pt",
        map_location="cpu",  # Lambda has no GPU
        weights_only=False,  # the checkpoint is a pickled module, not a state dict
    )
    species_model.eval()

    label_map = load_label_map(local_dir / "labels.txt")

    loaded = (str(local_dir / "mdv5a.pt"), species_model, CLASSES, label_map)
    _CACHE[version] = loaded

    logger.info(
        "Loaded version %s in %.1fs (%d classes, %d labels)",
        version,
        time.time() - started,
        len(CLASSES),
        len(label_map),
    )
    return loaded


def cached_versions() -> list[str]:
    """Versions currently held in memory. Used by the handler to report warmth."""
    return sorted(_CACHE)


def artefact_bucket() -> str:
    return os.environ["MODEL_BUCKET"]


def artefact_version() -> str:
    return os.environ.get("MODEL_VERSION", "v1")
