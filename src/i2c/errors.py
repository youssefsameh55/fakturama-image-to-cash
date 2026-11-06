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


class UIError(I2CError):
    """A UI interaction failed technically (control missing, value did not stick)."""


class UITimeout(UIError):
    """A polling wait expired. `cause` is the last exception the condition raised, if any."""

    def __init__(self, message: str, cause: BaseException | None = None) -> None:
        super().__init__(message if cause is None else f"{message} (last error: {cause!r})")
        self.cause = cause


class ElementNotFound(UIError):
    """No control matched the query."""


class AmbiguousElement(UIError):
    """More than one control matched a query that must be unique."""
