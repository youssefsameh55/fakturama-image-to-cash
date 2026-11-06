"""Deterministic checks on extracted OrderData (PLAN.md section 5). No LLM involved."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import ValidationError

from i2c import calc
from i2c.models import OrderData, PaymentStatus

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Severity = "error"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def warnings(issues: Iterable[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "warning"]


def schema_issues(exc: ValidationError) -> list[Issue]:
    """Pydantic parse errors -> Issues, so they can be fed back on the retry."""
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        out.append(Issue("schema", f"{loc}: {err['msg']} (got {err.get('input')!r})"))
    return out


def validate(order: OrderData,
             known_payment_methods: Iterable[str] = (),
             expected_currency: str = "EUR",
             tolerance: Decimal = calc.TOLERANCE) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, msg: str) -> None:
        issues.append(Issue(code, msg, "error"))

    def warn(code: str, msg: str) -> None:
        issues.append(Issue(code, msg, "warning"))

    # --- required fields -------------------------------------------------------------
    if not order.external_reference:
        err("missing_reference", "external_reference is required")
    if order.order_date is None:
        err("missing_order_date", "order_date is required (YYYY-MM-DD)")
    if not order.debtor.company:
        err("missing_company", "debtor.company is required")
    ba = order.debtor.billing_address
    missing_ba = [f for f in ("street", "zip", "city", "country") if ba is None or not getattr(ba, f)]
    if missing_ba:
        err("missing_billing_address", f"billing_address is missing: {', '.join(missing_ba)}")
    if not order.items:
        err("no_items", "at least one item is required")
    for t in ("net", "vat", "gross"):
        if getattr(order.totals, t) is None:
            err("missing_total", f"totals.{t} is required")

    # --- items -----------------------------------------------------------------------
    computed_lines: list[tuple[Decimal, Decimal]] = []
    items_complete = True
    for idx, item in enumerate(order.items, start=1):
        label = f"item {item.position or idx} ({item.sku or '?'})"
        missing = [f for f in ("sku", "quantity", "unit_net_price", "vat_percent") if getattr(item, f) in (None, "")]
        if missing:
            err("missing_item_field", f"{label}: missing {', '.join(missing)}")
            items_complete = False
            continue
        if item.quantity <= 0:
            err("bad_quantity", f"{label}: quantity must be > 0, got {item.quantity}")
        for f in ("vat_percent", "discount_percent"):
            v = getattr(item, f)
            if not (Decimal("0") <= v <= Decimal("100")):
                err("bad_percent", f"{label}: {f} must be within 0..100, got {v}")
        if item.unit_net_price < 0:
            err("bad_price", f"{label}: unit_net_price must be >= 0, got {item.unit_net_price}")

        expected = calc.line_net(item.quantity, item.unit_net_price, item.discount_percent)
        if item.line_net_total is not None and not calc.close(expected, item.line_net_total, tolerance):
            err("line_net_mismatch",
                f"{label}: {item.quantity} x {item.unit_net_price} - {item.discount_percent}% = {expected}, "
                f"but line_net_total is {item.line_net_total}")
        computed_lines.append((expected, item.vat_percent))

    # --- totals ----------------------------------------------------------------------
    t = order.totals
    if items_complete and computed_lines:
        net = sum((n for n, _ in computed_lines), Decimal("0"))
        if t.net is not None and not calc.close(net, t.net, tolerance):
            err("net_total_mismatch", f"sum of line nets is {net}, but totals.net is {t.net}")
        vat = calc.vat_total(computed_lines)
        vat_tol = tolerance * len(computed_lines)  # Fakturama may round per line
        if t.vat is not None and not calc.close(vat, t.vat, vat_tol):
            err("vat_total_mismatch", f"computed VAT is {vat}, but totals.vat is {t.vat}")
    if None not in (t.net, t.vat, t.gross) and not calc.close(calc.gross(t.net, t.vat), t.gross, tolerance):
        err("gross_total_mismatch", f"totals.net + totals.vat = {calc.gross(t.net, t.vat)}, but totals.gross is {t.gross}")

    # --- payment ---------------------------------------------------------------------
    p = order.payment
    if p.status == PaymentStatus.PAID and p.payment_date is None:
        err("paid_without_date", "payment status is PAID but no payment_date was extracted")
    if p.status != PaymentStatus.PAID and p.payment_date is not None:
        warn("date_without_paid", f"payment_date {p.payment_date} given but status is {p.status.value}; date ignored")
    known = {m.casefold() for m in known_payment_methods}
    if not p.method:
        warn("missing_payment_method", "no payment method extracted")
    elif known and p.method.casefold() not in known:
        warn("unknown_payment_method", f"payment method {p.method!r} has no code mapping in settings.yaml")

    # --- misc ------------------------------------------------------------------------
    if order.currency and order.currency != expected_currency:
        warn("currency", f"currency is {order.currency}, expected {expected_currency}")
    elif not order.currency:
        warn("currency", f"no currency extracted; assuming {expected_currency}")
    skus = Counter(i.sku.casefold() for i in order.items if i.sku)
    dups = sorted(s for s, n in skus.items() if n > 1)
    if dups:
        warn("duplicate_sku", f"SKU appears on more than one line: {', '.join(dups)}")

    return issues
