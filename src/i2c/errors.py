"""Exception types shared across the project."""
from __future__ import annotations

from pathlib import Path
from typing import Any


class I2CError(Exception):
    """Base class for all project errors."""


class ManualReviewRequired(I2CError):
    """Stop the run: a human must decide (ambiguity, conflict, failed validation).

    The app is left as-is; the runner writes a summary pointing at `step` and `screenshot`.
    """

    def __init__(self, reason: str, step: str, screenshot: Path | None = None,
                 details: Any = None) -> None:
        super().__init__(f"[{step}] {reason}")
        self.reason = reason
        self.step = step
        self.screenshot = screenshot
        self.details = details


class ProviderError(I2CError):
    """The LLM provider call failed (auth, network, refusal, malformed response)."""


class ConfigError(I2CError):
    """Missing or invalid configuration (.env / settings.yaml)."""
