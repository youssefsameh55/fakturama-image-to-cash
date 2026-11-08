"""Attach to the running Fakturama main window (UIA backend) and reach its dialogs."""
from __future__ import annotations

import ctypes
import logging
import re

from i2c.errors import ElementNotFound
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from i2c.ui.wait import wait_until

log = logging.getLogger(__name__)
_dpi_done = False


def set_dpi_awareness() -> None:
    """Per-monitor DPI awareness (D15) so UIA rects, screenshots and clicks share one pixel space.

    Must run before pywinauto/comtypes create any window-related objects.
    """
    global _dpi_done
    if _dpi_done:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:  # noqa: BLE001 - already set, or older Windows
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    _dpi_done = True


class FakturamaApp:
    """Handle on the running Fakturama. Launching is out of scope: start it before the run."""

    def __init__(self, title_regex: str = ".*Fakturama.*", timeout: float = 15.0) -> None:
        self.title_regex = title_regex
        self.timeout = timeout
        self.main = None  # pywinauto UIAWrapper of the main window

    def attach(self) -> FakturamaApp:
        set_dpi_awareness()
        from pywinauto import Desktop

        rx = re.compile(self.title_regex)

        def find_main():  # noqa: ANN202
            for w in Desktop(backend="uia").windows(top_level_only=True):
                info = w.element_info
                if rx.fullmatch(info.name or "") and info.class_name == "SWT_Window0":
                    return w
            return None

        self.main = wait_until(find_main, self.timeout, desc=f"Fakturama window {self.title_regex!r}")
        log.info("attached to %r (pid %s)", self.main.element_info.name, self.main.element_info.process_id)
        return self

    def focus(self) -> None:
        """Bring to front and maximise, so mouse clicks land on Fakturama and OCR sees it."""
        w = self._require()
        try:
            if w.get_show_state() != 3:  # SW_MAXIMIZE
                w.maximize()
        except Exception:  # noqa: BLE001
            log.debug("maximise not available", exc_info=True)
        w.set_focus()

    def rect(self) -> Rect:
        return Rect.from_uia(self._require().element_info.rectangle)

    def snapshot(self, root: object | None = None, max_depth: int = 25) -> Snapshot:
        return Snapshot.take(root or self._require(), max_depth)

    def find_first(self, control_type: str, name: str, root: object | None = None):  # noqa: ANN201
        """One control by type + exact name via UIA FindFirst (searched inside UIA, early exit;
        measured ~6 ms vs ~0.7-0.9 s for a full window snapshot of Fakturama). Control view,
        same tree as snapshots: inactive editors are not in it. Returns UIAElementInfo or None."""
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        from i2c.perf import timed

        u = IUIA()
        cond = u.iuia.CreateAndCondition(
            u.iuia.CreatePropertyCondition(u.UIA_dll.UIA_ControlTypePropertyId, u.known_control_types[control_type]),
            u.iuia.CreatePropertyCondition(u.UIA_dll.UIA_NamePropertyId, name))
        base = getattr(root, "element_info", root) or self._require().element_info
        with timed("uia_find_first"):
            el = base.element.FindFirst(u.tree_scope["descendants"], cond)
        return UIAElementInfo(el) if el else None

    def names(self, control_type: str) -> list[str]:
        """Names of all controls of one type (e.g. every TabItem), in one UIA FindAll with the
        Name cached - instead of a full-window snapshot just to list tab names."""
        from pywinauto.uia_defines import IUIA

        from i2c.perf import timed

        u = IUIA()
        cond = u.iuia.CreatePropertyCondition(u.UIA_dll.UIA_ControlTypePropertyId, u.known_control_types[control_type])
        request = u.iuia.CreateCacheRequest()
        request.AddProperty(u.UIA_dll.UIA_NamePropertyId)
        with timed("uia_names"):
            found = self._require().element_info.element.FindAllBuildCache(u.tree_scope["descendants"], cond, request)
        return [found.GetElement(i).CachedName or "" for i in range(found.Length)] if found else []

    def find_dialog(self, title: str):  # noqa: ANN201 - pywinauto wrapper or None
        """A modal dialog by exact title. Probe: SWT dialogs are child Windows of the main
        window; top-level windows of the same process are checked too.

        The top-level check uses plain Win32 (EnumWindows filtered by Fakturama's pid, visible,
        exact title) instead of a UIA walk over every window on the desktop: that walk made two
        cross-process calls per window and ran on every 0.1 s poll while a selector opened or
        closed (uncounted; product lines spent ~14 s outside UIA/OCR/waits)."""
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo

        from i2c.perf import timed

        main = self._require()
        with timed("uia_dialog_lookup"):
            for child in main.element_info.children():
                if child.control_type == "Window" and child.name == title:
                    return UIAWrapper(child)
            for hwnd in _top_level_windows(self._pid(), title):
                return UIAWrapper(UIAElementInfo(hwnd))
        return None

    def _pid(self) -> int:
        if getattr(self, "_cached_pid", None) is None:
            self._cached_pid = self._require().element_info.process_id
        return self._cached_pid

    def wait_dialog(self, title: str, timeout: float | None = None, ready: str | None = None):  # noqa: ANN201
        """Wait for a dialog; with `ready`, also for a visible control of that UIA type inside it
        (a window with the right title can exist a moment before its contents do)."""
        def found():  # noqa: ANN202
            dlg = self.find_dialog(title)
            if dlg is None or not ready:
                return dlg
            return dlg if self.snapshot(dlg, max_depth=12).find(ready) else None

        return wait_until(found, timeout or self.timeout, desc=f"dialog {title!r}")

    def wait_dialog_closed(self, title: str, timeout: float | None = None) -> None:
        wait_until(lambda: self.find_dialog(title) is None, timeout or self.timeout,
                   desc=f"dialog {title!r} to close")

    def _require(self):  # noqa: ANN202
        if self.main is None:
            raise ElementNotFound("not attached to Fakturama; call attach() first")
        return self.main


def _top_level_windows(pid: int, title: str) -> list[int]:
    """Handles of visible top-level windows of process `pid` whose title is exactly `title` (Win32)."""
    import win32gui
    import win32process

    found: list[int] = []

    def visit(hwnd: int, _: object) -> bool:
        if (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title
                and win32process.GetWindowThreadProcessId(hwnd)[1] == pid):
            found.append(hwnd)
        return True

    win32gui.EnumWindows(visit, None)
    return found
