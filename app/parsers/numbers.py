"""Price parsing with OCR digit repair.

Scanned price lists carry an embedded OCR text layer whose digits are corrupted:
Cyrillic/Latin look-alikes (О/С→0, I/l→1, З→3, B→8) and space thousands separators
(`10 800`, `12 ООС`, `II 000`). These helpers recover a numeric value and report
whether repair was needed so the pipeline can flag low-confidence rows for review.
"""

from __future__ import annotations

import re

# Look-alike glyphs → digits. Applied only in "aggressive" mode (known price columns),
# where the surrounding context guarantees the cell is meant to be a number.
_LOOKALIKE = {
    "О": "0",
    "о": "0",
    "O": "0",
    "o": "0",
    "Q": "0",
    "Ο": "0",
    "ο": "0",
    "□": "0",
    "С": "0",
    "с": "0",
    "C": "0",
    "c": "0",  # 'С'/'C' read as 0 in numeric runs (ООС→000)
    "I": "1",
    "l": "1",
    "|": "1",
    "і": "1",
    "Ӏ": "1",
    "¡": "1",
    "Ι": "1",
    "З": "3",
    "з": "3",
    "б": "6",
    "B": "8",
    "В": "8",
    "g": "9",
    "q": "9",
    "S": "5",
    "s": "5",
    "Ѕ": "5",
}
_LOOKALIKE_TABLE = str.maketrans(_LOOKALIKE)

# Currency/unit tails to strip before parsing.
_CURRENCY_RE = re.compile(
    r"(тенге|тнг|тг|₸|kzt|руб(?:лей|ля|\.)?|rub|₽|usd|\$|долл(?:ар)?)\.?",
    re.IGNORECASE,
)
_SPACES_RE = re.compile(r"[   \t]")


def detect_currency(text: str) -> str:
    t = (text or "").lower()
    if "$" in t or "usd" in t or "долл" in t:
        return "USD"
    if "руб" in t or "₽" in t or "rub" in t:
        return "RUB"
    return "KZT"


def parse_price(
    text: str | None, aggressive: bool = False
) -> tuple[float | None, bool]:
    """Parse a single price cell.

    Returns ``(value, clean)``. ``clean`` is False when OCR repair / a fallback was
    needed (the caller may flag such rows for review). ``aggressive`` enables
    look-alike→digit substitution; use it for cells already known to be price columns.
    """
    if text is None:
        return None, True
    s = str(text).strip()
    if not s:
        return None, True

    s = _CURRENCY_RE.sub(" ", s)

    if aggressive:
        s = s.translate(_LOOKALIKE_TABLE)

    # Drop everything that is not a digit or separator.
    s = re.sub(r"[^\d.,   \t]", " ", s)
    s = _SPACES_RE.sub("", s).strip()
    if not s:
        return None, False

    # Normalize decimal comma and collapse multiple separators.
    # Treat a trailing ",dd" / ".dd" (<=2 digits) as a decimal fraction; other dots/commas
    # are stray OCR noise inside an integer and get removed.
    m = re.search(r"[.,](\d{1,2})$", s)
    frac = ""
    if m:
        frac = "." + m.group(1)
        s = s[: m.start()]
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None, False

    value = float(digits + frac)
    # Clean iff the source cell held only digits/separators — no look-alike letters
    # had to be repaired into digits.
    clean = not bool(re.search(r"[^\d.,   \t]", _CURRENCY_RE.sub(" ", str(text))))
    return value, clean


_ALLOWED_PRICE_CHARS = set("0123456789.,") | set(_LOOKALIKE.keys())


def price_cell_value(text: str | None) -> float | None:
    """Value of a cell *only if* it is composed entirely of digits / separators / look-alikes.

    Stricter than parse_price: rejects cells holding real letters (e.g. 'до 0,5 см',
    '1 посещение') so service-name fragments are never mistaken for a price column.
    """
    if not text:
        return None
    core = _CURRENCY_RE.sub(" ", str(text)).strip()
    if not core:
        return None
    if any(not ch.isspace() and ch not in _ALLOWED_PRICE_CHARS for ch in core):
        return None
    v, _ = parse_price(text, aggressive=True)
    return v if (v is not None and v > 0) else None
