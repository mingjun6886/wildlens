"""species.txt is a copy, so a test keeps it from drifting.

The API artefact is zipped from services/api alone and cannot see ml/labels.txt.
The names must equal what the tagger writes into a record, which is the last
column of labels.txt, lowercased (ml/tagger.py, load_label_map).
"""

from pathlib import Path

HERE = Path(__file__).resolve()
SPECIES = HERE.parents[1] / "species.txt"
LABELS = HERE.parents[3] / "ml" / "labels.txt"


def common_names_in_labels() -> set[str]:
    names = set()
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(";")
        if len(parts) >= 7 and parts[6].strip():
            names.add(parts[6].strip().lower())
    return names


def test_species_txt_matches_labels_txt():
    listed = SPECIES.read_text(encoding="utf-8").splitlines()
    assert listed == sorted(set(listed)), "one name per line, sorted, no duplicates or blanks"
    assert set(listed) == common_names_in_labels()
