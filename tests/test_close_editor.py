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

    def __init__(self, app, closes_on="^{F4}", dialog_on=None, dirties_on=None, save=None):
        self.element_info = SimpleNamespace(element=FakeElement())
        self.app, self.closes_on, self.dialog_on, self.sent = app, closes_on, dialog_on, []
        self.dirties_on, self.save = dirties_on, save

    def type_keys(self, keys, **_):
        self.sent.append(keys)
        if keys == self.dirties_on:          # live: Ctrl+W typed a 'w' into the Name box
            self.save.element.CurrentIsEnabled = True
        elif keys == self.dialog_on:
            self.app.windows.append(SimpleNamespace(control_type="Window", name="Save Resource"))
        elif keys == self.closes_on:
            self.element_info.element.closed = True


def make_ctx(save_enabled=False, keys=None):
    app = SimpleNamespace(windows=[])
    app.main = SimpleNamespace(element_info=SimpleNamespace(children=lambda: list(app.windows)))
    settings = SimpleNamespace(get=lambda key, default=None: keys if keys and key == "input.close_editor_keys"
                               else default)
    save = SimpleNamespace(element=SimpleNamespace(CurrentIsEnabled=save_enabled))
    return SimpleNamespace(app=app, settings=settings, results={"_save_button": save})


def test_closes_with_ctrl_f4_by_default():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app)
    assert close_saved_editor(ctx, anchor, "VAT") is True
    assert anchor.sent == ["^{F4}"]


def test_falls_back_to_next_configured_key():
    ctx = make_ctx(keys=["^{F4}", "%{F4}x"])
    anchor = FakeAnchor(ctx.app, closes_on="%{F4}x")
    assert close_saved_editor(ctx, anchor, "VAT") is True
    assert anchor.sent == ["^{F4}", "%{F4}x"]


def test_ignored_key_is_retried():
    """Live: Ctrl+F4 right after a save was ignored once; the same key a moment later closes."""
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app, closes_on=None)
    sent = anchor.type_keys

    def second_time_closes(keys, **kw):
        sent(keys, **kw)
        if len(anchor.sent) == 2:
            anchor.element_info.element.closed = True
    anchor.type_keys = second_time_closes
    assert close_saved_editor(ctx, anchor, "payment method") is True
    assert anchor.sent == ["^{F4}", "^{F4}"]


def test_key_that_changes_the_editor_stops_before_any_other_key():
    """The live failure: a close key typed a character -> editor dirty. Stop; never try Ctrl+F4 next
    (that would ask 'Save Parts')."""
    ctx = make_ctx(keys=["^w", "^{F4}"])
    anchor = FakeAnchor(ctx.app, closes_on="^{F4}", dirties_on="^w", save=ctx.results["_save_button"])
    with pytest.raises(UIError, match="changed the saved"):
        close_saved_editor(ctx, anchor, "payment method")
    assert anchor.sent == ["^w"]


def test_unsaved_editor_is_never_closed():
    ctx = make_ctx(save_enabled=True)
    anchor = FakeAnchor(ctx.app)
    assert close_saved_editor(ctx, anchor, "product") is False
    assert anchor.sent == []


def test_dialog_on_close_stops_the_run():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app, closes_on=None, dialog_on="^{F4}")
    with pytest.raises(UIError, match="dialog"):
        close_saved_editor(ctx, anchor, "debtor")


def test_no_key_works_leaves_editor_open_with_warning():
    ctx = make_ctx()
    anchor = FakeAnchor(ctx.app, closes_on=None)
    assert close_saved_editor(ctx, anchor, "payment method") is False
    assert "could not be closed" in ctx.results["warnings"][0]
    assert anchor.sent == ["^{F4}"] * 3          # retried, then left open


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
