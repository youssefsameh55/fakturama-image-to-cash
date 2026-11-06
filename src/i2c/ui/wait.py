"""Polling waits (D9). The only sanctioned way to synchronise with the UI: no bare sleeps."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from i2c.errors import UITimeout

T = TypeVar("T")
log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 0.25


def wait_until(condition: Callable[[], T], timeout: float, interval: float = DEFAULT_INTERVAL,
               desc: str = "condition") -> T:
    """Poll `condition` until it returns a truthy value; return that value.

    Exceptions raised by `condition` (stale UIA elements, window not there yet) count as
    "not yet" and are retried; the last one is attached to the timeout error.
    """
    deadline = time.monotonic() + timeout
    last_exc: BaseException | None = None
    while True:
        try:
            value = condition()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - UIA raises many types while the UI settles
            last_exc = exc
        if time.monotonic() >= deadline:
            raise UITimeout(f"timed out after {timeout:.1f}s waiting for {desc}", last_exc)
        time.sleep(interval)  # polling interval inside a bounded wait, not a blind sleep


def wait_stable(read: Callable[[], T], timeout: float, interval: float = DEFAULT_INTERVAL,
                equal_reads: int = 2, desc: str = "stable value") -> T:
    """Read repeatedly until `equal_reads` consecutive reads are equal; return that value.

    Used for tables that refresh after typing in a search box (D9: 2 identical OCR reads).
    """
    deadline = time.monotonic() + timeout
    previous: object = _MISSING
    streak = 0
    last_exc: BaseException | None = None
    while True:
        try:
            value = read()
            streak = streak + 1 if value == previous else 1
            previous = value
            if streak >= equal_reads:
                return value
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            streak = 0
            previous = _MISSING
        if time.monotonic() >= deadline:
            raise UITimeout(f"timed out after {timeout:.1f}s waiting for {desc}", last_exc)
        time.sleep(interval)


_MISSING = object()
