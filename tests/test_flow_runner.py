"""Runner semantics, --from-json validation and the combo keyboard fallback (no real UI)."""
import json
from pathlib import Path

import pytest
from PIL import Image

from i2c.config import load_settings
from i2c.errors import ManualReviewRequired, UIError
from i2c.flow.context import RunContext
from i2c.flow.runner import EXIT_ERROR, EXIT_MANUAL_REVIEW, EXIT_OK, Step, run_flow
from i2c.main import _load_order_json
from i2c.models import OrderData
from i2c.ui.evidence import Evidence
from i2c.ui.geom import Rect
from i2c.ui.input import select_combo

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "order_01.expected.json"


class StaticEvidence(Evidence):
    """Evidence that 'captures' a blank image instead of the screen."""

    def shot(self, step, caption, targets=None, ok=True, image=None, origin=None):
        return super().shot(step, caption, targets, ok, image=Image.new("RGB", (64, 48), "white"),
                            origin=(0, 0))


@pytest.fixture
def ctx(tmp_path):
    settings = load_settings(ROOT)
    order = OrderData.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    return RunContext(settings, order, app=None, evidence=StaticEvidence(tmp_path, background=False),
                      run_dir=tmp_path)


def _summary(ctx):
    return json.loads((ctx.run_dir / "summary.json").read_text(encoding="utf-8"))


def test_runner_ok_stops_after_stage_and_records_steps(ctx):
    def a(c):
        c.targets = [Rect(1, 1, 5, 5)]
        c.results["order_no"] = "PO1"
        return "did a"

    code = run_flow(ctx, [("order", [Step("a", a)]), ("debtor", [Step("b", lambda c: "b")])],
                    stop_after="order")
    s = _summary(ctx)
    assert code == EXIT_OK and s["status"] == "ok" and s["stages_done"] == ["order"]
    assert s["steps"][0]["message"] == "did a" and s["steps"][0]["screenshot"] == "01_order_a.png"
    assert s["results"]["order_no"] == "PO1"
    assert (ctx.run_dir / "01_order_a.png").exists()


def test_runner_manual_review_stops_with_exit_3(ctx):
    def stop(c):
        raise ManualReviewRequired("two debtors match", c.current_step)

    ran = []
    code = run_flow(ctx, [("debtor", [Step("match", stop), Step("never", lambda c: ran.append(1))])], None)
    s = _summary(ctx)
    assert code == EXIT_MANUAL_REVIEW and not ran
    assert s["status"] == "manual_review" and s["step"] == "debtor.match"
    assert s["steps"][0]["status"] == "failed" and s["steps"][0]["screenshot"]


def test_runner_error_exit_4_and_not_implemented_marks_incomplete(ctx):
    def boom(c):
        raise UIError("control vanished")

    assert run_flow(ctx, [("order", [Step("x", boom)])], None) == EXIT_ERROR
    assert _summary(ctx)["status"] == "error"

    assert run_flow(ctx, [("order", []), ("debtor", [])], None, not_implemented=("debtor",)) == EXIT_OK
    s = _summary(ctx)
    assert s["status"] == "incomplete" and s["stopped_at"] == "debtor"


def test_from_json_validates_and_copies(tmp_path):
    settings = load_settings(ROOT)
    order = _load_order_json(settings, FIXTURE, tmp_path)
    assert order.external_reference == "WEB-2026-0714-A17"
    assert (tmp_path / "extracted.json").exists()

    bad = json.loads(FIXTURE.read_text(encoding="utf-8"))
    bad["totals"]["net"] = "999.00"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ManualReviewRequired):
        _load_order_json(settings, p, tmp_path)


class FakeCombo:
    """Read-only combo: select() unsupported, HOME/DOWN move through items."""

    class _V:
        def __init__(self, combo):
            self.combo = combo

        @property
        def CurrentValue(self):  # noqa: N802
            return self.combo.items[self.combo.i]

    def __init__(self, items, i):
        self.items, self.i = items, i
        self.iface_value = self._V(self)

    def select(self, text):
        raise RuntimeError("no list items exposed")

    def set_focus(self):
        pass

    def is_expanded(self):
        return False

    def type_keys(self, keys, **_):
        if keys == "{HOME}":
            self.i = 0
        elif keys == "{DOWN}":
            self.i = min(self.i + 1, len(self.items) - 1)


def test_select_combo_arrow_fallback():
    c = FakeCombo(["Gross", "Net"], 0)
    assert select_combo(c, "Net", timeout=0.1) == "Net"
    with pytest.raises(UIError):
        select_combo(FakeCombo(["Gross", "Net"], 0), "Brutto", timeout=0.1)
