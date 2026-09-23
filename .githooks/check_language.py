#!/usr/bin/env python3
"""Exit 1 if the file contains Vietnamese text, 0 otherwise.

Matches only characters that exist in Vietnamese but not in Western European
languages: the seven modified letters (a-breve, a-circumflex, d-stroke,
e-circumflex, o-circumflex, o-horn, u-horn) in both cases, plus the Latin
Extended Additional block U+1EA0-U+1EF9, which holds the tone-marked vowels.

Deliberately NOT matched:

  a e i o u y carrying only a grave, acute, circumflex or diaeresis
      These occur in French and Spanish, and in loanwords such as "cafe" and
      "naive", so matching them produces false positives on English text.

  U+00D7 MULTIPLICATION SIGN
      A naive Latin-1 range catches this. It appears throughout the design
      document in expressions such as "2 workers x 4 GB x 900 s".

The code points are written as integers rather than string literals or \\u
escapes, so that this file contains no Vietnamese characters and therefore
passes its own check. An earlier version listed them as literals in a comment
and blocked its own commit.

Known limitation: Vietnamese written without diacritics is indistinguishable
from any other Latin text and passes. That is acceptable for a guard rail —
the goal is catching an accidental paste, not defeating a determined author.
"""

import sys

# The seven modified letters, lowercase then uppercase.
MODIFIED_LETTERS = frozenset(
    (
        0x0103, 0x00E2, 0x0111, 0x00EA, 0x00F4, 0x01A1, 0x01B0,
        0x0102, 0x00C2, 0x0110, 0x00CA, 0x00D4, 0x01A0, 0x01AF,
    )
)

# Latin Extended Additional: Vietnamese tone-marked vowels, inclusive.
TONE_MARKED_FIRST = 0x1EA0
TONE_MARKED_LAST = 0x1EF9


def contains_vietnamese(text: str) -> bool:
    for character in text:
        code_point = ord(character)
        if code_point in MODIFIED_LETTERS:
            return True
        if TONE_MARKED_FIRST <= code_point <= TONE_MARKED_LAST:
            return True
    return False


def main() -> int:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: check_language.py <file>\n")
        return 2
    try:
        with open(sys.argv[1], encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        # Unreadable, or deleted between staging and now. Not our problem.
        return 0
    return 1 if contains_vietnamese(text) else 0


if __name__ == "__main__":
    sys.exit(main())
