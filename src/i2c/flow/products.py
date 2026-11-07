"""Stage `products` (brief 3.1-3.17): VATs, products and the order's item lines, in source order.

1. VAT: Data > VATs, exact name 'VAT <p>%' and value p -> reuse; same name, other value ->
   manual review; missing -> create (Name = Description = 'VAT <p>%', code S, value p), save
   once, verify it is listed. (The list has no VAT-code column: the code is set on creation and
   not re-checked for an existing VAT - see README.)
2. Per item: Order > upper Items icon > 'Select a product' > search SKU > OCR > match.
   EXACT -> double-click (adds the line). NONE -> cancel, create the product (gross catalogue
   price = unit net x (1 + VAT), cost 0, stock 0, VAT by its description), save once, search
   again, double-click. AMBIGUOUS -> manual review.
3. Per line: set Qty and Discount in the Items table, then verify the OCR'd row: Item No.,
   Qty, U.Price (net), VAT, Discount and line Price = qty x unit net x (1 - discount).
"""
from __future__ import annotations

import logging
from decimal import Decimal

from i2c.calc import line_net, parse_decimal, product_gross_price
from i2c.errors import ManualReviewRequired, UIError
from i2c.flow.common import (activate_editor, cancel_dialog, open_selector, open_view,
                             pick_row_and_ok, read_rect, read_view_table, save_once, search_selector)
from i2c.flow.context import RunContext
from i2c.flow.runner import Step
from i2c.match import Outcome, cell_matches, match_by_name, match_product
from i2c.models import Item
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from i2c.ui.input import click, get_value, select_combo, set_text
from i2c.ui.table import Row, Table
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)

DIALOG = "dialogs.select_product"


def _L(ctx: RunContext, key: str) -> str:  # noqa: N802
    return ctx.label(key)


def vat_name(ctx: RunContext, percent: Decimal) -> str:
    p = format(percent.normalize(), "f")
    return str(ctx.settings.get("vat.name_template", "VAT {percent}%")).format(percent=p)


def _num_eq(cell: str | None, want: Decimal, tol: Decimal = Decimal("0.005")) -> bool:
    got = parse_decimal(cell)
    return got is not None and abs(got - want) <= tol


# ------------------------------------------------------------------ VATs

def ensure_vats(ctx: RunContext) -> str:
    percents = sorted({i.vat_percent for i in ctx.order.items if i.vat_percent is not None})
    if not percents:
        raise ManualReviewRequired("no VAT rates in the extracted items", ctx.current_step)
    done = []
    for p in percents:
        done.append(_ensure_vat(ctx, p))
    return "; ".join(done)


def _vat_rows(ctx: RunContext) -> list[dict[str, str]]:
    view = open_view(ctx, "vats", _L(ctx, "views.vats"))
    ctx.targets.append(view.table_rect())
    return read_view_table(ctx, view, "vats").as_dicts()


def _ensure_vat(ctx: RunContext, p: Decimal) -> str:
    name = vat_name(ctx, p)
    rows = _vat_rows(ctx)
    result = match_by_name(rows, name)
    entities = ctx.results["entities"].setdefault("vat", {})
    if result.outcome is Outcome.EXACT:
        if not _num_eq(rows[result.index].get("Value"), p):
            raise ManualReviewRequired(f"{name!r} exists with value {rows[result.index].get('Value')!r}, "
                                       f"expected {p}%", ctx.current_step, details=rows)
        entities[name] = "selected"
        return f"{name} exists"
    if result.outcome is not Outcome.NONE:
        raise ManualReviewRequired(result.reason, ctx.current_step, details=rows)

    click(ctx.snapshot().one("Button", _L(ctx, "views.new_vat_button")).wrapper())
    editor = _L(ctx, "vat_editor.tab")
    wait_until(lambda: ctx.snapshot().find("Pane", name=editor), ctx.timeout("window"), ctx.interval,
               desc="New TAX Rate editor")
    ed = ctx.editor(editor)
    m = ctx.text_methods
    set_text(ed.by_label(_L(ctx, "vat_editor.name"), ["Edit"]).wrapper(), name, methods=m)
    set_text(ed.by_label(_L(ctx, "vat_editor.description"), ["Edit"]).wrapper(), name, methods=m)
    code = ed.one("ComboBox", _L(ctx, "vat_editor.code"))
    select_combo(code.wrapper(), _L(ctx, "vat_editor.code_item"), ctx.timeout())
    value = ed.by_label(_L(ctx, "vat_editor.value"), ["Edit"])
    set_text(value.wrapper(), format(p.normalize(), "f"), methods=m,
             normalize=lambda s: str(parse_decimal(s)))
    ctx.targets = [code.rect, value.rect]
    save_once(ctx, f"VAT {name!r}")   # 'Set as standard' deliberately untouched

    rows = _vat_rows(ctx)
    check = match_by_name(rows, name)
    if check.outcome is not Outcome.EXACT or not _num_eq(rows[check.index].get("Value"), p):
        raise UIError(f"{name!r} not listed with value {p}% after saving: {rows}")
    entities[name] = "created"
    return f"{name} created (code S, value {p}%) and listed"


# ------------------------------------------------------------------ products + lines

def _items_table_rect(ed: Snapshot, ctx: RunContext) -> Rect:
    """The Items NatTable: the largest scrollbar-only Pane to the right of the 'Items' label."""
    label = ed.one("Text", _L(ctx, "order_editor.items"))
    panes = [n for n in ed.nodes if ed.is_leaf_pane(n) and n.rect.left >= label.rect.right - 4
             and n.rect.top <= label.rect.top + 12 and n.rect.bottom > label.rect.bottom + 40]
    if not panes:
        raise UIError("Items table not found right of the 'Items' label")
    return max(panes, key=lambda n: n.rect.area).rect


def read_items(ctx: RunContext) -> tuple[Table, Rect]:
    ed = activate_editor(ctx, _L(ctx, "order_editor.tab"))
    rect = _items_table_rect(ed, ctx)
    return read_rect(ctx, rect, "items"), rect


def _line_for(table: Table, sku: str) -> int | None:
    hits = [i for i, r in enumerate(table.rows) if cell_matches(r.get("Item No."), sku)]
    if len(hits) > 1:
        raise UIError(f"{len(hits)} item lines with item number {sku!r}")
    return hits[0] if hits else None


def make_add_line(item: Item):  # noqa: ANN201
    def add_line(ctx: RunContext) -> str:
        return _add_line(ctx, item)
    add_line.__name__ = f"add_line_{item.position}"
    return add_line


def _add_line(ctx: RunContext, item: Item) -> str:
    if not item.sku:
        raise ManualReviewRequired(f"item {item.position} has no SKU", ctx.current_step)
    order_tab = _L(ctx, "order_editor.tab")
    activate_editor(ctx, order_tab)
    items, _ = read_items(ctx)
    if _line_for(items, item.sku) is not None:
        raise ManualReviewRequired(f"the order already has a line for {item.sku!r}; refusing to add it twice",
                                   ctx.current_step, details=items.as_dicts())

    title = _L(ctx, DIALOG)
    entities = ctx.results["entities"].setdefault("product", {})
    open_selector(ctx, order_tab, "order_editor.items", title)
    table, _ = search_selector(ctx, title, item.sku, "select_product")
    rows = table.as_dicts()
    result = match_product(rows, item.sku, item.description)
    if result.outcome is Outcome.EXACT:
        _description_policy(ctx, result.reason, rows[result.index])
        pick_row_and_ok(ctx, title, table, result.index, click_column="Item No.")
        entities[item.sku] = "selected"
    elif result.outcome is Outcome.NONE:
        cancel_dialog(ctx, title)
        _create_product(ctx, item)
        entities[item.sku] = "created"
        activate_editor(ctx, order_tab)
        open_selector(ctx, order_tab, "order_editor.items", title)
        table, _ = search_selector(ctx, title, item.sku, "select_product")
        rows = table.as_dicts()
        again = match_product(rows, item.sku, item.description)
        if again.outcome is not Outcome.EXACT:
            raise ManualReviewRequired(f"new product {item.sku!r} not found exactly once after saving: "
                                       f"{again.reason}", ctx.current_step, details=rows)
        pick_row_and_ok(ctx, title, table, again.index, click_column="Item No.")
    else:
        raise ManualReviewRequired(result.reason, ctx.current_step, details=rows)

    def line_present() -> bool:
        t, _ = read_items(ctx)
        return _line_for(t, item.sku) is not None

    wait_until(line_present, ctx.timeout(), ctx.interval, desc=f"item line {item.sku!r}")
    return f"line for {item.sku} added ({entities[item.sku]} product)"


def _description_policy(ctx: RunContext, reason: str, row: dict) -> None:
    if "description differs" not in reason:
        return
    if ctx.settings.get("matching.product_description_mismatch", "warn") == "stop":
        raise ManualReviewRequired(reason, ctx.current_step, details=[row])
    log.warning("product match: %s", reason)
    ctx.results.setdefault("warnings", []).append(reason)


def _create_product(ctx: RunContext, item: Item) -> None:
    if item.unit_net_price is None or item.vat_percent is None:
        raise ManualReviewRequired(f"item {item.sku} lacks unit price or VAT", ctx.current_step)
    editor = _L(ctx, "product_editor.tab")
    snap = ctx.snapshot()
    if snap.find("TabItem", name=editor) or snap.find("TabItem", name="*" + editor):
        raise ManualReviewRequired(f"an unsaved '{editor}' editor is already open; close it and re-run",
                                   ctx.current_step)
    click(snap.one("Text", _L(ctx, "left_panel.new_product")).wrapper())
    wait_until(lambda: ctx.snapshot().find("Pane", name=editor), ctx.timeout("window"), ctx.interval,
               desc="New product editor")
    ed = ctx.editor(editor)
    m = ctx.text_methods
    gross = product_gross_price(item.unit_net_price, item.vat_percent)
    money = lambda s: str(parse_decimal(s))  # noqa: E731 - '$297.50' vs '297.50'
    set_text(ed.by_label(_L(ctx, "product_editor.item_number"), ["Edit"]).wrapper(), item.sku, methods=m)
    set_text(ed.by_label(_L(ctx, "product_editor.name"), ["Edit"]).wrapper(), item.description or "", methods=m)
    set_text(ed.by_label(_L(ctx, "product_editor.description"), ["Edit"]).wrapper(), item.description or "",
             methods=m)
    set_text(ed.by_label(_L(ctx, "product_editor.price_gross"), ["Edit"]).wrapper(), str(gross), methods=m,
             normalize=money)
    cost = ed.by_label(_L(ctx, "product_editor.cost_price"), ["Edit"])
    if parse_decimal(get_value(cost.wrapper())) != Decimal("0"):
        set_text(cost.wrapper(), "0.00", methods=m, normalize=money)
    vat_combo = ed.by_label(_L(ctx, "product_editor.vat"), ["ComboBox"])
    select_combo(vat_combo.wrapper(), vat_name(ctx, item.vat_percent), ctx.timeout())
    stock = ed.by_label(_L(ctx, "product_editor.stock"), ["Edit"])
    if parse_decimal(get_value(stock.wrapper())) != Decimal("0"):
        set_text(stock.wrapper(), "0.00", methods=m, normalize=money)
    ctx.targets = [vat_combo.rect]
    save_once(ctx, f"product {item.sku!r} (gross {gross})")


# ------------------------------------------------------------------ line values

def make_set_line(item: Item):  # noqa: ANN201
    def set_line(ctx: RunContext) -> str:
        return _set_line(ctx, item)
    set_line.__name__ = f"set_line_{item.position}"
    return set_line


def _expected(item: Item) -> dict[str, Decimal]:
    return {"Qty.": item.quantity, "U.Price": item.unit_net_price, "VAT": item.vat_percent,
            "Discount": item.discount_percent,
            "Price": line_net(item.quantity, item.unit_net_price, item.discount_percent)}


def _set_line(ctx: RunContext, item: Item) -> str:
    want = _expected(item)
    if any(v is None for v in want.values()):
        raise ManualReviewRequired(f"item {item.sku} has missing numbers: {want}", ctx.current_step)
    for column in ("Qty.", "Discount", "U.Price"):
        table, rect = read_items(ctx)
        idx = _line_for(table, item.sku)
        if idx is None:
            raise UIError(f"item line {item.sku!r} disappeared")
        if _num_eq(table.rows[idx].get(column), want[column]):
            continue
        _edit_cell(ctx, table, rect, idx, column, _fmt(want[column]))
        wait_until(lambda c=column: _num_eq(_cell(ctx, item.sku, c), want[c]), ctx.timeout(), ctx.interval,
                   desc=f"{item.sku} {column} = {want[column]}")

    table, _ = read_items(ctx)
    row = table.rows[_line_for(table, item.sku)]
    diffs = {c: (row.get(c), str(v)) for c, v in want.items() if not _num_eq(row.get(c), v)}
    ctx.targets.append(row.rect)
    if diffs:
        raise UIError(f"line {item.sku} differs (shown, expected): {diffs}")
    return (f"{item.sku}: qty {want['Qty.']} x {want['U.Price']} - {want['Discount']}% "
            f"@ VAT {want['VAT']}% = {want['Price']} (read back)")


def _fmt(v: Decimal) -> str:
    return format(v.normalize(), "f")


def _cell(ctx: RunContext, sku: str, column: str) -> str | None:
    table, _ = read_items(ctx)
    idx = _line_for(table, sku)
    return None if idx is None else table.rows[idx].get(column)


def _edit_cell(ctx: RunContext, table: Table, rect: Rect, idx: int, column: str, value: str) -> None:
    """NatTable cell edit: double-click the cell (x from the header grid lines, y from the row's
    OCR box), type, Enter. If an inline Edit appears in UIA it is typed into and read back."""
    from pywinauto import keyboard, mouse

    if column not in table.headers:
        raise UIError(f"Items table has no column {column!r} (headers {table.headers})")
    a, b = table.columns[table.headers.index(column)]
    row: Row = table.rows[idx]
    x, y = (a + b) // 2, int(row.rect.cy)
    ctx.targets.append(Rect(a, row.rect.top, b, row.rect.bottom))
    mouse.double_click(button="left", coords=(x, y))

    def inline_editor():  # noqa: ANN202
        ed = ctx.editor(_L(ctx, "order_editor.tab"))
        new = [n for n in ed.find("Edit") if rect.contains_point(*n.rect.center)]
        return new[0] if new else None

    try:
        node = wait_until(inline_editor, 1.5, ctx.interval, desc="inline cell editor")
        set_text(node.wrapper(), value, methods=("keys",), normalize=lambda s: str(parse_decimal(s)))
        log.info("edited %s via inline Edit (UIA)", column)
    except Exception:  # noqa: BLE001 - no UIA editor: type blind, verified by OCR afterwards
        log.info("no inline Edit for %s in UIA; typing into the focused cell", column)
        keyboard.send_keys("^a" + value)
    keyboard.send_keys("{ENTER}")


def check_net_mode(ctx: RunContext) -> str:
    """Line prices are compared as net: refuse to touch lines unless the order is in Net mode
    (in Gross mode U.Price shows gross and a 'fix' would write the wrong price)."""
    from i2c.flow.order import _price_mode_combo

    ed = activate_editor(ctx, _L(ctx, "order_editor.tab"))
    combo = _price_mode_combo(ed, ctx)
    mode = get_value(combo.wrapper())
    ctx.targets = [combo.rect]
    if mode != _L(ctx, "order_editor.price_mode_net"):
        raise UIError(f"order price mode is {mode!r}, expected Net (stage 'order' sets it)")
    return "order is in Net mode"


def make_steps(ctx_items: list[Item]) -> list[Step]:
    steps = [Step("check_net_mode", check_net_mode), Step("ensure_vats", ensure_vats)]
    for n, it in enumerate(ctx_items, 1):
        pos = it.position or n
        steps.append(Step(f"add_line_{pos}", make_add_line(it)))
        steps.append(Step(f"set_line_{pos}", make_set_line(it)))
    return steps
