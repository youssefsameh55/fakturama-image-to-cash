"""Stage `invoice` (brief 5.1-5.7): complete and verify the Invoice created from the Order.

The editor is opened by save_order.create_invoice (follow-up of the saved order). Invoice No.,
Invoice date and Service date are left as proposed. Verified as copied from the order: Cust.Ref.,
invoice address, Order Date, Net / With VAT, both lines, totals. Then payment method; for PAID
the 'paid' box, the payment date ('at', probe 13) and Value = invoice total. Save once, then
Data > Documents must list the invoice (Cust.Ref., total, paid state) and the order (unchanged).
"""
from __future__ import annotations

import logging

from i2c.errors import ManualReviewRequired, UIError
from i2c.flow.common import activate_editor, save_once
from i2c.flow.context import RunContext
from i2c.flow.debtor import _address_box_text, _expect_address
from i2c.flow.order import _price_mode_combo
from i2c.flow.runner import Step
from i2c.flow.save_order import check_document, find_document, verify_lines, verify_totals
from i2c.calc import close, parse_decimal
from i2c.match import cell_matches, norm
from i2c.models import PaymentStatus
from i2c.ui.find import Snapshot
from i2c.ui.input import (get_checked, get_value, parse_display_date, select_combo, set_checkbox,
                          set_date_by_arrows, set_text)
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)


def _L(ctx: RunContext, key: str) -> str:  # noqa: N802
    return ctx.label(key)


def invoice_tab(ctx: RunContext) -> str:
    return ctx.results.get("invoice_tab") or _L(ctx, "invoice_editor.tab")


def _editor(ctx: RunContext) -> Snapshot:
    return activate_editor(ctx, invoice_tab(ctx))


def _payment_combo(ed: Snapshot, ctx: RunContext):  # noqa: ANN202
    """The unnamed ComboBox right of the 'paid' CheckBox (probe 10/13)."""
    paid = ed.one("CheckBox", _L(ctx, "invoice_editor.paid"))
    combos = [n for n in ed.find("ComboBox") if n.rect.v_overlap(paid.rect) > 10
              and n.rect.left >= paid.rect.right - 4]
    if not combos:
        raise UIError("payment method combo not found right of 'paid'")
    return min(combos, key=lambda n: n.rect.left - paid.rect.right)


# ------------------------------------------------------------------ steps

def check_invoice(ctx: RunContext) -> str:
    o = ctx.order
    ed = _editor(ctx)
    problems = []
    ref = get_value(ed.by_label(_L(ctx, "order_editor.cust_ref"), ["Edit"]).wrapper()).strip()
    if ref != o.external_reference:
        problems.append(f"Cust.Ref. {ref!r}")
    order_date = parse_display_date(get_value(ed.by_label(_L(ctx, "invoice_editor.order_date"), ["Edit"]).wrapper()))
    if order_date != o.order_date:
        problems.append(f"Order Date {order_date}")
    mode = get_value(_price_mode_combo(ed, ctx).wrapper())
    if mode != _L(ctx, "order_editor.price_mode_net"):
        problems.append(f"price mode {mode!r}")
    vat_mode = get_value(ed.by_label(_L(ctx, "order_editor.vat_mode"), ["ComboBox"]).wrapper())
    if vat_mode != _L(ctx, "order_editor.with_vat"):
        problems.append(f"VAT mode {vat_mode!r}")
    if problems:
        raise ManualReviewRequired(f"invoice header not copied from the order: {problems}", ctx.current_step)
    _expect_address(_address_box_text(ctx, ed, _L(ctx, "order_editor.invoice_address_tab")),
                    o.debtor.billing_address, o.debtor.company, "invoice")
    lines = verify_lines(ctx, ed)
    shown = verify_totals(ctx, _editor(ctx))
    return (f"copied from order: Cust.Ref., Order Date {o.order_date}, Net / With VAT, invoice address, "
            f"{len(lines)} lines, totals {shown['net']} / {shown['vat']} / {shown['gross']}")


def set_payment(ctx: RunContext) -> str:
    pay = ctx.order.payment
    ed = _editor(ctx)
    combo = _payment_combo(ed, ctx)
    try:
        select_combo(combo.wrapper(), pay.method or "", ctx.timeout())
    except UIError as exc:
        raise ManualReviewRequired(f"payment method {pay.method!r} not available in the invoice: {exc}",
                                   ctx.current_step) from exc
    ctx.targets.append(combo.rect)
    paid_box = ed.one("CheckBox", _L(ctx, "invoice_editor.paid"))

    if pay.status is not PaymentStatus.PAID:
        set_checkbox(paid_box.wrapper(), False)
        return f"payment method {pay.method!r}; status {pay.status.value}: 'paid' left unticked, nothing invented"

    if pay.payment_date is None:
        raise ManualReviewRequired("PAID without a payment date", ctx.current_step)
    set_checkbox(paid_box.wrapper(), True)
    at = _L(ctx, "invoice_editor.paid_at")
    wait_until(lambda: _editor(ctx).find("Text", name=at), ctx.timeout(), ctx.interval, desc="payment date field")
    ed = _editor(ctx)
    date_edit = ed.by_label(at, ["Edit"])
    set_date_by_arrows(date_edit.wrapper(), pay.payment_date)
    total = ctx.order.totals.gross
    value = ed.one("Edit", _L(ctx, "invoice_editor.value"))
    set_text(value.wrapper(), str(total), methods=ctx.text_methods, normalize=lambda s: str(parse_decimal(s)))

    ed = _editor(ctx)       # independent re-read
    got = {"method": get_value(_payment_combo(ed, ctx).wrapper()).strip(),
           "paid": get_checked(ed.one("CheckBox", _L(ctx, "invoice_editor.paid")).wrapper()),
           "date": parse_display_date(get_value(ed.by_label(at, ["Edit"]).wrapper())),
           "value": parse_decimal(get_value(ed.one("Edit", _L(ctx, "invoice_editor.value")).wrapper()))}
    want = {"method": pay.method, "paid": True, "date": pay.payment_date, "value": total}
    diffs = {k: (got[k], want[k]) for k in want
             if (not close(got[k], want[k]) if k == "value" and got[k] is not None else got[k] != want[k])}
    ctx.targets += [paid_box.rect, date_edit.rect, value.rect]
    if diffs:
        raise UIError(f"payment fields differ (shown, wanted): {diffs}")
    return f"payment {pay.method!r}, paid at {pay.payment_date} value {total} (read back)"


def save_invoice(ctx: RunContext) -> str:
    before = {n.name for n in ctx.snapshot().find("TabItem")}
    _editor(ctx)
    save_once(ctx, f"invoice {ctx.results.get('invoice_no')}")

    def renamed() -> str | None:
        new = [t for t in {n.name for n in ctx.snapshot().find("TabItem")} - before if not t.startswith("*")]
        return new[0] if len(new) == 1 else None

    try:
        name = wait_until(renamed, 3.0, ctx.interval, desc="invoice tab rename after save")
    except Exception:  # noqa: BLE001
        name = invoice_tab(ctx).lstrip("*")
    ctx.results["invoice_tab"] = name
    return f"invoice {ctx.results.get('invoice_no')} saved (tab {name!r})"


def verify_documents(ctx: RunContext) -> str:
    o = ctx.order
    inv_no, order_no = ctx.results.get("invoice_no"), ctx.results.get("order_no")
    inv = find_document(ctx, inv_no)
    problems = check_document(inv, None, o.external_reference, o.totals.gross)   # invoice date = today
    state = norm(inv.get("State"))
    if o.payment.status is PaymentStatus.PAID and ("unpaid" in state or "paid" not in state):
        problems.append(f"State {inv.get('State')!r} (expected paid)")
    if problems:
        raise UIError(f"invoice {inv_no} in Documents differs: {problems} (row {inv})")
    order = find_document(ctx, order_no)
    order_problems = check_document(order, o.order_date, o.external_reference, o.totals.gross)
    if order_problems:
        raise UIError(f"order {order_no} changed in Documents: {order_problems} (row {order})")
    ctx.results["invoice_document"], ctx.results["order_document"] = inv, order
    return f"invoice {inv_no}: {inv.get('State')!r} total {inv.get('Total')}; order {order_no} still listed: {order.get('State')!r}"


STEPS = [
    Step("check_invoice", check_invoice),
    Step("set_payment", set_payment),
    Step("save_invoice", save_invoice),
    Step("verify_documents", verify_documents),
]
