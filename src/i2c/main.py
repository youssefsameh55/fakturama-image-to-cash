"""CLI entry point: extract | probe | run."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from i2c.errors import ConfigError, ManualReviewRequired, ProviderError

STAGES = ("order", "debtor", "products", "save_order", "invoice")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MANUAL_REVIEW = 3
EXIT_ERROR = 4

log = logging.getLogger("i2c")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="i2c", description="Fakturama Image-to-Cash")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="extract order data from an image (LLM, cached)")
    ex.add_argument("image")
    ex.add_argument("--no-cache", action="store_true", help="ignore the extraction cache")

    sub.add_parser("probe", help="UIA probe (alias for tools/dump_tree.py)", add_help=False)

    run = sub.add_parser("run", help="full flow against a running Fakturama")
    run.add_argument("image", nargs="?", default="samples/order_01.png")
    run.add_argument("--from-json", help="skip extraction and load OrderData from this JSON file")
    run.add_argument("--stop-after", choices=STAGES)
    run.add_argument("--start-at", choices=STAGES,
                     help="skip earlier stages and continue in the editors that are already open "
                          "(development: re-run one stage without redoing the previous ones)")
    return ap


# ------------------------------------------------------------------ order data

def _extract(settings, image: Path, run_dir: Path, use_cache: bool = True):  # noqa: ANN001, ANN202
    """LLM extraction (cached), saved to run_dir. Raises ManualReviewRequired/ProviderError/ConfigError."""
    from i2c.extract import extract_order
    from i2c.extract.cache import ExtractionCache
    from i2c.extract.providers import make_provider

    result = extract_order(
        image,
        make_provider(settings),
        cache=ExtractionCache(settings.cache_dir),
        max_attempts=settings.extraction_max_attempts,
        known_payment_methods=(settings.get("payment_method_codes") or {}).keys(),
        max_image_long_side=int(settings.get("extraction.max_image_long_side", 1568)),
        use_cache=use_cache,
    )
    (run_dir / "extracted.json").write_text(
        json.dumps(result.order.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "extraction_meta.json").write_text(json.dumps(
        {**result.meta, "warnings": [str(i) for i in result.issues]}, indent=2), encoding="utf-8")
    return result


def _load_order_json(settings, path: Path, run_dir: Path):  # noqa: ANN001, ANN202
    """--from-json: the same deterministic validation as a fresh extraction, without the LLM."""
    from pydantic import ValidationError

    from i2c.extract.validate import errors, schema_issues, validate
    from i2c.models import OrderData

    data = json.loads(path.read_text(encoding="utf-8"))
    order = None
    try:
        order = OrderData.model_validate(data)
        issues = validate(order, known_payment_methods=(settings.get("payment_method_codes") or {}).keys())
    except ValidationError as exc:
        issues = schema_issues(exc)
    if order is None or errors(issues):
        raise ManualReviewRequired(f"{path} does not validate", "load_json",
                                   details=[str(i) for i in issues])
    for issue in issues:
        log.warning("extraction warning: %s", issue)
    (run_dir / "extracted.json").write_text(
        json.dumps(order.to_json_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return order


def _write_manual_review(run_dir: Path, exc: ManualReviewRequired) -> None:
    log.error("MANUAL REVIEW: %s", exc)
    for d in exc.details or []:
        log.error("  %s", d)
    (run_dir / "summary.json").write_text(json.dumps(
        {"status": "manual_review", "step": exc.step, "reason": exc.reason, "details": exc.details},
        indent=2, default=str), encoding="utf-8")


# ------------------------------------------------------------------ commands

def cmd_extract(args: argparse.Namespace) -> int:
    from i2c.config import load_settings
    from i2c.logging_setup import new_run_id, setup_logging

    settings = load_settings()
    run_dir = settings.artifacts_dir / new_run_id()
    setup_logging(run_dir)

    image = Path(args.image)
    if not image.is_file():
        log.error("image not found: %s", image)
        return EXIT_USAGE

    try:
        result = _extract(settings, image, run_dir, use_cache=not args.no_cache)
    except ManualReviewRequired as exc:
        _write_manual_review(run_dir, exc)
        return EXIT_MANUAL_REVIEW
    except (ProviderError, ConfigError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return EXIT_ERROR

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result.order.to_json_dict(), indent=2, ensure_ascii=False))
    m = result.meta
    log.info("provider=%s model=%s cache_hit=%s attempts=%d tokens in/out=%d/%d latency=%.2fs",
             m["provider"], m["model"], m["cache_hit"], m["attempts"],
             m["input_tokens"], m["output_tokens"], m["latency_s"])
    log.info("saved %s", run_dir / "extracted.json")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    from i2c.ui.app import FakturamaApp, set_dpi_awareness

    set_dpi_awareness()  # before any UIA / capture call

    from i2c.config import load_settings
    from i2c.flow import debtor as debtor_stage
    from i2c.flow import order as order_stage
    from i2c.flow.context import RunContext
    from i2c.flow.runner import run_flow
    from i2c.logging_setup import new_run_id, setup_logging
    from i2c.ui.evidence import Evidence

    settings = load_settings()
    run_dir = settings.artifacts_dir / new_run_id()
    setup_logging(run_dir)

    try:
        if args.from_json:
            order = _load_order_json(settings, Path(args.from_json), run_dir)
            log.info("order data loaded from %s (no LLM call)", args.from_json)
        else:
            image = Path(args.image)
            if not image.is_file():
                log.error("image not found: %s", image)
                return EXIT_USAGE
            order = _extract(settings, image, run_dir).order
    except ManualReviewRequired as exc:
        _write_manual_review(run_dir, exc)
        return EXIT_MANUAL_REVIEW
    except (ProviderError, ConfigError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return EXIT_ERROR

    app = FakturamaApp(settings.window_title, float(settings.get("timeouts.window", 15))).attach()
    app.focus()
    ctx = RunContext(settings, order, app, Evidence(run_dir, area=app.rect), run_dir)

    implemented = {"order": order_stage.STEPS, "debtor": debtor_stage.STEPS}
    names = STAGES[STAGES.index(args.start_at):] if args.start_at else STAGES
    if args.start_at:
        log.warning("starting at stage %r: earlier stages are assumed done in the open editors", args.start_at)
    stages = [(name, implemented.get(name, [])) for name in names]
    missing = tuple(name for name in names if name not in implemented)
    return run_flow(ctx, stages, args.stop_after, not_implemented=missing)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "probe":
        print("Use: python tools/dump_tree.py --list | --window <regex> --out artifacts/probe/<name>")
        return EXIT_USAGE
    args = build_parser().parse_args(argv)
    if args.cmd == "extract":
        return cmd_extract(args)
    if args.cmd == "run":
        return cmd_run(args)
    return EXIT_USAGE
