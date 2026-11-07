"""Stage `order` (brief 1.1-1.8): open a New Order, set Date / Cust.Ref. / Net / With VAT, verify.

Selectors come from the probe of the New Order editor (labels in settings.yaml):
- toolbar Button "Create: New Order"; editor = Pane "New Order"
- No.: unnamed Edit right of Text "No."; Date: unnamed Edit right of Text "Date"
- Cust.Ref.: Edit "Cust.Ref."; price mode: the unnamed ComboBox whose value is Net/Gross
- VAT mode: ComboBox "VAT" (distinct from the totals Edit "VAT")
"""
from __future__ import annotations

import logging

from i2c.errors import ManualReviewRequired, UIError
from i2c.flow.context import RunContext
from i2c.flow.runner import Step
from i2c.ui.find import Snapshot
from i2c.ui.input import (click, get_value, parse_display_date, select_combo, set_date,
                          set_date_by_arrows, set_text)
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)


def _pane(ctx: RunContext) -> str:
    return ctx.label("order_editor.tab")


def _number_edit(ed: Snapshot, ctx: RunContext):  # noqa: ANN202
    return ed.by_label(ctx.label("order_editor.number"), control_types=["Edit"])


def _price_mode_combo(ed: Snapshot, ctx: RunContext):  # noqa: ANN202
    values = set(ctx.label("order_editor.price_mode_values"))
    combos = [n for n in ed.find("ComboBox") if get_value(n.wrapper()) in values]
    if len(combos) != 1:
        raise UIError(f"expected one Net/Gross combo in the order editor, found {len(combos)}")
    return combos[0]


def set_order_date(ctx: RunContext, ctrl, value) -> None:  # noqa: ANN001
    """Date fields are spinner widgets: text set via UIA reverts (seen live), so the default
    is arrow-key editing; `input.date_method: text` switches back to typed text."""
    if ctx.settings.get("input.date_method", "arrows") == "arrows":
        set_date_by_arrows(ctrl, value)
    else:
        set_date(ctrl, value, pattern=ctx.date_pattern, methods=ctx.text_methods)


def _save_enabled(ctx: RunContext) -> bool:
    return ctx.snapshot().one("Button", ctx.label("toolbar.save")).enabled


# ------------------------------------------------------------------ steps

def open_order(ctx: RunContext) -> str:
    snap = ctx.snapshot()
    if snap.find("TabItem", name=_pane(ctx)):
        raise ManualReviewRequired(
            f"an unsaved '{_pane(ctx)}' editor is already open; close it (without saving) and "
            "re-run, so this run cannot write into a stale order", ctx.current_step)
    button = snap.one("Button", ctx.label("toolbar.new_order"))
    ctx.targets = [button.rect]
    click(button.wrapper())

    def editor_ready() -> str | None:
        ed = ctx.editor(_pane(ctx))
        number = get_value(_number_edit(ed, ctx).wrapper()).strip()
        return number or None

    number = wait_until(editor_ready, ctx.timeout("window"), ctx.interval, desc="New Order editor")
    ed = ctx.editor(_pane(ctx))
    ctx.targets = [_number_edit(ed, ctx).rect]
    ctx.results["order_no"] = number
    return f"New Order editor open, No. {number} (kept as proposed)"


def order_header(ctx: RunContext) -> str:
    order = ctx.order
    if order.order_date is None or not order.external_reference:
        raise ManualReviewRequired("order date or external reference missing", ctx.current_step)

    ed = ctx.editor(_pane(ctx))
    date_node = ed.by_label(ctx.label("order_editor.date"), control_types=["Edit"])
    set_order_date(ctx, date_node.wrapper(), order.order_date)

    ref_node = ed.by_label(ctx.label("order_editor.cust_ref"), control_types=["Edit"])
    set_text(ref_node.wrapper(), order.external_reference, methods=ctx.text_methods)

    # Focus has left the date field now; a value the widget did not commit would have reverted.
    shown = parse_display_date(get_value(date_node.wrapper()))
    if shown != order.order_date:
        raise UIError(f"Date reverted to {shown} after leaving the field (wanted {order.order_date})")

    # A value that only changed the widget text (not Fakturama's model) leaves Save disabled.
    # In that case type it for real once more; if Save is still disabled, stop.
    try:
        wait_until(lambda: _save_enabled(ctx), 2.0, ctx.interval, desc="editor dirty (Save enabled)")
    except Exception:  # noqa: BLE001
        log.warning("Save still disabled after setting values; re-entering Cust.Ref. with keystrokes")
        set_text(ref_node.wrapper(), order.external_reference, methods=("keys",))
        wait_until(lambda: _save_enabled(ctx), ctx.timeout(), ctx.interval,
                   desc="editor dirty (Save enabled) after keyboard input")
    ctx.targets = [date_node.rect, ref_node.rect]
    return f"Date={order.order_date.isoformat()} Cust.Ref.={order.external_reference} (read back)"


def order_modes(ctx: RunContext) -> str:
    ed = ctx.editor(_pane(ctx))
    price = _price_mode_combo(ed, ctx)
    select_combo(price.wrapper(), ctx.label("order_editor.price_mode_net"), ctx.timeout())
    vat = ed.by_label(ctx.label("order_editor.vat_mode"), control_types=["ComboBox"])
    select_combo(vat.wrapper(), ctx.label("order_editor.with_vat"), ctx.timeout())
    ctx.targets = [price.rect, vat.rect]
    return "price mode Net, VAT mode With VAT (read back)"


def verify_order_header(ctx: RunContext) -> str:
    """Independent re-read from a fresh snapshot: values survived focus changes."""
    order = ctx.order
    ed = ctx.editor(_pane(ctx))
    number = get_value(_number_edit(ed, ctx).wrapper()).strip()
    date_node = ed.by_label(ctx.label("order_editor.date"), control_types=["Edit"])
    ref_node = ed.by_label(ctx.label("order_editor.cust_ref"), control_types=["Edit"])
    price = _price_mode_combo(ed, ctx)
    vat = ed.by_label(ctx.label("order_editor.vat_mode"), control_types=["ComboBox"])
    got = {
        "No.": number,
        "Date": parse_display_date(get_value(date_node.wrapper())),
        "Cust.Ref.": get_value(ref_node.wrapper()).strip(),
        "price mode": get_value(price.wrapper()),
        "VAT mode": get_value(vat.wrapper()),
    }
    want = {
        "No.": ctx.results.get("order_no"),
        "Date": order.order_date,
        "Cust.Ref.": order.external_reference,
        "price mode": ctx.label("order_editor.price_mode_net"),
        "VAT mode": ctx.label("order_editor.with_vat"),
    }
    diff = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    ctx.targets = [date_node.rect, ref_node.rect, price.rect, vat.rect]
    if diff:
        raise UIError(f"order header differs after re-read (got, want): {diff}")
    return "header re-read OK: " + ", ".join(f"{k}={v}" for k, v in got.items())


STEPS = [
    Step("open_order", open_order),
    Step("set_header", order_header),
    Step("set_modes", order_modes),
    Step("verify_header", verify_order_header),
]
