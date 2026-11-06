"""Read Fakturama's NatTable tables (D8): UIA gives the rectangle, OCR gives the words.

Pipeline: OCR words -> group into rows by y -> header row = first row -> column boundaries from
the vertical grid lines drawn in the header strip (fallback: gaps between header words) ->
cells. Row rects come from OCR word boxes, so clicks never use hardcoded coordinates.

Everything except `read_table` is pure and unit-tested offline.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from PIL import Image

from i2c.ui.geom import Rect, Word


@dataclass(frozen=True)
class Row:
    cells: dict[str, str]
    rect: Rect
    words: tuple[Word, ...] = field(repr=False, default=())

    def click_point(self) -> tuple[int, int]:
        """Centre of the row's leftmost OCR word: a point that is certainly inside the row."""
        first = min(self.words, key=lambda w: w.rect.left) if self.words else None
        return first.rect.center if first else self.rect.center

    def get(self, column: str, default: str = "") -> str:
        return self.cells.get(column, default)


@dataclass(frozen=True)
class Table:
    headers: list[str]                       # canonical header per column ("" = unnamed column)
    columns: list[tuple[int, int]]           # screen x-range per column
    rows: list[Row]
    raw_headers: list[str] = field(default_factory=list)

    def signature(self) -> tuple:
        """Comparable content, used to detect when the table stopped changing."""
        return tuple(tuple(sorted(r.cells.items())) for r in self.rows)

    def as_dicts(self) -> list[dict[str, str]]:
        return [dict(r.cells) for r in self.rows]


# ---------------------------------------------------------------- text helpers

_PERCENT_MISREADS = re.compile(r"(?<=\d)\s+(?:0/0|o/o|%o|°/o)(?![\w/])")


def clean_text(text: str) -> str:
    """Collapse whitespace and repair known OCR misreads (e.g. '0.00 %' read as '0.00 0/0')."""
    text = " ".join(text.split())
    return _PERCENT_MISREADS.sub(" %", text)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------- rows

def group_rows(words: list[Word], tolerance: int) -> list[list[Word]]:
    """Group words whose vertical centres lie within `tolerance` px of the row's mean centre."""
    rows: list[list[Word]] = []
    centres: list[float] = []
    for w in sorted(words, key=lambda w: (w.rect.cy, w.rect.left)):
        if rows and abs(w.rect.cy - centres[-1]) <= tolerance:
            rows[-1].append(w)
            centres[-1] = sum(x.rect.cy for x in rows[-1]) / len(rows[-1])
        else:
            rows.append([w])
            centres.append(w.rect.cy)
    return [sorted(r, key=lambda w: w.rect.left) for r in rows]


def _bounding(words: list[Word] | tuple[Word, ...]) -> Rect:
    return Rect(min(w.rect.left for w in words), min(w.rect.top for w in words),
                max(w.rect.right for w in words), max(w.rect.bottom for w in words))


# ---------------------------------------------------------------- columns

def detect_vertical_lines(gray: Image.Image, top: int, bottom: int, min_contrast: int = 6,
                          min_fraction: float = 0.8) -> list[int]:
    """x positions (image coords) of vertical grid lines inside the band [top, bottom).

    A pixel column is a line where, in at least `min_fraction` of the band's rows, it is darker
    than its left neighbour and than the pixel 1 or 2 to its right (lines are 1-2 px wide).
    Text strokes don't qualify because they never span most of the band.
    """
    gray = gray.convert("L")
    top, bottom = max(0, top), min(gray.height, bottom)
    if bottom - top < 3:
        return []
    band = gray.crop((0, top, gray.width, bottom))
    w, h = band.size
    px = band.tobytes()
    hits = [0] * w
    for y in range(h):
        row = px[y * w:(y + 1) * w]
        for x in range(1, w - 1):
            v = row[x] + min_contrast
            if v <= row[x - 1] and (v <= row[x + 1] or (x + 2 < w and v <= row[x + 2])):
                hits[x] += 1
    need = min_fraction * h
    return merge_close([x for x in range(w) if hits[x] >= need])


def merge_close(xs: list[int], gap: int = 4) -> list[int]:
    """Merge runs of nearby x positions (3D separators are drawn as 2-3 lines) into their centre."""
    groups: list[list[int]] = []
    for x in sorted(xs):
        if groups and x - groups[-1][-1] <= gap:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [round(sum(g) / len(g)) for g in groups]


def columns_from_lines(lines: list[int], left: int, right: int, min_width: int = 12) -> list[tuple[int, int]]:
    """Column x-ranges between consecutive grid lines (screen coords), dropping slivers."""
    edges = [left] + [x for x in sorted(lines) if left < x < right] + [right]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b - a >= min_width]


def columns_from_header_words(header: list[Word], left: int, right: int,
                              min_gap: int = 24) -> list[tuple[int, int]]:
    """Fallback when no grid lines are found: header words separated by > min_gap px are
    different columns; boundaries are the midpoints of the gaps."""
    if not header:
        return [(left, right)]
    words = sorted(header, key=lambda w: w.rect.left)
    cuts: list[int] = []
    for a, b in zip(words, words[1:]):
        if b.rect.left - a.rect.right > min_gap:
            cuts.append((a.rect.right + b.rect.left) // 2)
    return columns_from_lines(cuts, left, right, min_width=1)


def _column_of(word: Word, columns: list[tuple[int, int]]) -> int | None:
    cx = word.rect.cx
    for i, (a, b) in enumerate(columns):
        if a <= cx < b:
            return i
    return None


def cells_for(words: list[Word], columns: list[tuple[int, int]]) -> list[str]:
    parts: list[list[str]] = [[] for _ in columns]
    for w in sorted(words, key=lambda w: w.rect.left):
        i = _column_of(w, columns)
        if i is not None:
            parts[i].append(w.text)
    return [clean_text(" ".join(p)) for p in parts]


# ---------------------------------------------------------------- headers

def match_headers(raw: list[str], expected: list[str] | None, min_ratio: float = 0.6) -> list[str]:
    """Map OCR'd header texts to canonical names.

    Exact (normalised) matches first, then greedy best fuzzy ratio, so 'Com an' -> 'Company'
    while 'Name' never steals 'First Name'. Unmatched columns keep their cleaned OCR text.
    """
    result = [clean_text(r) for r in raw]
    if not expected:
        return result
    remaining = list(expected)
    assigned: dict[int, str] = {}
    for i, r in enumerate(raw):
        for e in remaining:
            if _norm(r) and _norm(r) == _norm(e):
                assigned[i] = e
                remaining.remove(e)
                break
    scored = sorted(
        ((difflib.SequenceMatcher(None, _norm(r), _norm(e)).ratio(), i, e)
         for i, r in enumerate(raw) if i not in assigned and _norm(r)
         for e in remaining),
        reverse=True)
    for ratio, i, e in scored:
        if ratio < min_ratio:
            break
        if i not in assigned and e in remaining:
            assigned[i] = e
            remaining.remove(e)
    return [assigned.get(i, result[i]) for i in range(len(raw))]


# ---------------------------------------------------------------- assembly

def build_table(words: list[Word], table_rect: Rect, lines: list[int] | None,
                expected_headers: list[str] | None = None, row_tolerance: int = 8) -> Table:
    """Assemble a Table from OCR words (screen coords) and grid lines (screen x positions).

    The first row of words is the header row. `lines=None`/empty -> header-gap fallback.
    """
    words = [w for w in words if w.text.strip()]
    rows = group_rows(words, row_tolerance)
    if not rows:
        return Table([], [], [], [])
    header_words, data_rows = rows[0], rows[1:]
    columns = (columns_from_lines(lines, table_rect.left, table_rect.right) if lines
               else columns_from_header_words(header_words, table_rect.left, table_rect.right))
    raw = cells_for(header_words, columns)
    headers = match_headers(raw, expected_headers)
    keys = [h if h else f"col{i}" for i, h in enumerate(headers)]

    out: list[Row] = []
    for rw in data_rows:
        values = cells_for(rw, columns)
        box = _bounding(rw)
        out.append(Row(dict(zip(keys, values)),
                       Rect(table_rect.left, box.top, table_rect.right, box.bottom), tuple(rw)))
    return Table(headers, columns, out, raw)


def header_band(words: list[Word], row_tolerance: int, pad: int = 6) -> tuple[int, int] | None:
    """(top, bottom) in screen coords of the header row, padded so grid lines dominate it."""
    rows = group_rows([w for w in words if w.text.strip()], row_tolerance)
    if not rows:
        return None
    box = _bounding(rows[0])
    return box.top - pad, box.bottom + pad


def read_table(rect: Rect, expected_headers: list[str] | None = None, *, scale: float = 1.0,
               language: str = "en-US", row_tolerance: int = 8) -> tuple[Table, Image.Image]:
    """Live: capture `rect` (from UIA), OCR it and assemble the table. Returns (table, image)."""
    from i2c.ui.ocr import ocr_rect

    words, img = ocr_rect(rect, scale, language)
    lines: list[int] = []
    band = header_band(words, row_tolerance)
    if band:
        img_lines = detect_vertical_lines(img, band[0] - rect.top, band[1] - rect.top)
        lines = [rect.left + x for x in img_lines]
    return build_table(words, rect, lines, expected_headers, row_tolerance), img
