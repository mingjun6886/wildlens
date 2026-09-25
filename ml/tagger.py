"""Two-stage wildlife tagger: MegaDetector locates animals, SpeciesNet names them.

Imported both by the local evaluation harness and, from Phase 3 onward, by the
Lambda handler. It owns no model-loading policy of its own: callers pass in an
already-loaded SpeciesNet model and a path to the MegaDetector weights, so the
Lambda can cache both across warm invocations.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path

import torch
import torchvision.transforms as transforms
from megadetector.detection.run_detector_batch import load_and_run_detector_batch
from PIL import Image

logger = logging.getLogger(__name__)

# The 46 species SpeciesNet was trained on, in the order its output layer emits
# them: the index of a name here is the index of its logit in the model output.
# Never sort this list. Reordering it produces wrong labels with no error at all.
CLASSES = [
    "Alectura_lathami",
    "Antechinus_agilis",
    "Bos_taurus",
    "Burhinus_grallarius",
    "Canis_familiaris",
    "Chalcophaps_longirostris",
    "Colluricincla_harmonica",
    "Corcorax_melanorhamphos",
    "Dacelo_novaeguineae",
    "Dama_dama",
    "Eopsaltria_australis",
    "Felis_catus",
    "Geopelia_humeralis",
    "Gymnorhina_tibicen",
    "Homo_sapiens",
    "Isoodon_macrourus",
    "Lepus_europaeus",
    "Macropus_giganteus",
    "Menura_novaehollandiae",
    "Mus_musculus",
    "Oryctolagus_cuniculus",
    "Perameles_nasuta",
    "Pitta_versicolor",
    "Rattus",
    "Rattus_fuscipes",
    "Rattus_rattus",
    "Strepera_graculina",
    "Sus_scrofa",
    "Tachyglossus_aculeatus",
    "Thylogale_stigmatica",
    "Trichosurus_caninus",
    "Trichosurus_cunninghami",
    "Trichosurus_vulpecula",
    "Varanus_varius",
    "Vombatus_ursinus",
    "Vulpes_vulpes",
    "Wallabia_bicolor",
    "Canis_dingo",
    "Capra_hircus",
    "Casuarius_casuarius",
    "Heteromyias_cinereifrons",
    "Hypsiprymnodon_moschatus",
    "Megapodius_reinwardt",
    "Notamacropus_rufogriseus",
    "Orthonyx_spaldingii",
    "Uromys_caudimaculatus",
]

# MegaDetector emits three categories: 1 animal, 2 person, 3 vehicle.
CATEGORY_ANIMAL = "1"

# A distant animal occupies few pixels, so crops are enlarged before the
# transform runs: upscaling first gives the resize something to work with.
CROP_SIZE = 600
MODEL_INPUT_SIZE = 480

_transform = transforms.Compose(
    [
        transforms.Resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE)),
        transforms.ToTensor(),
    ]
)


def load_label_map(path: str | Path) -> dict[str, str]:
    """Parse labels.txt into {"Sus_scrofa": "wild boar", ...}.

    The file is semicolon-delimited with seven columns:
    uuid; class; order; family; genus; species; common name.
    Only the last three are used.
    """
    mapping: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.strip().split(";")
            if len(parts) < 7:
                continue
            genus, species, common = parts[4], parts[5], parts[6]
            if not common:
                continue
            key = f"{genus.capitalize()}_{species}" if species else genus.capitalize()
            mapping[key] = common.strip().lower()
    return mapping


def _to_tag(scientific_name: str, label_map: dict[str, str]) -> str:
    """Convert a scientific name to the tag stored in DynamoDB.

    Every tag is a lowercase string, because tags are what users search by and a
    search must not depend on capitalisation. Most names resolve through
    labels.txt. A few do not: the genus-only class "Rattus" has no common name
    in the data, since it means "a rat whose species could not be determined".
    Those fall back to the scientific name, lowercased and de-underscored, so
    the output stays uniform.
    """
    common = label_map.get(scientific_name)
    if common:
        return common
    return scientific_name.replace("_", " ").lower()


@torch.no_grad()
def tag_images(
    image_paths: list[str],
    md_model_path: str,
    species_model,
    classes: list[str],
    label_map: dict[str, str],
    conf_thresh: float = 0.05,
) -> dict[str, dict[str, int]]:
    """Tag several images in a single MegaDetector pass.

    Loading MegaDetector costs about thirty seconds; running inference on one
    image costs about two. Passing every path in one call pays the load once
    instead of once per image, which is what makes evaluating a whole test set
    practical rather than a coffee break.

    Returns {image_path: {common_name: count}}. An image containing no animals
    maps to an empty dict, which is a valid result and not an error.
    """
    if not image_paths:
        return {}

    try:
        device = next(species_model.parameters()).device
    except StopIteration:
        device = torch.device("cpu")

    detections_per_image = load_and_run_detector_batch(
        model_file=str(md_model_path),
        image_file_names=[str(path) for path in image_paths],
    )

    results: dict[str, dict[str, int]] = {}

    for record in detections_per_image:
        path = record["file"]
        counter: collections.Counter[str] = collections.Counter()

        try:
            image = Image.open(path).convert("RGB")
        except OSError:
            logger.warning("Could not open %s; recording no tags", path)
            results[path] = {}
            continue

        width, height = image.size

        for detection in record.get("detections", []):
            if detection.get("category") != CATEGORY_ANIMAL:
                continue
            if detection.get("conf", 0.0) < conf_thresh:
                continue

            # MegaDetector returns [x, y, w, h] normalised to the 0..1 range.
            x, y, w, h = detection["bbox"]
            crop = image.crop(
                (
                    int(x * width),
                    int(y * height),
                    int((x + w) * width),
                    int((y + h) * height),
                )
            )
            crop = crop.resize((CROP_SIZE, CROP_SIZE), Image.BILINEAR)

            tensor = _transform(crop).unsqueeze(0)  # 1, C, H, W
            tensor = tensor.permute(0, 2, 3, 1)  # 1, H, W, C — SpeciesNet wants channels last
            tensor = tensor.to(device)

            logits = species_model(tensor)
            probabilities = torch.softmax(logits, dim=1)[0].cpu().numpy()
            scientific_name = classes[int(probabilities.argmax())]
            counter[_to_tag(scientific_name, label_map)] += 1

        results[path] = dict(counter)

    return results


def tag_image(
    image_path: str,
    md_model_path: str,
    species_model,
    classes: list[str],
    label_map: dict[str, str],
    conf_thresh: float = 0.05,
) -> dict[str, int]:
    """Tag a single image. Returns {common_name: count}, empty if nothing found.

    This is the entry point the Lambda handler uses, since an invocation carries
    exactly one file.
    """
    return tag_images(
        [image_path], md_model_path, species_model, classes, label_map, conf_thresh
    ).get(str(image_path), {})
