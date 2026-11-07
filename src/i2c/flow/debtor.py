"""Stage `debtor` (brief 2.1-2.13): select the Debtor in the Order, or create it first.

Flow: Order > upper Addresses icon > 'Select the address' > search company > OCR > match.
  EXACT -> select. AMBIGUOUS/CONFLICT -> manual review. NONE -> cancel, make sure the payment
  method exists (created before the Debtor so no unsaved editor waits in the background),
  create the Debtor (main address = invoice; a different delivery address becomes
  'additional address #1' with the Delivery role, G1), save once, then select it in the Order.
Verification: the Order's address box shows the source address (also proves the save).
"""
from __future__ import annotations

import logging

from i2c.errors import ManualReviewRequired, UIError
from i2c.flow.common import (activate_editor, activate_subtab, cancel_dialog, open_selector, open_view,
                             pick_row_and_ok, read_view_table, save_once, search_selector)
from i2c.flow.context import RunContext
from i2c.flow.runner import Step
from i2c.match import Outcome, match_by_name, match_debtor, norm
from i2c.models import Address
from i2c.ui.find import Snapshot
from i2c.ui.input import click, get_checked, get_value, select_combo, set_checkbox, set_text
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)

DIALOG = "dialogs.select_address"


def _L(ctx: RunContext, key: str) -> str:  # noqa: N802 - short label accessor
    return ctx.label(key)


def _creating(ctx: RunContext) -> bool:
    return ctx.results["entities"].get("debtor") in ("to_create", "created")


def _review(ctx: RunContext, result, rows) -> ManualReviewRequired:  # noqa: ANN001
    return ManualReviewRequired(result.reason, ctx.current_step,
                                details=[rows[i] for i in result.candidates] or rows)


# ------------------------------------------------------------------ step 1: search

def find_debtor(ctx: RunContext) -> str:
    debtor = ctx.order.debtor
    if not debtor.company:
        raise ManualReviewRequired("no company name extracted", ctx.current_step)
    order_tab = _L(ctx, "order_editor.tab")
    activate_editor(ctx, order_tab)
    title = _L(ctx, DIALOG)
    open_selector(ctx, order_tab, "order_editor.addresses", title)
    table, _ = search_selector(ctx, title, debtor.company, "select_address")
    rows = table.as_dicts()
    result = match_debtor(rows, debtor)
    ctx.results["debtor_search"] = {"query": debtor.company, "rows": rows, "outcome": result.outcome.value}

    if result.outcome is Outcome.EXACT:
        pick_row_and_ok(ctx, title, table, result.index)
        ctx.results["entities"]["debtor"] = "selected"
        return f"existing debtor selected: {rows[result.index]}"
    if result.outcome is Outcome.NONE:
        cancel_dialog(ctx, title)
        ctx.results["entities"]["debtor"] = "to_create"
        return f"no debtor {debtor.company!r} ({len(rows)} search rows) -> will create"
    raise _review(ctx, result, rows)


# ------------------------------------------------------------------ step 2: payment method

def ensure_payment_method(ctx: RunContext) -> str:
    if not _creating(ctx):
        return "skipped: debtor already exists"
    method = ctx.order.payment.method
    if not method:
        raise ManualReviewRequired("no payment method extracted", ctx.current_step)
    view_name = _L(ctx, "views.terms_of_payment")
    view = open_view(ctx, "terms_of_payment", view_name)
    table = read_view_table(ctx, view, "payments")
    rows = table.as_dicts()
    result = match_by_name(rows, method, {"Description": method})
    ctx.targets = [view.table_rect()]
    if result.outcome is Outcome.EXACT:
        ctx.results["entities"]["payment_method"] = "selected"
        return f"payment method {method!r} exists"
    if result.outcome is not Outcome.NONE:
        raise _review(ctx, result, rows)

    code = (ctx.settings.get("payment_method_codes") or {}).get(method)
    if not code:
        raise ManualReviewRequired(f"no payment code mapping for {method!r} in settings.yaml", ctx.current_step)
    # The view's toolbar (green +) is a sibling of the view pane, not inside it (probe 06a).
    click(ctx.snapshot().one("Button", _L(ctx, "views.new_payment_button")).wrapper())
    editor = _L(ctx, "payment_editor.tab")
    wait_until(lambda: ctx.snapshot().find("Pane", name=editor), ctx.timeout("window"), ctx.interval,
               desc="New Term of Payment editor")
    ed = ctx.editor(editor)
    set_text(ed.by_label(_L(ctx, "payment_editor.name"), ["Edit"]).wrapper(), method, methods=ctx.text_methods)
    set_text(ed.by_label(_L(ctx, "payment_editor.description"), ["Edit"]).wrapper(), method,
             methods=ctx.text_methods)
    code_combo = ed.one("ComboBox", _L(ctx, "payment_editor.code"))
    select_combo(code_combo.wrapper(), code, ctx.timeout())
    _expect_defaults(ctx, ed, {
        "payment_editor.cash_discount": ("0%", "0%"), "payment_editor.discount_days": ("0", "0"),
        "payment_editor.net_days": ("0", "0"), "payment_editor.text_unpaid": ("", ""),
        "payment_editor.text_deposit": ("", ""), "payment_editor.text_paid": ("", ""),
    })
    ctx.targets = [code_combo.rect]
    save_once(ctx, f"payment method {method!r}")   # 'Set as standard' deliberately not touched

    table = read_view_table(ctx, open_view(ctx, "terms_of_payment", view_name), "payments")
    check = match_by_name(table.as_dicts(), method, {"Description": method})
    if check.outcome is not Outcome.EXACT:
        raise UIError(f"payment method {method!r} not listed after saving: {check.reason}")
    ctx.results["entities"]["payment_method"] = "created"
    return f"payment method {method!r} created (code {code!r}) and listed"


def _expect_defaults(ctx: RunContext, ed: Snapshot, fields: dict[str, tuple[str, str]]) -> None:
    """Fields that must hold a value (normally the default): set them only if they differ."""
    for key, (want, typed) in fields.items():
        node = ed.find("Edit", name=_L(ctx, key))
        if not node:
            raise UIError(f"field {_L(ctx, key)!r} not found")
        w = node[0].wrapper()
        if get_value(w).strip() != want:
            set_text(w, typed, methods=ctx.text_methods, normalize=lambda s: s.strip())


# ------------------------------------------------------------------ step 3: create debtor

def create_debtor(ctx: RunContext) -> str:
    if not _creating(ctx):
        return "skipped: debtor already exists"
    d = ctx.order.debtor
    editor = _L(ctx, "debtor_editor.tab")
    snap = ctx.snapshot()
    if snap.find("TabItem", name=editor) or snap.find("TabItem", name="*" + editor):
        raise ManualReviewRequired(
            f"an unsaved '{editor}' editor is already open; close it (without saving) and re-run",
            ctx.current_step)
    click(snap.one("Text", _L(ctx, "left_panel.new_contact")).wrapper())
    wait_until(lambda: ctx.snapshot().find("Pane", name=editor), ctx.timeout("window"), ctx.interval,
               desc="New Debtor editor")
    ed = ctx.editor(editor)
    ctx.results["customer_id"] = get_value(ed.by_label(_L(ctx, "debtor_editor.customer_id"), ["Edit"]).wrapper())

    set_text(ed.by_label(_L(ctx, "debtor_editor.company"), ["Edit"]).wrapper(), d.company, methods=ctx.text_methods)
    first, last = ed.inputs_after_label(_L(ctx, "debtor_editor.name"))[:2]
    set_text(first.wrapper(), d.first_name or "", methods=ctx.text_methods)
    set_text(last.wrapper(), d.last_name or "", methods=ctx.text_methods)

    same = d.delivery_same_as_billing
    invoice_role, delivery_role = _L(ctx, "debtor_editor.role_invoice"), _L(ctx, "debtor_editor.role_delivery")
    _fill_address(ctx, editor, d.billing_address, d.company, contact=(d.email, d.phone),
                  roles={invoice_role: True, delivery_role: same})
    if not same:
        ed = ctx.editor(editor)
        click(ed.one("Button", _L(ctx, "debtor_editor.add_address")).wrapper())
        extra = _L(ctx, "debtor_editor.additional_tab")
        wait_until(lambda: ctx.editor(editor).find("TabItem", name=extra), ctx.timeout(), ctx.interval,
                   desc=f"tab {extra!r}")
        activate_subtab(ctx, editor, extra, lambda s: _active_address_tab(s) == extra)
        _fill_address(ctx, editor, d.delivery_address, d.company, contact=(None, None),
                      roles={invoice_role: False, delivery_role: True})

    misc = activate_subtab(ctx, editor, _L(ctx, "debtor_editor.tab_misc"),
                           lambda s: bool(s.find("Edit", name=_L(ctx, "debtor_editor.alias"))))
    if d.alias:
        set_text(misc.one("Edit", _L(ctx, "debtor_editor.alias")).wrapper(), d.alias, methods=ctx.text_methods)
    _expect_defaults(ctx, misc, {"debtor_editor.discount": ("0%", "0%")})
    select_combo(misc.one("ComboBox", _L(ctx, "debtor_editor.net_gross")).wrapper(),
                 _L(ctx, "debtor_editor.net"), ctx.timeout())
    pay = misc.one("ComboBox", _L(ctx, "debtor_editor.payment"))
    select_combo(pay.wrapper(), ctx.order.payment.method, ctx.timeout())
    ctx.targets = [pay.rect]

    save_once(ctx, f"debtor {d.company!r}")
    _verify_saved_debtor(ctx)
    ctx.results["entities"]["debtor"] = "created"
    return (f"debtor {d.company!r} created (Customer ID {ctx.results['customer_id']} kept), "
            f"{'1 address' if same else 'invoice + delivery address'}, payment {ctx.order.payment.method!r}")


def _verify_saved_debtor(ctx: RunContext) -> None:
    """Read the saved debtor back from Data > Debtors: proves Fakturama stored the values, not
    just the widgets (live: a Company shown in the editor was saved empty). The list's ZIP/City
    show one of the debtor's addresses (seen: the delivery one), so only names are compared."""
    d = ctx.order.debtor
    view = open_view(ctx, "debtors", _L(ctx, "views.debtors"))
    rows = read_view_table(ctx, view, "debtors").as_dicts()
    hits = [r for r in rows if all(norm(r.get(k)) == norm(v) for k, v in
                                   (("Company", d.company), ("First Name", d.first_name), ("Name", d.last_name)))]
    if len(hits) != 1:
        raise UIError(f"saved debtor not found exactly once in the Debtors list by company + name "
                      f"(found {len(hits)}): {rows}")
    ctx.targets.append(view.table_rect())


def _active_address_tab(ed: Snapshot) -> str | None:
    """Which address sub-tab is shown: probe dumps expose the active one as a `Tab` node."""
    tabs = [n.name for n in ed.find("Tab") if n.name.startswith(("Main address", "additional address"))]
    return tabs[0] if len(tabs) == 1 else None


def _fill_address(ctx: RunContext, editor: str, addr: Address | None, company: str | None,
                  contact: tuple[str | None, str | None], roles: dict[str, bool]) -> None:
    if addr is None:
        raise ManualReviewRequired("address missing in extracted data", ctx.current_step)
    ed = ctx.editor(editor)
    m = ctx.text_methods
    additional = addr.additional or (addr.name if addr.name and norm(addr.name) != norm(company) else None)
    if additional:
        set_text(ed.by_label(_L(ctx, "debtor_editor.additional_name"), ["Edit"]).wrapper(), additional, methods=m)
    set_text(ed.by_label(_L(ctx, "debtor_editor.street"), ["Edit"]).wrapper(), addr.street or "", methods=m)
    zip_edit, city_edit = ed.inputs_after_label(_L(ctx, "debtor_editor.zip_city"))[:2]
    set_text(zip_edit.wrapper(), addr.zip or "", methods=m)
    set_text(city_edit.wrapper(), addr.city or "", methods=m)
    if addr.country:
        select_combo(ed.by_label(_L(ctx, "debtor_editor.country"), ["ComboBox"]).wrapper(), addr.country,
                     ctx.timeout())
    email, phone = contact
    if email:
        set_text(ed.by_label(_L(ctx, "debtor_editor.email"), ["Edit"]).wrapper(), email, methods=m)
    if phone:
        set_text(ed.by_label(_L(ctx, "debtor_editor.phone"), ["Edit"]).wrapper(), phone, methods=m)
    _set_roles(ctx, editor, roles)


def _set_roles(ctx: RunContext, editor: str, roles: dict[str, bool]) -> None:
    """address type: the ▸ button opens a popup with role CheckBoxes (probe 11_address_type)."""
    label = _L(ctx, "debtor_editor.address_type")
    ed = ctx.editor(editor)
    field = ed.inputs_after_label(label, ("Edit",))[0]
    button = ed.inputs_after_label(label, ("Button",))[0]
    first_role = next(iter(roles))
    click(button.wrapper())
    wait_until(lambda: ctx.snapshot().find("CheckBox", name=first_role), ctx.timeout(), ctx.interval,
               desc="address type popup")
    snap = ctx.snapshot()
    for role, on in roles.items():
        set_checkbox(snap.one("CheckBox", role).wrapper(), on)
    states = {role: get_checked(snap.one("CheckBox", role).wrapper()) for role in roles}
    _close_popup(ctx, first_role, field)
    shown = norm(get_value(field.wrapper()))
    wrong = [r for r, on in roles.items() if on != (norm(r) in shown) or states[r] != on]
    if wrong:
        raise UIError(f"address type shows {get_value(field.wrapper())!r}, roles wanted {roles}")
    ctx.targets.append(field.rect)


def _close_popup(ctx: RunContext, marker: str, field) -> None:  # noqa: ANN001
    """Live finding: clicking ▸ again does not close the role popup. Esc first; if it is still
    open, click into the address-type field (focus leaves the popup). The field already shows
    the roles at this point, so neither loses the selection - which is verified afterwards."""
    from pywinauto import keyboard

    def closed() -> bool:
        return not ctx.snapshot().find("CheckBox", name=marker)

    keyboard.send_keys("{ESC}")
    try:
        wait_until(closed, 2.0, ctx.interval, desc="role popup to close after Esc")
        return
    except Exception:  # noqa: BLE001
        log.info("role popup still open after Esc; clicking the address type field")
    click(field.wrapper())
    wait_until(closed, ctx.timeout(), ctx.interval, desc="role popup to close")


# ------------------------------------------------------------------ step 4: select in order

def select_created_debtor(ctx: RunContext) -> str:
    if ctx.results["entities"].get("debtor") != "created":
        return "skipped: debtor was selected directly"
    debtor = ctx.order.debtor
    order_tab = _L(ctx, "order_editor.tab")
    activate_editor(ctx, order_tab)
    title = _L(ctx, DIALOG)
    open_selector(ctx, order_tab, "order_editor.addresses", title)
    table, _ = search_selector(ctx, title, debtor.company, "select_address")
    rows = table.as_dicts()
    result = match_debtor(rows, debtor)
    if result.outcome is not Outcome.EXACT:
        raise ManualReviewRequired(f"new debtor not found exactly once after saving: {result.reason}",
                                   ctx.current_step, details=rows)
    pick_row_and_ok(ctx, title, table, result.index)
    return f"new debtor selected in the order: {rows[result.index]}"


# ------------------------------------------------------------------ step 5: verify

def verify_order_addresses(ctx: RunContext) -> str:
    d = ctx.order.debtor
    ed = activate_editor(ctx, _L(ctx, "order_editor.tab"))
    text = _address_box_text(ctx, ed, _L(ctx, "order_editor.invoice_address_tab"))
    _expect_address(text, d.billing_address, d.company, "invoice")
    checked = ["invoice"]
    if not d.delivery_same_as_billing:
        tab = _L(ctx, "order_editor.delivery_address_tab")
        if not ed.find("TabItem", name=tab) and not ed.find("Tab", name=tab):
            raise UIError(f"order has no {tab!r} tab although the debtor has a delivery address")
        ed = activate_subtab(ctx, _L(ctx, "order_editor.tab"), tab, lambda s: bool(s.find("Tab", name=tab)))
        _expect_address(_address_box_text(ctx, ed, tab), d.delivery_address, None, "delivery")
        checked.append("delivery")
    return f"order shows the source {' and '.join(checked)} address"


def _address_box_text(ctx: RunContext, ed: Snapshot, tab_name: str) -> str:
    tabs = ed.find("Tab", name=tab_name)
    if not tabs:
        raise UIError(f"address tab {tab_name!r} not visible in the order")
    edits = [n for n in ed.within(tabs[0]).find("Edit")]
    if not edits:
        raise UIError(f"no address text box in {tab_name!r}")
    ctx.targets.append(edits[0].rect)
    return get_value(edits[0].wrapper())


def _expect_address(text: str, addr: Address | None, company: str | None, which: str) -> None:
    flat = norm(text)
    parts = [company, addr.name if addr else None, addr.street if addr else None,
             addr.zip if addr else None, addr.city if addr else None]
    missing = [p for p in parts if p and norm(p) not in flat]
    if not flat or missing:
        raise UIError(f"order {which} address {text!r} lacks {missing}")


STEPS = [
    Step("find_debtor", find_debtor),
    Step("ensure_payment_method", ensure_payment_method),
    Step("create_debtor", create_debtor),
    Step("select_created_debtor", select_created_debtor),
    Step("verify_addresses", verify_order_addresses),
]
