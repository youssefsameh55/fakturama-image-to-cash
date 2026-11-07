"""Input helpers (D10): every setter reads the value back and fails loudly if it didn't stick.

Text: UIA ValuePattern first, then keyboard (focus, select all, type). The order is configurable
(`input.text_methods`) because SWT may only commit data-bound values on real key input.
Clicks use the real mouse at the centre of the UIA rectangle: UIA Invoke on an SWT button that
opens a modal dialog can block until the dialog closes.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from i2c.errors import UIError, UITimeout
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)

_SENDKEYS_SPECIAL = set("+^%~(){}[]")
KEY_PAUSE = 0.01   # s between keystrokes (pywinauto default 0.05 = 1 s per 20 chars); set from settings


def escape_keys(text: str) -> str:
    """Escape pywinauto send_keys metacharacters so `text` is typed literally."""
    return "".join("{%s}" % c if c in _SENDKEYS_SPECIAL else c for c in text)


def get_value(ctrl: object) -> str:
    """Current text of an Edit/ComboBox via ValuePattern, falling back to window text."""
    try:
        return str(ctrl.iface_value.CurrentValue or "")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        try:
            return str(ctrl.window_text() or "")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return ""


def click(ctrl: object) -> None:
    """Real mouse click at the centre of the control's UIA rectangle."""
    ctrl.click_input()  # type: ignore[attr-defined]


def _set_via_value(ctrl: object, text: str) -> None:
    ctrl.iface_value.SetValue(text)  # type: ignore[attr-defined]


def has_focus(ctrl: object) -> bool:
    try:
        return bool(ctrl.element_info.element.CurrentHasKeyboardFocus)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return False


def ensure_focus(ctrl: object, timeout: float = 1.5) -> None:
    """Give `ctrl` keyboard focus and PROVE it before any keystroke is sent.

    Live: keys meant for a dialog's search box landed in the order's Items table (which then
    added a product line by its item number). Typing blind is never allowed: focus via UIA,
    else a click into the control, else stop.
    """
    if has_focus(ctrl):
        return
    for attempt in ("focus", "click"):
        try:
            if attempt == "focus":
                ctrl.set_focus()  # type: ignore[attr-defined]
            else:
                ctrl.click_input()  # type: ignore[attr-defined]
            wait_until(lambda: has_focus(ctrl), timeout / 3 if attempt == "focus" else timeout,
                       interval=0.05, desc="keyboard focus")
            return
        except Exception:  # noqa: BLE001
            continue
    raise UIError("control did not take keyboard focus; refusing to type blind")


def _set_via_keys(ctrl: object, text: str) -> None:
    ensure_focus(ctrl)
    # set_foreground=False: bringing the top-level window forward can move focus elsewhere.
    ctrl.type_keys("^a{BACKSPACE}" + escape_keys(text), with_spaces=True,  # type: ignore[attr-defined]
                   with_tabs=False, with_newlines=False, set_foreground=False, pause=KEY_PAUSE)


_TEXT_METHODS = {"value": _set_via_value, "keys": _set_via_keys}


def set_text(ctrl: object, text: str, *, methods: tuple[str, ...] = ("value", "keys"),
             timeout: float = 3.0, normalize=None) -> str:  # noqa: ANN001
    """Set `text`, verify by read-back; try the next method if it didn't stick.

    `normalize(read_back) -> str` lets callers compare formatted values (e.g. '0%').
    Returns the read-back value.
    """
    norm = normalize or (lambda s: s)
    errors: list[str] = []

    def stuck() -> str | None:
        value = get_value(ctrl)
        return (value or text) if norm(value) == norm(text) else None

    for method in methods:
        try:
            _TEXT_METHODS[method](ctrl, text)
            return wait_until(stuck, timeout, desc=f"text {text!r} to stick")
        except UITimeout:
            errors.append(f"{method}: read back {get_value(ctrl)!r}")
        except Exception as exc:  # noqa: BLE001 - pattern unsupported, focus refused...
            errors.append(f"{method}: {exc!r}")
        log.debug("set_text via %s failed, trying next: %s", method, errors[-1])
    raise UIError(f"could not set text {text!r}: " + "; ".join(errors))


def _collapse(ctrl: object) -> None:
    try:
        if ctrl.is_expanded():  # type: ignore[attr-defined]
            ctrl.collapse()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _select_by_arrows(ctrl: object, text: str, max_items: int, step_timeout: float) -> bool:
    """Keyboard fallback for read-only combos: HOME, then DOWN until the value equals `text`.
    Every step is verified by reading the value; the visited values are what the combo offers."""
    ctrl.set_focus()  # type: ignore[attr-defined]
    ctrl.type_keys("{HOME}", set_foreground=True)  # type: ignore[attr-defined]
    seen: list[str] = []
    for _ in range(max_items):
        value = get_value(ctrl)
        if value.strip() == text.strip():
            return True
        if seen and value == seen[-1]:
            break  # end of list: DOWN no longer changes the value
        seen.append(value)
        ctrl.type_keys("{DOWN}", set_foreground=True)  # type: ignore[attr-defined]
        try:
            wait_until(lambda: get_value(ctrl) != value, step_timeout, desc="combo to move")
        except UITimeout:
            break
    log.info("combo items seen while searching %r: %s", text, seen)
    return _combo_is(ctrl, text)


def _combo_is(ctrl: object, text: str) -> bool:
    """Exact item match, ignoring surrounding whitespace (probe: 'Mutually defined ')."""
    return get_value(ctrl).strip() == text.strip()


def select_combo(ctrl: object, text: str, timeout: float = 5.0, max_items: int = 300) -> str:
    """Select the item whose text equals `text` (trimmed); verify via the combo's value.

    1. pywinauto select (expand + pick list item)
    2. type the text: a Windows combo jumps to the first item with that prefix (long lists
       such as Country), verified by read-back
    3. arrow-key walk from the top (read-only SWT combos), verified per step
    """
    if _combo_is(ctrl, text):
        return get_value(ctrl)
    errors: list[str] = []
    try:
        ctrl.select(text)  # type: ignore[attr-defined]
        wait_until(lambda: _combo_is(ctrl, text), timeout, desc=f"combo value {text!r}")
        _collapse(ctrl)
        return get_value(ctrl)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"select: {exc!r}")
        _collapse(ctrl)
    try:
        ctrl.set_focus()  # type: ignore[attr-defined]
        ctrl.type_keys(escape_keys(text), with_spaces=True, set_foreground=True)  # type: ignore[attr-defined]
        wait_until(lambda: _combo_is(ctrl, text), 1.5, desc=f"combo value {text!r} after typing")
        return get_value(ctrl)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"typing: {exc!r}")
    try:
        if _select_by_arrows(ctrl, text, max_items, step_timeout=1.0):
            return get_value(ctrl)
        errors.append("arrows: item not found")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"arrows: {exc!r}")
    raise UIError(f"combo did not take {text!r} (now {get_value(ctrl)!r}; {errors})")


def combo_items(ctrl: object, max_items: int = 300) -> list[str]:
    """All items of a combo, read by walking it with the keyboard (restores nothing: callers
    select afterwards). Used to give a precise error when an exact item is missing."""
    ctrl.set_focus()  # type: ignore[attr-defined]
    ctrl.type_keys("{HOME}", set_foreground=True)  # type: ignore[attr-defined]
    items: list[str] = []
    for _ in range(max_items):
        value = get_value(ctrl)
        if items and value == items[-1]:
            break
        items.append(value)
        ctrl.type_keys("{DOWN}", set_foreground=True)  # type: ignore[attr-defined]
        try:
            wait_until(lambda: get_value(ctrl) != value, 0.7, desc="combo to move")
        except UITimeout:
            break
    return items


def select_tab(ctrl: object) -> bool:
    """Try to activate a TabItem through accessibility APIs. Returns False if none works.

    Live finding: SWT CTabItems answer SelectionItem.Select() with 'Member not found', and
    their UIA rectangles are in unscaled coordinates, so callers fall back to clicking the
    tab label found by OCR in the tab bar (flow.common.activate_tab).
    """
    for attempt in (lambda: ctrl.iface_selection_item.Select(),            # type: ignore[attr-defined]
                    lambda: ctrl.iface_legacy_iaccessible.DoDefaultAction()):  # type: ignore[attr-defined]
        try:
            attempt()
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def get_checked(ctrl: object) -> bool:
    return bool(ctrl.get_toggle_state())  # type: ignore[attr-defined]


def set_checkbox(ctrl: object, checked: bool, timeout: float = 3.0) -> None:
    if get_checked(ctrl) == checked:
        return
    try:
        ctrl.toggle()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        click(ctrl)
    try:
        wait_until(lambda: get_checked(ctrl) == checked, timeout, desc=f"checkbox={checked}")
    except UITimeout as exc:
        raise UIError(f"checkbox did not become {checked}") from exc


# ------------------------------------------------------------------ dates

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_display_date(d: date, pattern: str = "{mon} {day}, {year}") -> str:
    """Locale-independent date text as Fakturama shows it (probe: 'Nov 7, 2026')."""
    return pattern.format(mon=_MONTHS[d.month - 1], day=d.day, dd=f"{d.day:02d}",
                          mm=f"{d.month:02d}", m=d.month, year=d.year)


def parse_display_date(text: str) -> date | None:
    """Parse the date formats Fakturama may display; None if unrecognised."""
    text = " ".join(text.split())
    for fmt in ("%b %d, %Y", "%d.%m.%Y", "%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def set_date(ctrl: object, d: date, *, pattern: str = "{mon} {day}, {year}",
             methods: tuple[str, ...] = ("value", "keys"), timeout: float = 3.0) -> date:
    """Set a plain-text date field and verify the read-back parses to the same date."""
    text = format_display_date(d, pattern)
    set_text(ctrl, text, methods=methods, timeout=timeout,
             normalize=lambda s: parse_display_date(s) or s)
    got = parse_display_date(get_value(ctrl))
    if got != d:
        raise UIError(f"date field shows {get_value(ctrl)!r}, expected {d.isoformat()}")
    return got


# A spinner-style date widget (Fakturama's Date fields): the text can be replaced through UIA
# but the widget redraws from its own model a moment later (seen live: 'Jul 14, 2026' set,
# 'Nov 7, 2026' back). It is driven like a user would: arrow keys per date field.

_PARTS = ("year", "month", "day")


def _date_or_fail(ctrl: object) -> date:
    got = parse_display_date(get_value(ctrl))
    if got is None:
        raise UIError(f"date field shows unparseable {get_value(ctrl)!r}")
    return got


def _press_and_read(ctrl: object, keys: str, before: date, step_timeout: float) -> date:
    ctrl.type_keys(keys, set_foreground=False)  # type: ignore[attr-defined]
    try:
        return wait_until(lambda: (v := _date_or_fail(ctrl)) != before and v, step_timeout,
                          interval=0.05, desc=f"date to change after {keys}")
    except UITimeout:
        return _date_or_fail(ctrl)


def _changed_part(a: date, b: date) -> str | None:
    """Which field was spun. A month step can clamp the day (Mar 31 -> Apr 30) or roll the
    year (Dec -> Jan), so 'month changed' wins; otherwise exactly one part must differ."""
    if a.month != b.month:
        return "month"
    diff = [p for p in _PARTS if getattr(a, p) != getattr(b, p)]
    return diff[0] if len(diff) == 1 else None


def set_date_by_arrows(ctrl: object, target: date, *, max_fields: int = 4,
                       step_timeout: float = 1.0, max_rounds: int = 3, max_steps: int = 400) -> date:
    """Set a field-based date widget with the keyboard and verify after every step.

    1. Click into the leftmost field (left edge of the control's UIA rect, not a fixed position).
    2. Calibrate: for each field, UP -> see which part (year/month/day) changed -> DOWN to
       undo -> RIGHT. This learns the field order instead of assuming a locale format.
    3. Year, then month, then day (so day clamping can't bite): one UP/DOWN at a time, each
       verified, until the part matches. Repeated rounds absorb roll-overs (e.g. month wrap).
    """
    def to_field(index: int) -> None:
        r = ctrl.rectangle()  # type: ignore[attr-defined]
        ctrl.click_input(coords=(6, (r.bottom - r.top) // 2))  # type: ignore[attr-defined]
        if index:
            ctrl.type_keys("{RIGHT %d}" % index, set_foreground=False)  # type: ignore[attr-defined]

    to_field(0)
    current = _date_or_fail(ctrl)
    if current == target:
        return current

    order: list[str] = []
    for _ in range(max_fields):
        up = _press_and_read(ctrl, "{UP}", current, step_timeout)
        part = _changed_part(current, up)
        if part is None:
            break
        back = _press_and_read(ctrl, "{DOWN}", up, step_timeout)
        if getattr(back, part) != getattr(current, part):
            raise UIError(f"date widget did not undo UP on {part} ({current} -> {up} -> {back})")
        current = back  # may differ in a clamped day; the adjust phase fixes every part anyway
        if part in order:
            break  # RIGHT did not move any further: last field reached
        order.append(part)
        ctrl.type_keys("{RIGHT}", set_foreground=False)  # type: ignore[attr-defined]
    if not order:
        raise UIError("date widget did not react to arrow keys")
    log.debug("date widget field order: %s", order)

    for _ in range(max_rounds):
        for part in [p for p in _PARTS if p in order]:
            to_field(order.index(part))
            for _ in range(max_steps):
                current = _date_or_fail(ctrl)
                delta = _part_delta(current, target, part)
                if delta == 0:
                    break
                after = _press_and_read(ctrl, "{UP}" if delta > 0 else "{DOWN}", current, step_timeout)
                if after == current:
                    raise UIError(f"date widget stopped reacting while setting {part}")
        if _date_or_fail(ctrl) == target:
            break
    got = _date_or_fail(ctrl)
    if got != target:
        raise UIError(f"date widget shows {got.isoformat()}, expected {target.isoformat()} "
                      f"(field order {order})")
    return got


def _part_delta(current: date, target: date, part: str) -> int:
    return getattr(target, part) - getattr(current, part)
