"""Stage `save_order` (brief 4.1-4.7): verify the complete Order, save it once, find it in
Data > Documents, then create the linked Invoice via the Order's 'Create a follow-up document >
Invoice' (never the toolbar Invoice button).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from i2c.calc import close, parse_decimal
from i2c.errors import ManualReviewRequired, UIError
from i2c.flow.common import activate_editor, open_view, read_rect, save_once
from i2c.flow.context import RunContext
from i2c.flow.debtor import verify_order_addresses
from i2c.flow.products import _expected, _items_table_rect, _line_for, _value_ok
from i2c.flow.runner import Step
from i2c.match import cell_matches, code_matches
from i2c.ui.find import Snapshot
from i2c.ui.input import click, get_value, parse_display_date, select_combo, set_text
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)


def _L(ctx: RunContext, key: str) -> str:  # noqa: N802
    return ctx.label(key)


def order_tab(ctx: RunContext) -> str:
    """Editor tab of our order: 'New Order' until saved, then whatever Fakturama renames it to."""
    return ctx.results.get("order_tab") or _L(ctx, "order_editor.tab")


def money(ed: Snapshot, label: str) -> Decimal | None:
    return parse_decimal(get_value(ed.by_label(label, ["Edit"]).wrapper()))


def verify_totals(ctx: RunContext, ed: Snapshot, net_label_key: str = "order_editor.total_net") -> dict:
    t = ctx.order.totals
    shown = {"net": money(ed, _L(ctx, net_label_key)), "vat": money(ed, _L(ctx, "order_editor.vat_total")),
             "gross": money(ed, _L(ctx, "order_editor.total"))}
    want = {"net": t.net, "vat": t.vat, "gross": t.gross}
    diffs = {k: (str(shown[k]), str(want[k])) for k in want
             if want[k] is not None and (shown[k] is None or not close(shown[k], want[k]))}
    for key in (net_label_key, "order_editor.vat_total", "order_editor.total"):
        ctx.targets.append(ed.by_label(_L(ctx, key), ["Edit"]).rect)
    if diffs:
        raise ManualReviewRequired(f"totals differ from the source (shown, source): {diffs}", ctx.current_step,
                                   details=diffs)
    return shown


def verify_lines(ctx: RunContext, ed: Snapshot) -> list[dict]:
    rect = _items_table_rect(ed, ctx)
    table = read_rect(ctx, rect, "items")
    if len(table.rows) != len(ctx.order.items):
        raise UIError(f"{len(table.rows)} item lines shown, {len(ctx.order.items)} in the source: {table.as_dicts()}")
    for item in ctx.order.items:
        idx = _line_for(table, item.sku)
        if idx is None:
            raise UIError(f"no line for {item.sku}: {table.as_dicts()}")
        row = table.rows[idx]
        diffs = {c: (row.get(c), str(v)) for c, v in _expected(item).items() if not _value_ok(c, row.get(c), v)}
        if diffs:
            raise UIError(f"line {item.sku} differs (shown, expected): {diffs}")
        ctx.targets.append(row.rect)
    return table.as_dicts()


# ------------------------------------------------------------------ steps

def check_order(ctx: RunContext) -> str:
    ed = activate_editor(ctx, order_tab(ctx))
    verify_order_addresses(ctx)
    ed = ctx.editor(order_tab(ctx))
    lines = verify_lines(ctx, ed)
    # order-level discount 0 %, shipping 'Free of shipping costs' / 0.00 (brief 4.2)
    disc = ed.by_label(_L(ctx, "order_editor.discount"), ["Edit"])
    if parse_decimal(get_value(disc.wrapper())) != Decimal("0"):
        set_text(disc.wrapper(), "0", methods=ctx.text_methods, normalize=lambda s: str(parse_decimal(s)))
    ship = ed.one("ComboBox", _L(ctx, "order_editor.shipping"))
    select_combo(ship.wrapper(), _L(ctx, "order_editor.shipping_free"), ctx.timeout())
    ship_value = [n for n in ed.find("Edit") if n.name == "" and n.rect.v_overlap(ship.rect) > 10
                  and n.rect.left > ship.rect.right - 4]
    if not ship_value or parse_decimal(get_value(ship_value[0].wrapper())) != Decimal("0"):
        raise UIError("shipping amount is not 0.00")
    shown = verify_totals(ctx, ctx.editor(order_tab(ctx)))
    return (f"{len(lines)} lines, addresses, discount 0 %, free shipping; totals net {shown['net']} "
            f"vat {shown['vat']} gross {shown['gross']} = source")


def save_order(ctx: RunContext) -> str:
    snap = ctx.snapshot()
    before = {n.name for n in snap.find("TabItem")}
    activate_editor(ctx, order_tab(ctx))
    save_once(ctx, f"order {ctx.results.get('order_no')}")

    def renamed() -> str | None:
        after = {n.name for n in ctx.snapshot().find("TabItem")}
        new = [t for t in after - before if not t.startswith("*")]
        return new[0] if len(new) == 1 else None

    try:
        name = wait_until(renamed, 3.0, ctx.interval, desc="order tab rename after save")
    except Exception:  # noqa: BLE001 - tab kept its name
        name = order_tab(ctx).lstrip("*")
    ctx.results["order_tab"] = name
    ed = activate_editor(ctx, name)
    number = get_value(ed.by_label(_L(ctx, "order_editor.number"), ["Edit"]).wrapper()).strip()
    if number != ctx.results.get("order_no"):
        raise UIError(f"after saving, the editor shows order {number!r}, expected {ctx.results.get('order_no')!r}")
    return f"order {number} saved (tab {name!r})"


def find_document(ctx: RunContext, number: str) -> dict:
    """Data > Documents: type `number` into the view's search once, then poll the OCR'd list
    until exactly one row has that document number."""
    from i2c.flow.common import read_rect

    view = open_view(ctx, "documents", _L(ctx, "views.documents"))
    box = view.by_label(_L(ctx, "dialogs.search"), ["Edit"])
    set_text(box.wrapper(), number, methods=("keys",))
    rect = view.table_rect()
    ctx.targets.append(rect)

    def row() -> dict | None:
        rows = read_rect(ctx, rect, "documents").as_dicts()
        hits = [r for r in rows if code_matches(r.get("Document"), number)]
        if len(hits) > 1:
            raise UIError(f"{len(hits)} documents numbered {number!r}: {rows}")
        return hits[0] if hits else None

    return wait_until(row, ctx.timeout(), ctx.interval, desc=f"document {number} in Data > Documents")


def check_document(row: dict, date, cust_ref: str | None, total: Decimal | None) -> list[str]:  # noqa: ANN001
    problems = []
    shown = parse_display_date(row.get("Date", ""))
    if shown is not None and date is not None and shown != date:
        problems.append(f"Date {row.get('Date')!r}")
    if cust_ref and not cell_matches(row.get("Cust.Ref."), cust_ref):
        problems.append(f"Cust.Ref. {row.get('Cust.Ref.')!r}")
    got = parse_decimal(row.get("Total"))
    if total is not None and (got is None or not close(got, total)):
        problems.append(f"Total {row.get('Total')!r}")
    return problems


def verify_order_document(ctx: RunContext) -> str:
    number = ctx.results.get("order_no")
    row = find_document(ctx, number)
    ctx.results["order_document"] = row
    problems = check_document(row, ctx.order.order_date, ctx.order.external_reference, ctx.order.totals.gross)
    if problems:
        raise UIError(f"order {number} in Documents differs: {problems} (row {row})")
    return f"Documents: {row}"


def create_invoice(ctx: RunContext) -> str:
    """The saved order's own 'Create a follow-up document > Invoice' button (brief 4.6)."""
    ed = activate_editor(ctx, order_tab(ctx))
    buttons = [n for n in ed.find("Button", name=_L(ctx, "order_editor.follow_up_invoice"))]
    if len(buttons) != 1:
        raise UIError(f"expected one follow-up 'Invoice' button in the order, found {len(buttons)}")
    btn = buttons[0]
    wait_until(lambda: ctx.editor(order_tab(ctx)).find("Button", name=btn.name, enabled=True),
               ctx.timeout(), ctx.interval, desc="follow-up Invoice enabled")
    ctx.targets.append(btn.rect)
    click(btn.wrapper())
    editor = _L(ctx, "invoice_editor.tab")
    wait_until(lambda: ctx.snapshot().find("Pane", name=editor), ctx.timeout("window"), ctx.interval,
               desc="invoice editor")
    ed = ctx.editor(editor)
    number = get_value(ed.by_label(_L(ctx, "invoice_editor.number"), ["Edit"]).wrapper()).strip()
    ctx.results["invoice_no"] = number
    return f"invoice editor opened from the order's follow-up button (No. {number})"


STEPS = [
    Step("check_order", check_order),
    Step("save_order", save_order),
    Step("verify_in_documents", verify_order_document),
    Step("create_invoice", create_invoice),
]
