"""Fetch model artefacts from S3 and cache them for the life of the container.

The Lambda keeps its execution environment alive between invocations, so module
level state survives a warm call. That is the whole basis of the caching here:
the first invocation in an environment pays 8-10 s to fetch and deserialise
470 MB, and every warm invocation afterwards pays nothing. The cost is therefore
per environment, not per request — which is also the reason a cold start gained
little from this cache while a warm call gained 23%.

Loading from S3 rather than baking the weights into the image is what lets a
model be swapped by changing one environment variable — no rebuild, no deploy.
A baked-in version is therefore treated as a *cache tier* rather than a
replacement: a version present on the image filesystem is used directly, and any
other version still comes from S3. The override path stays exercised, so the
swap-without-rebuild capability survives the optimisation.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import torch
from megadetector.detection.run_detector import load_detector

from tagger import CLASSES, load_label_map

logger = logging.getLogger(__name__)

ARTEFACTS = ("mdv5a.pt", "model.pt", "labels.txt")

# Versions baked into the container image, if any. Checked before S3.
BAKED_DIR = Path("/opt/models")

# Holds at most one version, and this limit is load-bearing.
#
# Until Phase 5 the cache held a *path* to the detector weights, so keeping
# several versions cost a few bytes and the comment here advertised being able to
# hold v1 and v2 at once for an A/B comparison. It now holds the *loaded*
# detector: 140M parameters, roughly 560 MB resident. Two versions would add
# about 1.1 GB to a function whose measured peak is already 3000 MB of a 3008 MB
# ceiling, so the second one would not fail gracefully — it would be killed with
# no useful error.
#
# The A/B capability was never used, and could not be: swapping MODEL_VERSION
# updates the function configuration, which discards every warm execution
# environment. Two versions never met in one container even in principle.
_CACHE: dict[str, tuple] = {}


def artefact_dir(s3_client, bucket: str, version: str) -> tuple[Path, str]:
    """Make a version's three files available locally. Returns (directory, source).

    Two tiers. A version baked into the image is already on a read-only layer and
    needs nothing. Any other version is fetched from S3 into /tmp — the path that
    makes a model swap a configuration change rather than a deployment.
    """
    baked = BAKED_DIR / version
    if baked.is_dir():
        logger.info("Using baked-in models at %s", baked)
        return baked, "image"

    # This line is the evidence that swapping models needs no code change: the
    # S3 prefix it prints comes entirely from an environment variable.
    logger.info("Loading models from s3://%s/%s/", bucket, version)
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

    return local_dir, "s3"


def get_models(s3_client, bucket: str, version: str) -> tuple:
    """Return (detector, species_model, classes, label_map) for a version.

    Cached after the first call. Both models are returned loaded. Returning the
    detector as a path, as this did before Phase 5, meant MegaDetector
    deserialised it again on every request — about a second warm and twenty-five
    on the first call after the file reached /tmp.
    """
    if version in _CACHE:
        logger.info("Model cache hit for version %s", version)
        return _CACHE[version]

    started = time.time()

    # Evict before loading, not after. Holding the outgoing detector while the
    # incoming one loads is the one moment two of them would be resident, and at
    # 3000 MB of a 3008 MB ceiling that moment is an out-of-memory kill.
    if _CACHE:
        logger.info("Evicting cached version(s) %s before loading %s", sorted(_CACHE), version)
        _CACHE.clear()

    # The three stages are timed separately because the aggregate cannot answer
    # the question that decides whether to bake weights into the image. Baking
    # removes `fetchMs` and nothing else: the detector is deserialised from a
    # local file either way. A single "model load took 9 seconds" figure looks
    # like a case for baking whatever the split actually is.
    fetch_started = time.time()
    local_dir, source = artefact_dir(s3_client, bucket, version)
    fetch_ms = int((time.time() - fetch_started) * 1000)

    # force_cpu skips a GPU probe that can only ever fail here, and pins the
    # device rather than leaving it to be discovered.
    detector_started = time.time()
    detector = load_detector(str(local_dir / "mdv5a.pt"), force_cpu=True)
    detector_ms = int((time.time() - detector_started) * 1000)

    species_started = time.time()
    species_model = torch.load(
        local_dir / "model.pt",
        map_location="cpu",  # Lambda has no GPU
        weights_only=False,  # the checkpoint is a pickled module, not a state dict
    )
    species_model.eval()
    label_map = load_label_map(local_dir / "labels.txt")
    species_ms = int((time.time() - species_started) * 1000)

    logger.info(
        json.dumps(
            {
                "msg": "model load breakdown",
                "version": version,
                "source": source,
                "fetchMs": fetch_ms,
                "detectorMs": detector_ms,
                "speciesMs": species_ms,
            }
        )
    )

    loaded = (detector, species_model, CLASSES, label_map)
    _CACHE[version] = loaded

    logger.info(
        "Loaded version %s from %s in %.1fs (%d classes, %d labels)",
        version,
        source,
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
