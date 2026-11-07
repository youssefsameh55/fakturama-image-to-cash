import json
from decimal import Decimal
from pathlib import Path

from i2c.calc import parse_decimal
from i2c.config import load_settings
from i2c.flow.context import RunContext
from i2c.flow.products import _expected, _items_table_rect, make_steps, vat_name
from i2c.models import OrderData
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from tests.uia_dump import load_dump

ROOT = Path(__file__).resolve().parents[1]
ORDER = OrderData.model_validate(json.loads((ROOT / "tests/fixtures/order_01.expected.json").read_text("utf-8")))


def ctx():
    return RunContext(load_settings(ROOT), ORDER, app=None, evidence=None, run_dir=ROOT)


def test_parse_decimal_ui_formats():
    assert parse_decimal("$1,234.50") == Decimal("1234.50")
    assert parse_decimal("19.00 %") == Decimal("19.00")
    assert parse_decimal("VAT 19%") == Decimal("19")
    assert parse_decimal("2") == Decimal("2")
    assert parse_decimal("Free of Tax") is None
    assert parse_decimal("") is None


def test_vat_name_and_expected_lines():
    c = ctx()
    assert vat_name(c, Decimal("19")) == "VAT 19%"
    assert vat_name(c, Decimal("19.00")) == "VAT 19%"
    assert vat_name(c, Decimal("7.5")) == "VAT 7.5%"
    chair, mat = ORDER.items
    assert _expected(chair) == {"Qty.": Decimal("2"), "U.Price": Decimal("250.00"), "VAT": Decimal("19"),
                                "Discount": Decimal("10"), "Price": Decimal("450.00")}
    assert _expected(mat)["Price"] == Decimal("120.00")


def test_items_table_found_right_of_label():
    snap = Snapshot.take(load_dump(ROOT / "tests/fixtures/uia/new_order.txt"))
    ed = snap.within(snap.find("Pane", name="New Order")[0])
    assert _items_table_rect(ed, ctx()) == Rect(686, 646, 2978, 1048)


def test_steps_follow_source_order():
    names = [s.name for s in make_steps(ORDER.items)]
    assert names == ["ensure_vats", "add_line_1", "set_line_1", "add_line_2", "set_line_2"]
