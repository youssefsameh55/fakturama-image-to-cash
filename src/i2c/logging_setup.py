"""Logging to console and to artifacts/<run-id>/run.log."""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def setup_logging(run_dir: Path | None = None, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    for noisy in ("httpx", "httpcore", "anthropic", "google_genai", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
