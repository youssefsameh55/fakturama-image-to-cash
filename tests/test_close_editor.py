"""close_saved_editor (fake UIA, no Fakturama) and the per-step cost counters."""
from types import SimpleNamespace

import pytest

from i2c import perf
from i2c.errors import UIError
from i2c.flow.common import close_saved_editor


class FakeElement:
    def __init__(self):
        self.closed = False
        self.CurrentHasKeyboardFocus = True

    @property
    def CurrentIsOffscreen(self):  # noqa: N802 - mirrors UIA
        if self.closed:
            raise OSError("UIA_E_ELEMENTNOTAVAILABLE")
        return False

    @property
    def CurrentBoundingRectangle(self):  # noqa: N802
        return SimpleNamespace(right=100)


class FakeAnchor:
    """An Edit inside the editor; `closes_on` = the key that closes the editor, `dialog_on` opens one."""

    def __init__(self, app, closes_on="^w", dialog_on=None):
        self.element_info = SimpleNamespace(element=FakeElement())
        self.app, self.closes_on, self.dialog_on, self.sent = app, closes_on, dialog_on, []

    def type_keys(self, keys, **_):
        self.sent.append(keys)
        if keys == self.dialog_on:
            self.app.windows.append(SimpleNamespace(control_type="Window", name="Save Resource"))
        elif keys == self.closes_on:
            self.element_info.element.closed = True


def make_ctx(save_enabled=False):
    app = SimpleNamespace(windows=[])
    app.main = SimpleNamespace(element_info=SimpleNamespace(children=lambda: list(app.windows)))
    settings = SimpleNamespace(get=lambda key, default=None: default)
    save = SimpleNamespace(element=SimpleNamespace(CurrentIsEnabled=save_enabled))
    return SimpleNamespace(app=app, settings=settings, results={"_save_button": save})


def test_closes_with_first_key():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app)
    assert close_saved_editor(ctx, anchor, "VAT") is True
    assert anchor.sent == ["^w"]


def test_falls_back_to_second_key():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app, closes_on="^{F4}")
    assert close_saved_editor(ctx, anchor, "VAT") is True
    assert anchor.sent == ["^w", "^{F4}"]


def test_unsaved_editor_is_never_closed():
    ctx = make_ctx(save_enabled=True)
    anchor = FakeAnchor(ctx.app)
    assert close_saved_editor(ctx, anchor, "product") is False
    assert anchor.sent == []


def test_dialog_on_close_stops_the_run():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app, closes_on=None, dialog_on="^w")
    with pytest.raises(UIError, match="dialog"):
        close_saved_editor(ctx, anchor, "debtor")


def test_no_key_works_leaves_editor_open_with_warning():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app, closes_on=None)
    assert close_saved_editor(ctx, anchor, "payment method") is False
    assert "could not be closed" in ctx.results["warnings"][0]


def test_step_cost_is_the_change_since_before():
    perf.reset()
    with perf.timed("uia_find_first"):
        pass
    before = perf.step_cost()
    for name in ("uia_find_first", "uia_snapshot_cached", "ocr_pass", "evidence_capture"):
        with perf.timed(name):
            pass
    cost = perf.step_cost(before)
    assert cost["uia"][0] == 2 and cost["ocr"][0] == 1
