"""Invoice-editor selectors against the real probe (paid ticked) and document checks."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from i2c.config import load_settings
from i2c.flow.context import RunContext
from i2c.flow.invoice import _payment_combo
from i2c.flow.save_order import check_document
from i2c.models import OrderData
from i2c.ui.find import Snapshot
from tests.uia_dump import load_dump

ROOT = Path(__file__).resolve().parents[1]
ORDER = OrderData.model_validate(json.loads((ROOT / "tests/fixtures/order_01.expected.json").read_text("utf-8")))


def ctx():
    return RunContext(load_settings(ROOT), ORDER, app=None, evidence=None, run_dir=ROOT)


def test_paid_area_selectors():
    snap = Snapshot.take(load_dump(ROOT / "tests/fixtures/uia/invoice_paid.txt"))
    ed = snap.within(snap.find("Pane", name="New Invoice")[0])
    assert _payment_combo(ed, ctx()).rect.left == 716
    assert ed.by_label("at", ["Edit"]).rect.left == 968           # payment date
    assert ed.one("Edit", "Value").rect.left == 1314
    assert ed.by_label("Order Date", ["Edit"]).rect.left == 1758


def test_check_document():
    row = {"Document": "PO000001", "Date": "Jul 14, 2026", "Cust.Ref.": "WEB-2026-0714-A17",
           "State": "open", "Total": "$678.30"}
    assert check_document(row, date(2026, 7, 14), "WEB-2026-0714-A17", Decimal("678.30")) == []
    bad = dict(row, Total="$583.10", **{"Cust.Ref.": "WEB-2026 -07144..."})   # Cust.Ref. as OCR'd live
    warnings = []
    assert check_document(bad, date(2026, 7, 14), "WEB-2026-0714-A17", Decimal("678.30"), warnings) == ["Total '$583.10'"]
    assert len(warnings) == 1
