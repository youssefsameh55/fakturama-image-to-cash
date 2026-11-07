"""Money arithmetic: Decimal, ROUND_HALF_UP, 2 decimal places."""
from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
HUNDRED = Decimal("100")
TOLERANCE = Decimal("0.01")


def q2(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def line_net(quantity: Decimal, unit_net_price: Decimal, discount_percent: Decimal = Decimal("0")) -> Decimal:
    """qty x unit_net x (1 - discount/100), rounded."""
    return q2(quantity * unit_net_price * (1 - discount_percent / HUNDRED))


def product_gross_price(unit_net_price: Decimal, vat_percent: Decimal) -> Decimal:
    """Gross catalogue price of a product: unit_net x (1 + vat/100). No discount (brief 3.9)."""
    return q2(unit_net_price * (1 + vat_percent / HUNDRED))


def vat_total(lines: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Sum of line_net x vat/100 over (line_net, vat_percent) pairs, rounded once at the end."""
    return q2(sum((net * vat / HUNDRED for net, vat in lines), Decimal("0")))


def gross(net: Decimal, vat: Decimal) -> Decimal:
    return q2(net + vat)


def close(a: Decimal, b: Decimal, tolerance: Decimal = TOLERANCE) -> bool:
    return abs(Decimal(a) - Decimal(b)) <= tolerance


def parse_decimal(text: str | None) -> Decimal | None:
    """First number in a UI/OCR text: '$1,234.50' -> 1234.50, '19.00 %' -> 19.00, '10%' -> 10,
    'VAT 19%' -> 19, '2' -> 2. US-style display (Fakturama shows '$0.00'): ',' = thousands.
    None if there is no number."""
    import re

    if not text:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text.replace(" ", ""))
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except Exception:  # noqa: BLE001
        return None
