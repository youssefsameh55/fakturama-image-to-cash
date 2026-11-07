"""Deterministic matching of source entities against table rows (PLAN §8). No LLM, no fuzziness.

Rows are plain dicts (column header -> cell text) as read from Fakturama's tables.
"Exact" means equal after normalisation: Unicode NFKC, trimmed, whitespace collapsed, casefolded.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from i2c.models import Debtor


class Outcome(str, Enum):
    NONE = "none"            # nothing with this key -> create
    EXACT = "exact"          # exactly one row matches every key field -> select it
    AMBIGUOUS = "ambiguous"  # several rows match every key field -> stop
    CONFLICT = "conflict"    # same identity, different definition -> stop


@dataclass
class MatchResult:
    outcome: Outcome
    index: int | None = None          # row index for EXACT
    reason: str = ""
    candidates: list[int] = field(default_factory=list)


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return " ".join(text.split()).casefold()


MIN_VISIBLE_PREFIX = 4
# Trailing run of punctuation/space that contains at least one dot-like mark. Fakturama's '...'
# truncation is OCR'd in many ways ('...', '…', ' . „' - all seen live).
_TRAILING_JUNK = re.compile(r"[\s.,:;…„‚'\"`·_-]*[.…„‚,][\s.,:;…„‚'\"`·_-]*$")


def cell_matches(cell: object, want: object) -> bool:
    """Exact (normalised) equality; a cell Fakturama truncated to fit its column ('Northstar
    Offic...', seen live) matches when `want` starts with the visible part (>= 4 chars).
    Two rows sharing such a prefix both match, which surfaces as AMBIGUOUS -> manual review."""
    c, w = norm(cell), norm(want)
    if c == w:
        return True
    visible = _TRAILING_JUNK.sub("", c).rstrip()
    if visible == c:          # nothing looked like a truncation mark
        return False
    return len(visible) >= MIN_VISIBLE_PREFIX and w.startswith(visible)


def _eq(a: object, b: object) -> bool:
    """a = cell text from a table, b = source value."""
    return cell_matches(a, b)


def match_debtor(rows: list[dict[str, str]], debtor: Debtor) -> MatchResult:
    """Search key: Company. EXACT = Company + First Name + Name + ZIP + City equal (billing).
    Same company with other differing key fields = CONFLICT (a human must decide)."""
    addr = debtor.billing_address
    want = {"Company": debtor.company, "First Name": debtor.first_name, "Name": debtor.last_name,
            "ZIP": addr.zip if addr else None, "City": addr.city if addr else None}
    same_company = [i for i, r in enumerate(rows) if _eq(r.get("Company"), debtor.company)]
    if not same_company:
        return MatchResult(Outcome.NONE, reason=f"no debtor with company {debtor.company!r}")
    exact = [i for i in same_company if all(_eq(rows[i].get(k), v) for k, v in want.items())]
    if len(exact) == 1:
        return MatchResult(Outcome.EXACT, exact[0], "company, contact name, ZIP and city equal", exact)
    if len(exact) > 1:
        return MatchResult(Outcome.AMBIGUOUS, reason=f"{len(exact)} debtors match every key field",
                           candidates=exact)
    diffs = sorted({k for i in same_company for k, v in want.items() if not _eq(rows[i].get(k), v)})
    return MatchResult(Outcome.CONFLICT, reason=f"debtor {debtor.company!r} exists but differs in {diffs}",
                       candidates=same_company)


def match_by_name(rows: list[dict[str, str]], name: str, definition: dict[str, str] | None = None,
                  name_column: str = "Name") -> MatchResult:
    """Generic exact-name match (payment methods, VATs). `definition` = further columns that
    must be equal for reuse; a same-name row that differs there is a CONFLICT."""
    same = [i for i, r in enumerate(rows) if _eq(r.get(name_column), name)]
    if not same:
        return MatchResult(Outcome.NONE, reason=f"no row named {name!r}")
    if len(same) > 1:
        return MatchResult(Outcome.AMBIGUOUS, reason=f"{len(same)} rows named {name!r}", candidates=same)
    row = rows[same[0]]
    diffs = [k for k, v in (definition or {}).items() if not _eq(row.get(k), v)]
    if diffs:
        return MatchResult(Outcome.CONFLICT, reason=f"{name!r} exists but differs in {diffs}: "
                           + ", ".join(f"{k}={row.get(k)!r} (want {definition[k]!r})" for k in diffs),
                           candidates=same)
    return MatchResult(Outcome.EXACT, same[0], f"{name!r} equal", same)


def match_product(rows: list[dict[str, str]], sku: str, description: str | None = None,
                  sku_column: str = "Item No.", description_column: str = "Name") -> MatchResult:
    """EXACT = Item number equal. Duplicate SKU rows -> AMBIGUOUS. A differing description is
    reported in `reason` (the caller decides warn vs stop, settings matching.product_description_mismatch)."""
    same = [i for i, r in enumerate(rows) if _eq(r.get(sku_column), sku)]
    if not same:
        return MatchResult(Outcome.NONE, reason=f"no product with item number {sku!r}")
    if len(same) > 1:
        return MatchResult(Outcome.AMBIGUOUS, reason=f"{len(same)} products with item number {sku!r}",
                           candidates=same)
    reason = "item number equal"
    if description and not _eq(rows[same[0]].get(description_column), description):
        reason += f"; description differs ({rows[same[0]].get(description_column)!r} vs {description!r})"
    return MatchResult(Outcome.EXACT, same[0], reason, same)
