"""Text-PDF parser for clinic price lists with an embedded (low-quality) OCR layer.

These PDFs have no ruling lines, so columns are reconstructed geometrically. Two
strategies, chosen per page:

  * column-aligned (preferred): the x-projection profile of the *priced* rows yields
    stable column separators (so the unit and each price tier land in their own column,
    even when a service name wraps across several lines). Tiers are mapped to
    resident / nonresident / extra by reading each price column's header band
    (резидент / нерезидент / зарубежье …).
  * per-row gap split (fallback): when the column profile is unreliable (e.g. price and
    name share a column with no clean gap), each row is split independently by x-gaps and
    the trailing run of numeric cells is taken as the prices. Tiers stay positional.

Rows with no price are section headers or wrapped name fragments (buffered and prepended
to the next priced row). OCR digit corruption is repaired in app/parsers/numbers.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from .base import ParseResult, PriceRow
from .columns import column_bounds
from .numbers import detect_currency, price_cell_value

# Geometry tuning (PDF points).
Y_TOL = 5.0  # words within this vertical distance belong to the same visual row
GAP_FACTOR = (
    1.6  # a cell break is a gap > GAP_FACTOR × median intra-word gap on the row
)
GAP_FLOOR = 7.0
_COL_MIN_GAP = 3  # min empty x-band (px) to call a column separator (data rows only)
_PRICE_DENSITY = (
    0.6  # a column is a price column if ≥ this fraction of its data cells are numeric
)
# RC5 guard thresholds. On header-heavy pages whose price column is truncated in the text
# layer, dozens of price-less rows buffer into `pending_name`; a single stray truncated
# digit (e.g. "2") in a later row's price column then flushes the whole band into one giant
# row whose name spans many real services and whose only "price" is that stray digit.
_BAND_NAME_MAX = (
    120  # chars; far beyond any single service name in the corpus (~165 max,
)
#                       and those always carry a real ≥4-digit price, so are never caught)
_BAND_PRICE_FLOOR = (
    100  # KZT; a sub-100 "price" on a 120+-char name is a truncated digit
)

_CODE_RE = re.compile(r"^[A-ZА-ЯЁ]{1,5}[\d]+(?:[.\-]\d+)*$", re.IGNORECASE)
_ORDINAL_RE = re.compile(r"^\d{1,4}$")  # leading № column to drop from names

# Some lists carry a dedicated service-code column whose codes are NOT leading
# (Клиника 3: name | code | biomaterial | price…), so the code lands mid- or end-of-name
# instead of at token[0] where _CODE_RE looks. Detect such a token anywhere in the name
# region: a DOT-separated code with optional 1–2 leading homoglyph letters ('В02.110.002',
# 'СН.001.113', '10.90.000.000', '80.100'), or a bare code of ≥4 digits ('1151', '10401').
# Uppercase letters only, dot separators only (a dash would catch a week/age range like
# '19-21'), and ≥4 digit chars overall so a decimal quantity ('2.5') or a short count is
# never pulled.
_INLINE_CODE_RE = re.compile(
    r"^(?:[A-ZА-ЯЁ]{0,2}\d+(?:\.\d+)+|[A-ZА-ЯЁ]{1,2}(?:\.\d+)+|\d{4,})$"
)
_CODE_PREFIX_RE = re.compile(r"^[A-ZА-ЯЁ]{1,2}$")  # lone 'В' split off 'В 06.170.005'


def _is_inline_code(tok: str) -> bool:
    return bool(_INLINE_CODE_RE.match(tok)) and sum(c.isdigit() for c in tok) >= 4


def _pull_inline_code(tokens: list[str]) -> tuple[str | None, list[str]]:
    """Find a dedicated service-code token sitting inside the name region and split it
    out, returning (code, remaining_name_tokens). Returns (None, tokens) if none found.

    Takes the first code-shaped token. A lone 1–2 letter prefix split off by an OCR space
    ('В' '06.170.005') is rejoined to the code so it normalizes to the golden 'В06.170.005'.
    The leading № ordinal is handled by the caller before this runs, so it is never
    consumed here. A row carrying TWO+ code tokens is a merged/garbled visual row (two
    source rows collapsed, e.g. concatenated prices) where neither the code nor the price
    is trustworthy — extracting a confident code there would only surface a wrong, unflagged
    price as a silent error, so such rows are left untouched (unmatched, not mis-matched)."""
    idx = [i for i, tok in enumerate(tokens) if _is_inline_code(tok)]
    if len(idx) != 1:
        return None, tokens
    i = idx[0]
    start, code = i, tokens[i]
    if i >= 1 and _CODE_PREFIX_RE.match(tokens[i - 1]):
        start, code = i - 1, tokens[i - 1] + tokens[i]
    return code, tokens[:start] + tokens[i + 1 :]


_SECTION_RE = re.compile(
    r"^(раздел|блок|подраздел|глава|часть|приложение|прейскурант)\b",
    re.IGNORECASE,
)


# Header/footnote noise that must never be treated as a section title.
_SECTION_NOISE = ("ндс", "тенге", "цена", "наимен", "единиц", "руб", "зарубежь")

# Numbered Title-case category header, e.g. '9 Гинекология', '6 Функциональная диагностика',
# '40 Онкология, маммология', '*7 Ультразвуковое исследование'. These carry a small leading
# section number (optionally a '*'/'.' decoration) and NO price/unit. The body must start
# with a LETTER (so a sub-numbered service like '12.1 Хирургия общая' — body '1 Хирургия' —
# does NOT match) and is further screened by _is_category_shape.
_NUM_SECTION_RE = re.compile(
    r"^[\s*.+•·]*\d{1,3}[.)\s]+(?P<body>[^\W\d_].*)$", re.UNICODE
)


def _is_category_shape(body: str) -> bool:
    """True when `body` reads like a bare category label, not a wrapped-name fragment or a
    priced row that merely lost its price column.

    Gates (each rejects a known false positive):
      * lowercase head → a continuation of a wrapped name ('лекарственных средств)').
      * trailing comma → the name keeps going on the next line.
      * > 6 words      → a real (long) service name, never a category.
    A genuine wrapped-name fragment fails the head/comma gate, so it is never stolen from
    `pending_name` (its loss would drop a real service name). A short price-less SERVICE row
    misread as a category is harmless: it is unpriced (so emits no row either way) and only
    clears `pending_name`, which removes — never adds — name pollution downstream."""
    body = body.strip()
    if not body or body[0].islower():
        return False
    if body.endswith(","):
        return False
    return 1 <= len(body.split()) <= 6


def _classify_label(label: str) -> str:
    """Header text → price-tier role flag: 'F' foreign / 'R' resident / 'U' unknown.

    Order matters. The decisive signal is нерезидент vs резидент (checked first, since
    "нерезидент" contains "резидент"). "зарубежье" / "не проживающих" are only a weak
    foreign fallback, checked LAST — both a resident and a nonresident column carry a
    "ближнее/дальнее зарубежье" qualifier that bleeds across x-bands, so they must not
    decide a column that already names (не)резидент."""
    if "нерезидент" in label:
        return "F"
    if "резидент" in label:
        return "R"
    if "иностран" in label or "без граждан" in label:
        return "F"
    if any(
        k in label
        for k in ("граждан республики", "постоянно прожива", "кандас", "оралман")
    ):
        return "R"
    if "зарубеж" in label or "не прожива" in label:
        return "F"
    return "U"


def _is_section(text: str) -> bool:
    """Section header: keyword line, or an ALL-CAPS category label (e.g. 'ГЕМАТОЛОГИЯ')."""
    if not text or len(text) > 80:
        return False
    low = text.lower()
    if any(n in low for n in _SECTION_NOISE):
        return False
    if _SECTION_RE.match(text):
        return True
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 5 and sum(c.isupper() for c in letters) / len(letters) >= 0.75:
        return True
    m = _NUM_SECTION_RE.match(text)
    if m and _is_category_shape(m.group("body")):
        return True
    return False


# Unit-of-measure column vocabulary. On many price lists a short cell sits between the
# service name and the price columns ("1 посещение", "прием", "операция", "кровь с ЭДТА").
# It is the unit, not part of the name — but because the parser only knows "trailing
# numbers = price, everything else = name", that cell bleeds into the name and breaks
# catalogue matching. We treat a cell as a unit only when EVERY word is a known unit term
# (or an inter-word connective), so multi-word service names are never stripped.
_UNIT_WORDS = frozenset(
    {
        "посещение",
        "посещения",
        "прием",
        "приём",
        "приема",
        "приёма",
        "услуга",
        "услуги",
        "услуг",
        "год",
        "года",
        "сутки",
        "день",
        "дня",
        "час",
        "часа",
        "часов",
        "минута",
        "минуты",
        "минут",
        "исследование",
        "исследования",
        "анализ",
        "анализа",
        "операция",
        "операции",
        "процедура",
        "процедуры",
        "манипуляция",
        "манипуляции",
        "консультация",
        "консультации",
        "сеанс",
        "сеанса",
        "пакет",
        "пакета",
        "блок",
        "блока",
        "набор",
        "флакон",
        "ампула",
        "штука",
        "шт",
        "штук",
        "видеозвонок",
        "звонок",
        "койко",
        "койко-день",
        "кусочек",
        "точка",
        "зуб",
        "зуба",
        "снимок",
        "снимка",
        "проба",
        "пробы",
        # laboratory biomaterials (the "ед.изм." column on lab price lists)
        "кровь",
        "крови",
        "моча",
        "мочи",
        "кал",
        "кала",
        "сыворотка",
        "сыв",
        "плазма",
        "плазмы",
        "соскоб",
        "мазок",
        "слюна",
        "слюны",
        "эякулят",
    }
)
# Connectives allowed inside a unit phrase ("кровь с ЭДТА", "соскоб из влагалища").
_UNIT_STOP = frozenset(
    {"с", "со", "из", "у", "в", "во", "по", "и", "на", "до", "за", "о"}
)
_UNIT_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)  # alphabetic tokens only
_COUNT_TOKEN_RE = re.compile(r"^\d{1,3}$")  # leading count in "1 посещение"


def _looks_like_unit(text: str) -> bool:
    """True when a cell is a unit-of-measure label rather than a service name."""
    t = (text or "").strip().lower()
    if not t or len(t) > 40:
        return False
    tokens = [tok for tok in re.split(r"[\s/\\,;]+", t) if tok]
    # A leading count belongs to the unit ("1 посещение"); drop it before the vocab test.
    had_count = bool(tokens and _COUNT_TOKEN_RE.match(tokens[0]))
    if had_count:
        tokens = tokens[1:]
    words = _UNIT_WORD_RE.findall(" ".join(tokens))
    if not words or len(words) > 4:
        return False
    # Vocabulary path: every word is a known unit term or a connective, AND at least one
    # is a real unit word — so a connective-only cell never qualifies (a Cyrillic code
    # like "В02.110" tokenizes to "в", which is a connective, and must not be taken for
    # a unit if it ever sits directly left of the price run).
    if any(w in _UNIT_WORDS for w in words) and all(
        w in _UNIT_WORDS or w in _UNIT_STOP for w in words
    ):
        return True
    # Shape path: "<count> <one-or-two short words>" catches "1 <unit not in vocab>"
    # without risking multi-word service names — which, in this layout, never start with
    # a bare count (the row ordinal is segmented into its own cell).
    return had_count and len(words) <= 2 and all(len(w) <= 14 for w in words)


def _is_band_artifact(
    prices: list[float], pending_name: list[str], left_text: str
) -> bool:
    """RC5: True when an emitted row is really a truncated-page-band merge, not a
    service+price. Such a row has an implausibly long assembled name (a whole band of
    buffered, price-less rows) whose ONLY price is a stray sub-100 digit — the leftover of
    a price truncated in the text layer. Real services keep a ≥4-digit price even when the
    name is long, so legitimate long names are never caught (the price test guards them)."""
    if not prices or max(prices) >= _BAND_PRICE_FLOOR:
        return False
    name_len = sum(len(p) + 1 for p in pending_name) + len(left_text)
    return name_len > _BAND_NAME_MAX


# --------------------------------------------------------------------------- driver


def parse_pdf(path: str | Path, file_format: str = "pdf") -> ParseResult:
    doc = fitz.open(str(path))
    result = ParseResult(file_format=file_format)

    # Analyse every page up front so the tier pattern (which price column is resident /
    # nonresident) can be learned from the page that carries the column headers — usually
    # page 1 — and then applied to every page (later pages rarely repeat the header).
    pages = [_analyze_page(page) for page in doc]
    doc.close()

    pattern: list[str] | None = None
    for _cells_rows, price_cols, rf in pages:
        if price_cols and rf and _confident_roles(rf):
            pattern = _tier_pattern(rf)
            break

    raw_chunks: list[str] = []
    pending_name: list[str] = []  # buffered unpriced text lines (wrapped names)
    section: str | None = None

    for cells_rows, price_cols, _rf in pages:
        for cells in cells_rows:
            raw_chunks.append("  ".join(c[0] for c in cells))
            if price_cols is None:
                # Fallback: per-row gap split, positional tariffs (pipeline maps them).
                prices, pcols, left_text, unit = _split_row(cells)
                resident = nonresident = None
                extra: dict[str, float] | None = None
                resolved = False
            else:
                prices, pcols, left_text, unit = _row_from_columns(cells, price_cols)
                resident = nonresident = None
                extra = None
                resolved = False
                if prices:
                    role_map = _role_map(price_cols, pattern)
                    if role_map is None:
                        # Layout differs from the header page — tiers can't be placed
                        # safely. Keep every value (in extra) but leave resident/nonresident
                        # unset so the pipeline flags the row for review instead of
                        # publishing a guessed, silently-wrong resident price.
                        extra = {f"tier_{c}": v for v, c in zip(prices, pcols)}
                    else:
                        resident, nonresident, extra = _resolve_tiers(
                            prices, pcols, role_map
                        )
                    resolved = True

            if not prices:
                # No price: section header or a wrapped name fragment.
                if left_text and _is_section(left_text):
                    section, pending_name[:] = left_text, []
                elif left_text:
                    pending_name.append(left_text)
                continue

            if _is_band_artifact(prices, pending_name, left_text):
                # Stray truncated digit flushing a whole price-less band: drop the bogus
                # row but still flush the buffer (exactly as the emission below would have),
                # so the next real priced row is not poisoned by the discarded band.
                pending_name[:] = []
                continue

            result.rows.append(
                _build_row(
                    cells,
                    prices,
                    left_text,
                    unit,
                    section,
                    pending_name,
                    resident=resident,
                    nonresident=nonresident,
                    extra_tiers=extra,
                    tariffs_resolved=resolved,
                )
            )

    result.raw_text = "\n".join(raw_chunks)
    if not result.rows:
        result.warnings.append("no priced rows recovered from PDF text layer")
    return result


# --------------------------------------------------------------------- page analysis


def _analyze_page(page):
    """Return (cell_rows, price_cols, rf).

    cell_rows  — list of rows; each row a list of (text, x0, x1) cells.
    price_cols — sorted column indices holding prices (column-aligned grid), or None
                 when the column profile is unreliable and cell_rows is the per-row
                 gap-split fallback (price_cols None ⇒ positional tariffs downstream).
    rf         — per price-column header role flags ('R'/'F'/'U'), or None.
    """
    words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,wordno)
    if not words:
        return [], None, None

    word_rows = _cluster_rows(words)
    fallback = [_segment_cells(r) for r in word_rows]

    # Column separators come from the priced rows only — the garbled, multi-line header
    # band fills inter-column gaps and would otherwise collapse the price columns.
    data_words: list = []
    first_data_top: float | None = None
    for r, cells in zip(word_rows, fallback):
        if _has_trailing_price(cells):
            data_words.extend(r)
            top = min(w[1] for w in r)
            first_data_top = top if first_data_top is None else min(first_data_top, top)
    if len(data_words) < 4:
        return fallback, None, None

    bounds = column_bounds(
        [(w[0], w[2], w[1], w[4]) for w in data_words],
        page.rect.width,
        min_gap=_COL_MIN_GAP,
    )
    if len(bounds) <= 3:  # fewer than 2 interior separators — columns add nothing
        return fallback, None, None

    grid = [_assign_columns(r, bounds) for r in word_rows]
    data_mask = [_has_trailing_price(c) for c in fallback]
    price_cols = _price_columns(grid, len(bounds) - 1, data_mask)
    if not price_cols:
        # No clean trailing numeric column (e.g. price and name merged) — fall back.
        return fallback, None, None

    rf = _header_roles(words, bounds, price_cols, first_data_top)
    return grid, price_cols, rf


def _cluster_rows(words: list) -> list[list]:
    """Cluster words into visual rows by y proximity."""
    words = sorted(words, key=lambda w: (w[1], w[0]))
    rows: list[list] = []
    cur: list = []
    cur_y: float | None = None
    for w in words:
        if cur_y is None or abs(w[1] - cur_y) <= Y_TOL:
            cur.append(w)
            cur_y = w[1] if cur_y is None else (cur_y + w[1]) / 2
        else:
            rows.append(cur)
            cur = [w]
            cur_y = w[1]
    if cur:
        rows.append(cur)
    return rows


def _assign_columns(
    row_words: list, bounds: list[float]
) -> list[tuple[str, float, float]]:
    """Assign a row's words to fixed columns; order within a cell by (line, x) so a name
    wrapped across two lines reads top-to-bottom, left-to-right instead of interleaving."""
    n = len(bounds) - 1
    buckets: list[list[str]] = [[] for _ in range(n)]
    for w in sorted(row_words, key=lambda w: (round(w[1], 1), w[0])):
        center = (w[0] + w[2]) / 2.0
        col = n - 1
        for i in range(n):
            if bounds[i] <= center < bounds[i + 1]:
                col = i
                break
        buckets[col].append(w[4])
    return [
        (" ".join(b).strip(), bounds[i], bounds[i + 1]) for i, b in enumerate(buckets)
    ]


def _price_columns(grid, ncols: int, data_mask: list[bool]) -> list[int]:
    """The maximal run of rightmost columns whose data cells are predominantly numeric.
    Anchored at the right so the leading № ordinal column (also numeric) is excluded."""
    data_rows = [
        grid[i] for i in range(len(grid)) if i < len(data_mask) and data_mask[i]
    ]
    if not data_rows:
        return []

    def density(c: int) -> float:
        vals = [r[c][0] for r in data_rows if c < len(r) and r[c][0]]
        if not vals:
            return 0.0
        return sum(price_cell_value(v) is not None for v in vals) / len(vals)

    cols: list[int] = []
    for c in range(ncols - 1, -1, -1):
        if density(c) >= _PRICE_DENSITY:
            cols.insert(0, c)
        elif cols:
            break  # a non-numeric column ends the trailing price run
    return cols


def _header_roles(words, bounds, price_cols, first_data_top) -> list[str]:
    """Classify each price column from the header text above the first data row:
    'F' foreign / 'R' resident / 'U' unknown — left→right by price column."""
    if first_data_top is None:
        return ["U"] * len(price_cols)
    labels = {c: "" for c in price_cols}
    for w in words:
        if w[1] >= first_data_top:
            continue  # header band only
        center = (w[0] + w[2]) / 2.0
        for c in price_cols:
            if bounds[c] <= center < bounds[c + 1]:
                labels[c] += " " + w[4].lower()
                break
    return [_classify_label(labels[c]) for c in sorted(price_cols)]


# --------------------------------------------------------------------- tier mapping


def _confident_roles(rf: list[str]) -> bool:
    """Only propagate a tier pattern across pages when the header gave a CONFIDENT read:
    a real resident column was found ('R'), or it's a single foreign-only column. Patterns
    inferred without an explicit resident (resident chosen by fallback) are unreliable —
    propagating them and flagging count-mismatch pages would wrongly flag good positional
    rows (e.g. a doc whose data pages are positional-correct). Those degrade to positional."""
    return "R" in rf or (len(rf) == 1 and rf[0] == "F")


def _tier_pattern(rf: list[str]) -> list[str]:
    """Map header role flags (left→right) to roles: resident / nonresident / extra.
    nonresident is the nearest price column to the right of resident (else to the left),
    matching map_tariffs so a 3-tier list keeps the near-foreign tier as nonresident."""
    n = len(rf)
    if n == 1:
        return ["nonresident"] if rf[0] == "F" else ["resident"]
    resident_i = next((i for i, x in enumerate(rf) if x == "R"), None)
    if resident_i is None:
        resident_i = next((i for i, x in enumerate(rf) if x == "U"), None)
    roles = ["extra"] * n
    if resident_i is None:  # every column is foreign
        roles[0] = "nonresident"
        return roles
    roles[resident_i] = "resident"
    right = list(range(resident_i + 1, n))
    left = list(range(resident_i - 1, -1, -1))
    nxt = right or left
    if nxt:
        roles[nxt[0]] = "nonresident"
    return roles


def _role_map(
    price_cols: list[int], pattern: list[str] | None
) -> dict[int, str] | None:
    """Column-index → role for this page.

    * no confident pattern  → positional (first=resident, next=nonresident, rest=extra).
    * confident pattern, column count matches → map by the learned pattern.
    * confident pattern, column count DIFFERS → None: the page's layout doesn't match the
      header page, so we can't place the tiers safely. The caller flags the row for review
      rather than emitting a guessed (silently wrong) resident price."""
    pcs = sorted(price_cols)
    if pattern is None:
        return {
            c: ("resident" if i == 0 else "nonresident" if i == 1 else "extra")
            for i, c in enumerate(pcs)
        }
    if len(pcs) == len(pattern):
        return {pcs[i]: pattern[i] for i in range(len(pcs))}
    return None


def _resolve_tiers(
    prices: list[float], pcols: list[int], role_map: dict[int, str]
) -> tuple[float | None, float | None, dict[str, float] | None]:
    resident = nonresident = None
    extra: dict[str, float] = {}
    for val, col in zip(prices, pcols):
        role = role_map.get(col, "extra")
        if role == "resident" and resident is None:
            resident = val
        elif role == "nonresident" and nonresident is None:
            nonresident = val
        else:
            extra[f"tier_{col}"] = val
    return resident, nonresident, (extra or None)


# --------------------------------------------------------------------- row building


def _has_trailing_price(cells) -> bool:
    return bool(cells) and price_cell_value(cells[-1][0]) is not None


# A price's space-separated thousands group ('22 200') can be split across two adjacent
# price columns when the geometric column model drops a separator inside it: the cells
# become '22' | '200'. A SPLIT head is a bare 1-2 digit integer; its tail is the bare
# 3-digit remainder. A real adjacent tier is a WHOLE price (>= 1000, 4-6 digits) inside one
# cell, so it never shows a 1-2 digit head; a genuine small 3-digit price ('800' | '900')
# has no 1-2 digit head either — so neither is ever merged.
_SPLIT_HEAD_RE = re.compile(r"^\d{1,2}$")
_SPLIT_TAIL_RE = re.compile(r"^\d{3}$")


def _merge_split_thousands(
    cells, prices: list[float], pcols: list[int]
) -> tuple[list[float], list[int]]:
    """Re-join a thousands group split across two adjacent price columns ('22' | '200').

    Merge a column pair (c, c+1) into c — value = head*1000 + tail — only when the columns
    are adjacent, the left cell is a bare 1-2 digit integer and the right cell a bare 3-digit
    integer. This is the only shape a split thousands group can take; whole prices and small
    standalone 3-digit prices fail it, so genuine tiers are left untouched."""
    out_prices: list[float] = []
    out_pcols: list[int] = []
    k = 0
    while k < len(prices):
        if (
            k + 1 < len(prices)
            and pcols[k + 1] == pcols[k] + 1
            and _SPLIT_HEAD_RE.match(cells[pcols[k]][0].strip())
            and _SPLIT_TAIL_RE.match(cells[pcols[k + 1]][0].strip())
        ):
            out_prices.append(prices[k] * 1000 + prices[k + 1])
            out_pcols.append(pcols[k])
            k += 2
        else:
            out_prices.append(prices[k])
            out_pcols.append(pcols[k])
            k += 1
    return out_prices, out_pcols


def _row_from_columns(cells, price_cols: list[int]):
    """Interpret a column-aligned row: collect prices from the known price columns
    (robust to an empty tier mid-row), pull a unit cell, and join the name region."""
    first = min(price_cols)
    prices: list[float] = []
    pcols: list[int] = []
    for c in sorted(price_cols):
        if c < len(cells):
            v = price_cell_value(cells[c][0])
            if v is not None:
                prices.append(v)
                pcols.append(c)
    if not prices:
        text = " ".join(cells[j][0] for j in range(first) if cells[j][0]).strip()
        return [], [], text, None
    prices, pcols = _merge_split_thousands(cells, prices, pcols)
    unit = None
    name_end = first
    if (
        first - 1 >= 1
        and first - 1 < len(cells)
        and _looks_like_unit(cells[first - 1][0])
    ):
        unit = cells[first - 1][0]
        name_end = first - 1
    left_text = " ".join(cells[j][0] for j in range(name_end) if cells[j][0]).strip()
    return prices, pcols, left_text, unit


def _segment_cells(row_words: list) -> list[tuple[str, float, float]]:
    """Split a row's words into cells wherever the horizontal gap is unusually large."""
    row_words = sorted(row_words, key=lambda w: w[0])
    gaps = [row_words[i][0] - row_words[i - 1][2] for i in range(1, len(row_words))]
    positive = [g for g in gaps if g > 0]
    median = sorted(positive)[len(positive) // 2] if positive else 0
    threshold = max(GAP_FLOOR, median * GAP_FACTOR)

    cells: list[tuple[str, float, float]] = []
    chunk = [row_words[0]]
    for prev, w in zip(row_words, row_words[1:]):
        if w[0] - prev[2] > threshold:
            cells.append(_mk_cell(chunk))
            chunk = [w]
        else:
            chunk.append(w)
    cells.append(_mk_cell(chunk))
    return cells


def _mk_cell(words: list) -> tuple[str, float, float]:
    text = " ".join(w[4] for w in words).strip()
    return text, words[0][0], words[-1][2]


def _split_row(cells) -> tuple[list[float], list[int], str, str | None]:
    """Fallback row split: (trailing price values, their cell indices, leading text, unit)."""
    if not cells:
        return [], [], "", None
    prices: list[float] = []
    pcols: list[int] = []
    cut = len(cells)  # index where the trailing numeric run begins
    for i in range(len(cells) - 1, -1, -1):
        v = price_cell_value(cells[i][0])
        if v is None:
            break
        prices.insert(0, v)
        pcols.insert(0, i)
        cut = i
    # The non-price cell directly left of the price run is often a unit column
    # ("1 посещение", "прием", "операция"). Pull it into `unit` so it stops polluting
    # the service name — but only when it positively looks like a unit AND a name cell
    # still remains (cut >= 2), so layouts with no unit column (name abuts the price)
    # are never truncated.
    unit: str | None = None
    name_end = cut
    if prices and cut >= 2 and _looks_like_unit(cells[cut - 1][0]):
        unit = cells[cut - 1][0]
        name_end = cut - 1
    left_text = " ".join(cells[i][0] for i in range(name_end) if cells[i][0]).strip()
    return prices, pcols, left_text, unit


def _build_row(
    cells,
    prices,
    left_text,
    unit,
    section,
    pending_name,
    *,
    resident: float | None = None,
    nonresident: float | None = None,
    extra_tiers: dict[str, float] | None = None,
    tariffs_resolved: bool = False,
) -> PriceRow:
    # Pull a leading service code, or drop a leading № ordinal, from the left text.
    code = None
    tokens = left_text.split()
    if tokens and _CODE_RE.match(tokens[0]):
        code, tokens = tokens[0], tokens[1:]
    elif tokens and _ORDINAL_RE.match(tokens[0]):
        tokens = tokens[1:]  # bare row number, not part of the service name
    # No leading code? A dedicated code column may sit mid/trailing in the name region
    # (Клиника 3: 'Альфа-1-антитрипсин 10.90.000.000'); pull it into .code so the row can
    # be matched by source code instead of leaking the code into the service name.
    if code is None:
        code, tokens = _pull_inline_code(tokens)
    if pending_name:
        # Prepend buffered wrapped-name fragments, then drop any bare row-ordinal still
        # leading the assembled name — a lone "1" line, or a section number left of the
        # name, that no longer sits at the start of `left_text` where it was stripped above.
        tokens = " ".join(pending_name).split() + tokens
        pending_name[:] = []
        while tokens and _ORDINAL_RE.match(tokens[0]):
            tokens = tokens[1:]
    name = " ".join(tokens).strip()
    return PriceRow(
        name=name or "(?)",
        code=code,
        unit=unit,
        section=section,
        prices=prices,
        currency=detect_currency(" ".join(c[0] for c in cells)),
        raw="  ".join(c[0] for c in cells),
        tariffs_resolved=tariffs_resolved,
        resident=resident,
        nonresident=nonresident,
        extra_tiers=extra_tiers,
    )
