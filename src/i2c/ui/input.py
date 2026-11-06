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


def _set_via_keys(ctrl: object, text: str) -> None:
    ctrl.set_focus()  # type: ignore[attr-defined]
    ctrl.type_keys("^a{BACKSPACE}" + escape_keys(text), with_spaces=True,  # type: ignore[attr-defined]
                   with_tabs=False, with_newlines=False, set_foreground=True)


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
        if value == text:
            return True
        if seen and value == seen[-1]:
            break  # end of list: DOWN no longer changes the value
        seen.append(value)
        ctrl.type_keys("{DOWN}", set_foreground=True)  # type: ignore[attr-defined]
        try:
            wait_until(lambda: get_value(ctrl) != value, step_timeout, desc="combo to move")
        except UITimeout:
            break
    log.debug("combo items seen while searching %r: %s", text, seen)
    return get_value(ctrl) == text


def select_combo(ctrl: object, text: str, timeout: float = 5.0, max_items: int = 60) -> str:
    """Select the item whose text equals `text` exactly; verify via the combo's value.

    1. pywinauto select (expand + pick list item), 2. arrow-key walk (read-only SWT combos).
    """
    if get_value(ctrl) == text:
        return text
    errors: list[str] = []
    try:
        ctrl.select(text)  # type: ignore[attr-defined]
        wait_until(lambda: get_value(ctrl) == text, timeout, desc=f"combo value {text!r}")
        _collapse(ctrl)
        return text
    except Exception as exc:  # noqa: BLE001
        errors.append(f"select: {exc!r}")
        _collapse(ctrl)
    try:
        if _select_by_arrows(ctrl, text, max_items, step_timeout=1.0):
            return text
        errors.append("arrows: item not found")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"arrows: {exc!r}")
    raise UIError(f"combo did not take {text!r} (now {get_value(ctrl)!r}; {errors})")


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
    """Set a date field and verify the read-back parses to the same date."""
    text = format_display_date(d, pattern)
    set_text(ctrl, text, methods=methods, timeout=timeout,
             normalize=lambda s: parse_display_date(s) or s)
    got = parse_display_date(get_value(ctrl))
    if got != d:
        raise UIError(f"date field shows {get_value(ctrl)!r}, expected {d.isoformat()}")
    return got
