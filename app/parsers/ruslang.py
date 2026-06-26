"""Russian-specific text normalization.

Scanned/OCR'd price lists mix visually-identical Latin glyphs into Cyrillic words
(`Mаммография`, `OAK`, `Cаниторное`) and use `ё`/`е` interchangeably. Folding Latin
homoglyphs back to Cyrillic (only inside tokens that already contain Cyrillic, so
Latin codes like `B02.110` survive) makes search, fuzzy matching and de-duplication
robust regardless of how the OCR rendered a word.
"""

from __future__ import annotations

import re

# Latin → Cyrillic look-alikes (upper + lower). Only unambiguous homoglyphs.
_LAT2CYR = str.maketrans(
    {
        "A": "А",
        "B": "В",
        "E": "Е",
        "K": "К",
        "M": "М",
        "H": "Н",
        "O": "О",
        "P": "Р",
        "C": "С",
        "T": "Т",
        "Y": "У",
        "X": "Х",
        "a": "а",
        "e": "е",
        "o": "о",
        "p": "р",
        "c": "с",
        "y": "у",
        "x": "х",
        "k": "к",
        "m": "м",
        "h": "н",
        "t": "т",
    }
)
_HAS_CYR = re.compile(r"[а-яё]", re.IGNORECASE)
_WS = re.compile(r"\s+")


def fold_homoglyphs(text: str) -> str:
    """Replace Latin look-alikes with Cyrillic, but only within Cyrillic-bearing tokens."""
    if not text:
        return text
    parts = re.split(r"(\s+)", text)
    return "".join(p.translate(_LAT2CYR) if _HAS_CYR.search(p) else p for p in parts)


def normalize_ru(text: str | None) -> str:
    """Canonical form for matching/search: lower-case, ё→е, homoglyphs folded, spaces collapsed."""
    if not text:
        return ""
    s = text.lower().replace("ё", "е")
    s = fold_homoglyphs(s)
    return _WS.sub(" ", s).strip()


def despace(text: str | None) -> str:
    """All whitespace removed + lower-cased — used for keyword matching against OCR'd
    headers where letters are split by stray spaces (`Н аим ен овани е` → `наименование`)."""
    if not text:
        return ""
    return _WS.sub("", text.lower())
