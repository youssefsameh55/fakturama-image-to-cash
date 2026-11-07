"""find.Snapshot against real probe dumps replayed offline."""
from pathlib import Path

import pytest

from i2c.errors import AmbiguousElement, ElementNotFound
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from tests.uia_dump import load_dump

UIA = Path(__file__).parent / "fixtures" / "uia"


@pytest.fixture(scope="module")
def order() -> Snapshot:
    return Snapshot.take(load_dump(UIA / "new_order.txt"))


@pytest.fixture(scope="module")
def address_dialog() -> Snapshot:
    return Snapshot.take(load_dump(UIA / "select_address.txt"))


def test_named_edit_found_directly(order):
    assert order.by_label("Cust.Ref.").rect == Rect(742, 418, 1576, 456)


def test_unnamed_edit_found_right_of_label(order):
    no = order.by_label("No.", control_types=["Edit"])
    date = order.by_label("Date", control_types=["Edit"])
    assert no.name == "" and no.rect.left == 684
    assert date.rect == Rect(1472, 292, 1728, 328)


def test_vat_label_ambiguity_resolved_by_type(order):
    assert order.by_label("VAT", control_types=["ComboBox"]).name == "VAT"
    with pytest.raises(AmbiguousElement):
        order.by_label("VAT")  # ComboBox "VAT" and the totals Edit "VAT"


def test_price_mode_combo_by_value_free_query(order):
    combos = [n for n in order.find("ComboBox") if n.name == ""]
    assert len(combos) == 1 and combos[0].rect.left == 1826


def test_toolbar_buttons_by_tooltip_name(order):
    assert order.one("Button", "Create: New Order")
    save = order.one("Button", "Save the current contents")
    assert save.enabled is False
    inv = order.one("Button", "Invoice")
    assert inv.rect.left == 2467


def test_missing_label_raises(order):
    with pytest.raises(ElementNotFound):
        order.by_label("Does not exist")


def test_dialog_table_rect_excludes_scrollbar(address_dialog):
    dialog = address_dialog.one("Window", "Select the address")
    sub = address_dialog.within(dialog)
    assert sub.table_rect() == Rect(713, 346, 2273, 1176)
    assert sub.one("Button", "OK") and sub.one("Button", "Cancel")
    search = sub.by_label("Search:", control_types=["Edit"])
    assert search.rect.left == 1935


def test_view_table_rect_scoped_to_named_pane():
    snap = Snapshot.take(load_dump(UIA / "documents.txt"))
    view = snap.one("Pane", "Documents")
    assert snap.within(view).table_rect() == Rect(668, 1422, 2992, 1896)


def test_icons_below_label_upper_is_select_existing(order):
    addr = order.icons_below("Addresses")
    assert [i.rect.top for i in addr] == [514, 570]          # upper = select, lower = green +
    items = order.icons_below("Items")
    assert items[0].rect.top == 692 and len(items) == 4


def test_tab_item_ignores_dirty_star(order):
    assert order.tab_item("New Order").name == "New Order"
    with pytest.raises(ElementNotFound):
        order.tab_item("Nope")


def test_address_type_popup_and_second_address():
    from i2c.flow.debtor import _active_address_tab

    popup = Snapshot.take(load_dump(UIA / "debtor_address_type_popup.txt"))
    assert {n.name for n in popup.find("CheckBox")} == {"Invoice address", "Delivery address"}
    field = popup.inputs_after_label("address type", ("Edit",))[0]
    button = popup.inputs_after_label("address type", ("Button",))[0]
    assert button.rect.left >= field.rect.right - 1

    second = Snapshot.take(load_dump(UIA / "debtor_second_address.txt"))
    assert _active_address_tab(second) == "additional address #1"
    assert _active_address_tab(Snapshot.take(load_dump(UIA / "debtor_addresses.txt"))) == "Main address"
    assert second.one("Button", "+")


def test_debtor_two_edits_one_label():
    snap = Snapshot.take(load_dump(UIA / "debtor_addresses.txt"))
    assert snap.by_label("Street").name == "Street"
    # Two-column form: "Telefax" (second column) must not be taken as the City field.
    zip_, city = snap.inputs_after_label("ZIP - City")
    assert (zip_.rect.left, city.rect.left) == (894, 1026)
    first, last = snap.inputs_after_label("First Name Last Name")
    assert first.rect.left < last.rect.left
    assert [n.name for n in snap.inputs_after_label("Telefax")] == ["Telefax"]
