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

    def find_dialog(self, title: str):  # noqa: ANN201 - pywinauto wrapper or None
        """A modal dialog by exact title. Probe: SWT dialogs are child Windows of the main
        window; top-level windows of the same process are checked too."""
        from pywinauto import Desktop
        from pywinauto.controls.uiawrapper import UIAWrapper

        main = self._require()
        for child in main.element_info.children():
            if child.control_type == "Window" and child.name == title:
                return UIAWrapper(child)
        pid = main.element_info.process_id
        for w in Desktop(backend="uia").windows(top_level_only=True):
            if w.element_info.process_id == pid and w.element_info.name == title:
                return w
        return None

    def wait_dialog(self, title: str, timeout: float | None = None):  # noqa: ANN201
        return wait_until(lambda: self.find_dialog(title), timeout or self.timeout,
                          desc=f"dialog {title!r}")

    def wait_dialog_closed(self, title: str, timeout: float | None = None) -> None:
        wait_until(lambda: self.find_dialog(title) is None, timeout or self.timeout,
                   desc=f"dialog {title!r} to close")

    def _require(self):  # noqa: ANN202
        if self.main is None:
            raise ElementNotFound("not attached to Fakturama; call attach() first")
        return self.main
