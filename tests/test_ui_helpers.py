"""wait / input / evidence helpers that need no real UI."""
from datetime import date

import pytest
from PIL import Image

from i2c.errors import UIError, UITimeout
from i2c.ui.evidence import Evidence, annotate, slug
from i2c.ui.geom import Rect
from i2c.ui.input import (escape_keys, format_display_date, parse_display_date, set_date,
                          set_date_by_arrows, set_text)
from i2c.ui.wait import wait_stable, wait_until


# ------------------------------------------------------------------ wait

def test_wait_until_returns_value_and_retries_exceptions():
    calls = iter([RuntimeError("stale"), None, "ok"])

    def cond():
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    assert wait_until(cond, timeout=2, interval=0.01) == "ok"


def test_wait_until_times_out_with_last_error():
    def boom():
        raise LookupError("not there")

    with pytest.raises(UITimeout) as ei:
        wait_until(boom, timeout=0.05, interval=0.01, desc="thing")
    assert "thing" in str(ei.value) and isinstance(ei.value.cause, LookupError)


def test_wait_stable_needs_two_equal_reads():
    reads = iter([1, 2, 3, 3, 9])
    assert wait_stable(lambda: next(reads), timeout=2, interval=0.01) == 3


# ------------------------------------------------------------------ input

class FakeValue:
    def __init__(self, ctrl):
        self.ctrl = ctrl

    @property
    def CurrentValue(self):  # noqa: N802 - mirrors UIA
        return self.ctrl.text

    def SetValue(self, v):  # noqa: N802
        if self.ctrl.value_pattern_broken:
            raise RuntimeError("read-only")
        self.ctrl.text = v


class FakeEdit:
    def __init__(self, text="", value_pattern_broken=False, transform=lambda s: s):
        self.text = text
        self.value_pattern_broken = value_pattern_broken
        self.transform = transform
        self.iface_value = FakeValue(self)
        self.typed = []

    focusable = True

    @property
    def element_info(self):
        edit = self

        class _El:
            @property
            def CurrentHasKeyboardFocus(self):  # noqa: N802
                return edit.focused

        class _Info:
            element = _El()
        return _Info()

    focused = False

    def set_focus(self):
        self.focused = self.focusable

    def click_input(self, **_):
        self.focused = self.focusable

    def type_keys(self, keys, **_):
        assert self.focused, "typed without keyboard focus"
        self.typed.append(keys)
        raw = keys.replace("^a{BACKSPACE}", "")
        for c in "+^%~(){}[]":
            raw = raw.replace("{%s}" % c, c)
        self.text = self.transform(raw)


def test_escape_keys():
    assert escape_keys("A+B (x) 50%") == "A{+}B {(}x{)} 50{%}"


def test_set_text_value_pattern():
    e = FakeEdit()
    assert set_text(e, "WEB-2026-0714-A17", timeout=0.1) == "WEB-2026-0714-A17"
    assert e.typed == []


def test_set_text_falls_back_to_keys():
    e = FakeEdit(value_pattern_broken=True)
    assert set_text(e, "a+b", timeout=0.1) == "a+b"
    assert e.typed == ["^a{BACKSPACE}a{+}b"]


def test_set_text_raises_when_value_never_sticks():
    e = FakeEdit(value_pattern_broken=True, transform=lambda s: s[:-1])
    with pytest.raises(UIError):
        set_text(e, "abc", timeout=0.05)


def test_dates_round_trip():
    d = date(2026, 7, 14)
    assert format_display_date(d) == "Jul 14, 2026"
    assert format_display_date(date(2026, 11, 7)) == "Nov 7, 2026"
    assert parse_display_date("Nov 7, 2026") == date(2026, 11, 7)
    assert parse_display_date("14.07.2026") == d
    assert parse_display_date("garbage") is None
    e = FakeEdit()
    assert set_date(e, d, timeout=0.1) == d


class FakeDateWidget:
    """Spinner date like Fakturama's: fields [month, day, year], shown as 'Nov 7, 2026'.
    UP/DOWN change the focused field (month wraps without touching the year; day clamps)."""

    import calendar as _cal

    def __init__(self, d, fields=("month", "day", "year")):
        self.d, self.fields, self.caret, self.keys = d, fields, 0, []
        self.iface_value = self

    @property
    def CurrentValue(self):  # noqa: N802
        return format_display_date(self.d)

    def rectangle(self):
        return Rect(100, 100, 300, 130)

    def click_input(self, coords=None):
        self.caret = 0

    def type_keys(self, keys, **_):
        import re
        self.keys.append(keys)
        for name, n in re.findall(r"\{(\w+)(?: (\d+))?\}", keys):
            for _ in range(int(n or 1)):
                if name == "RIGHT":
                    self.caret = min(self.caret + 1, len(self.fields) - 1)
                elif name == "LEFT":
                    self.caret = max(self.caret - 1, 0)
                elif name in ("UP", "DOWN"):
                    self._spin(1 if name == "UP" else -1)

    def _spin(self, step):
        f, d = self.fields[self.caret], self.d
        y, m, day = d.year, d.month, d.day
        if f == "year":
            y += step
        elif f == "month":
            m = (m - 1 + step) % 12 + 1
        else:
            day = (day - 1 + step) % self._cal.monthrange(y, m)[1] + 1
        day = min(day, self._cal.monthrange(y, m)[1])
        self.d = date(y, m, day)


@pytest.mark.parametrize("fields", [("month", "day", "year"), ("day", "month", "year"), ("year", "month", "day")])
def test_set_date_by_arrows_learns_field_order(fields):
    w = FakeDateWidget(date(2026, 11, 7), fields)
    assert set_date_by_arrows(w, date(2026, 7, 14), step_timeout=0.05) == date(2026, 7, 14)


def test_set_date_by_arrows_handles_year_change_and_day_clamp():
    w = FakeDateWidget(date(2026, 3, 31))
    assert set_date_by_arrows(w, date(2025, 2, 28), step_timeout=0.05) == date(2025, 2, 28)


class DeadWidget(FakeDateWidget):
    def _spin(self, step):
        pass


def test_set_date_by_arrows_fails_loudly_when_widget_ignores_keys():
    with pytest.raises(UIError):
        set_date_by_arrows(DeadWidget(date(2026, 11, 7)), date(2026, 7, 14), step_timeout=0.02)


# ------------------------------------------------------------------ evidence

def test_annotate_adds_banner_and_keeps_size():
    img = Image.new("RGB", (800, 600), "white")
    out = annotate(img, (100, 50), [Rect(200, 150, 300, 200)], "Order header verified")
    assert out.width == 800 and out.height > 600
    banner_h = out.height - 600
    m = 3 + 2  # outline width + margin, drawn outside the target
    assert out.getpixel((150, 100 - m + banner_h)) == (230, 0, 120)   # top edge, outside target
    assert out.getpixel((150, 100 + 2 + banner_h)) == (255, 255, 255)  # target interior untouched


def test_evidence_numbering_with_given_image(tmp_path):
    ev = Evidence(tmp_path)
    img = Image.new("RGB", (200, 100), "white")
    p1 = ev.shot("Open order", "editor open", image=img, origin=(0, 0))
    p2 = ev.shot("Set header!", "failed", ok=False, image=img, origin=(0, 0))
    ev.close()                                   # PNGs are written in the background
    assert p1.name == "01_open_order.png" and p2.name == "02_set_header.png" and p2.exists()
    assert slug("  ") == "step"


def test_keys_refused_without_focus():
    e = FakeEdit(value_pattern_broken=True)
    e.focusable = False
    with pytest.raises(UIError):
        set_text(e, "MAT-DESK-02", methods=("keys",), timeout=0.05)
    assert e.typed == []          # nothing typed blind
