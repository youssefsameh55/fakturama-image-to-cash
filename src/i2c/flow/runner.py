"""Runs stages of steps. A step = action + verification (D11); the runner adds evidence & summary.

A step function takes the RunContext, does its action, verifies the outcome, and returns a short
message describing what was verified. It may set `ctx.targets` to outline controls in the
screenshot. Any exception stops the run: ManualReviewRequired -> exit 3, anything else -> exit 4.
The app is always left as-is for a human to inspect.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from i2c.errors import ManualReviewRequired
from i2c.flow.context import RunContext

log = logging.getLogger(__name__)

EXIT_OK, EXIT_MANUAL_REVIEW, EXIT_ERROR = 0, 3, 4


@dataclass(frozen=True)
class Step:
    name: str
    fn: Callable[[RunContext], str]


def run_flow(ctx: RunContext, stages: list[tuple[str, list[Step]]], stop_after: str | None,
             not_implemented: tuple[str, ...] = ()) -> int:
    started = time.monotonic()
    summary: dict = {"status": "running", "started": datetime.now().isoformat(timespec="seconds"),
                     "steps": [], "stages_done": []}
    code = EXIT_OK
    try:
        for stage, steps in stages:
            if stage in not_implemented:
                log.warning("stage %r is not implemented yet - stopping here", stage)
                summary["status"] = "incomplete"
                summary["stopped_at"] = stage
                break
            log.info("=== stage %s", stage)
            for step in steps:
                _run_step(ctx, stage, step, summary)
            summary["stages_done"].append(stage)
            if stage == stop_after:
                log.info("--stop-after %s reached", stage)
                break
        if summary["status"] == "running":
            summary["status"] = "ok"
    except ManualReviewRequired as exc:
        code = EXIT_MANUAL_REVIEW
        summary.update(status="manual_review", step=exc.step, reason=exc.reason, details=exc.details)
        log.error("MANUAL REVIEW at %s: %s", exc.step, exc.reason)
    except Exception as exc:  # noqa: BLE001 - every failure must end in a summary
        code = EXIT_ERROR
        summary.update(status="error", step=ctx.current_step, reason=f"{type(exc).__name__}: {exc}")
        log.exception("step %s failed", ctx.current_step)
    finally:
        summary["duration_s"] = round(time.monotonic() - started, 1)
        summary["results"] = ctx.results
        (ctx.run_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        log.info("summary: %s -> %s", summary["status"], ctx.run_dir / "summary.json")
    return code


def _run_step(ctx: RunContext, stage: str, step: Step, summary: dict) -> None:
    ctx.current_step = f"{stage}.{step.name}"
    ctx.targets = []
    t0 = time.monotonic()
    log.info("--- %s", ctx.current_step)
    record = {"step": ctx.current_step}
    try:
        message = step.fn(ctx)
    except Exception as exc:
        record.update(status="failed", error=f"{type(exc).__name__}: {exc}",
                      duration_s=round(time.monotonic() - t0, 2))
        shot = ctx.evidence.shot(ctx.current_step, f"{ctx.current_step}: {exc}"[:200], ctx.targets, ok=False)
        record["screenshot"] = shot.name if shot else None
        if isinstance(exc, ManualReviewRequired) and exc.screenshot is None:
            exc.screenshot = shot
        summary["steps"].append(record)
        raise
    shot = ctx.evidence.shot(ctx.current_step, f"{ctx.current_step}: {message}", ctx.targets, ok=True)
    record.update(status="ok", message=message, duration_s=round(time.monotonic() - t0, 2),
                  screenshot=shot.name if shot else None)
    summary["steps"].append(record)
    log.info("ok %s: %s", ctx.current_step, message)
