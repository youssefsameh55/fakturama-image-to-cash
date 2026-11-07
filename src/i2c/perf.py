"""Tiny run-wide timing counters (where does the time go?): UIA snapshots, OCR passes, evidence.

Reported in summary.json ("perf") and in the console summary at the end of every run.
"""
from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

_counts: dict[str, int] = defaultdict(int)
_seconds: dict[str, float] = defaultdict(float)


@contextmanager
def timed(name: str) -> Iterator[None]:
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _counts[name] += 1
        _seconds[name] += time.perf_counter() - t0


def count(name: str) -> None:
    _counts[name] += 1


def step_cost(before: dict[str, tuple[int, float]] | None = None) -> dict[str, tuple[int, float]]:
    """Totals of UIA calls, OCR passes and idle polling (sleeps inside bounded waits, i.e. time
    spent waiting for Fakturama); with `before`, the change since then (one step's cost)."""
    now = {"uia": (sum(c for k, c in _counts.items() if k.startswith("uia_")),
                   sum(s for k, s in _seconds.items() if k.startswith("uia_"))),
           "ocr": (_counts.get("ocr_pass", 0), _seconds.get("ocr_pass", 0.0)),
           "wait": (_counts.get("wait_idle", 0), _seconds.get("wait_idle", 0.0))}
    if before is None:
        return now
    return {k: (now[k][0] - before[k][0], now[k][1] - before[k][1]) for k in now}


def report() -> dict[str, dict[str, float]]:
    return {k: {"count": _counts[k], "seconds": round(_seconds[k], 2),
                "avg_ms": round(1000 * _seconds[k] / _counts[k], 1) if _counts[k] else 0.0}
            for k in sorted(_counts)}


def reset() -> None:
    _counts.clear()
    _seconds.clear()
