"""Extraction service: cache -> provider -> parse -> validate -> (one retry with feedback) -> result."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from i2c.errors import ManualReviewRequired
from i2c.extract import prompt
from i2c.extract.cache import ExtractionCache, cache_key
from i2c.extract.providers import ExtractionProvider, prepare_image
from i2c.extract.validate import Issue, errors, schema_issues, validate
from i2c.models import OrderData

log = logging.getLogger(__name__)

STEP = "extract"


@dataclass
class ExtractionResult:
    order: OrderData
    issues: list[Issue] = field(default_factory=list)   # warnings only (errors never get here)
    meta: dict = field(default_factory=dict)


def _check(data: dict, known_methods: Iterable[str]) -> tuple[OrderData | None, list[Issue]]:
    try:
        order = OrderData.model_validate(data)
    except ValidationError as exc:
        return None, schema_issues(exc)
    return order, validate(order, known_payment_methods=known_methods)


def extract_order(image_path: Path,
                  provider: ExtractionProvider,
                  cache: ExtractionCache | None = None,
                  max_attempts: int = 2,
                  known_payment_methods: Iterable[str] = (),
                  max_image_long_side: int = 1568,
                  use_cache: bool = True) -> ExtractionResult:
    known = tuple(known_payment_methods)
    image = prepare_image(Path(image_path), max_image_long_side)
    key = cache_key(image.sha256, provider.name, provider.model, prompt.PROMPT_VERSION)
    meta = {
        "image": str(image_path), "image_sha256": image.sha256,
        "provider": provider.name, "model": provider.model, "prompt_version": prompt.PROMPT_VERSION,
        "cache_key": key, "cache_hit": False, "attempts": 0,
        "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0,
    }

    if cache is not None and use_cache:
        cached = cache.get(key)
        if cached is not None:
            order, issues = _check(cached.get("order", {}), known)
            if order is not None and not errors(issues):
                meta["cache_hit"] = True
                log.info("extraction cache hit (%s)", key[:12])
                return ExtractionResult(order, issues, meta)
            log.warning("cached extraction %s no longer validates; re-extracting", key[:12])

    feedback: str | None = None
    issues: list[Issue] = []
    for attempt in range(1, max_attempts + 1):
        meta["attempts"] = attempt
        result = provider.extract(image, feedback)
        meta["input_tokens"] += result.input_tokens
        meta["output_tokens"] += result.output_tokens
        meta["latency_s"] = round(meta["latency_s"] + result.latency_s, 3)
        log.info("extraction attempt %d: %s/%s in=%d out=%d %.2fs", attempt, provider.name, provider.model,
                 result.input_tokens, result.output_tokens, result.latency_s)

        order, issues = _check(result.data, known)
        errs = errors(issues)
        if order is not None and not errs:
            if cache is not None:
                cache.put(key, {"order": order.to_json_dict(), "meta": meta})
            for w in issues:
                log.warning("extraction warning: %s", w)
            return ExtractionResult(order, issues, meta)

        for e in errs:
            log.warning("attempt %d: %s", attempt, e)
        feedback = prompt.retry_feedback(errs, result.data)

    summary = "; ".join(f"{e.code}: {e.message}" for e in errors(issues))
    raise ManualReviewRequired(
        f"extraction failed validation after {max_attempts} attempt(s): {summary}",
        step=STEP,
        details=[str(i) for i in issues],
    )
