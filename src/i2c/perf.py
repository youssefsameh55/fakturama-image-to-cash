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


def report() -> dict[str, dict[str, float]]:
    return {k: {"count": _counts[k], "seconds": round(_seconds[k], 2),
                "avg_ms": round(1000 * _seconds[k] / _counts[k], 1) if _counts[k] else 0.0}
            for k in sorted(_counts)}


def reset() -> None:
    _counts.clear()
    _seconds.clear()
