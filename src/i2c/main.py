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
    run.add_argument("image")
    run.add_argument("--from-json", help="skip extraction and load OrderData from this JSON file")
    run.add_argument("--stop-after", choices=STAGES)
    return ap


def cmd_extract(args: argparse.Namespace) -> int:
    from i2c.config import load_settings
    from i2c.extract import extract_order
    from i2c.extract.cache import ExtractionCache
    from i2c.extract.providers import make_provider
    from i2c.logging_setup import new_run_id, setup_logging

    settings = load_settings()
    run_dir = settings.artifacts_dir / new_run_id()
    setup_logging(run_dir)

    image = Path(args.image)
    if not image.is_file():
        log.error("image not found: %s", image)
        return EXIT_USAGE

    try:
        provider = make_provider(settings)
        result = extract_order(
            image,
            provider,
            cache=ExtractionCache(settings.cache_dir),
            max_attempts=settings.extraction_max_attempts,
            known_payment_methods=(settings.get("payment_method_codes") or {}).keys(),
            max_image_long_side=int(settings.get("extraction.max_image_long_side", 1568)),
            use_cache=not args.no_cache,
        )
    except ManualReviewRequired as exc:
        log.error("MANUAL REVIEW: %s", exc)
        for d in exc.details or []:
            log.error("  %s", d)
        (run_dir / "summary.json").write_text(json.dumps(
            {"status": "manual_review", "step": exc.step, "reason": exc.reason, "details": exc.details},
            indent=2), encoding="utf-8")
        return EXIT_MANUAL_REVIEW
    except (ProviderError, ConfigError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return EXIT_ERROR

    data = result.order.to_json_dict()
    out = run_dir / "extracted.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "extraction_meta.json").write_text(json.dumps(
        {**result.meta, "warnings": [str(i) for i in result.issues]}, indent=2), encoding="utf-8")

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    m = result.meta
    log.info("provider=%s model=%s cache_hit=%s attempts=%d tokens in/out=%d/%d latency=%.2fs",
             m["provider"], m["model"], m["cache_hit"], m["attempts"],
             m["input_tokens"], m["output_tokens"], m["latency_s"])
    log.info("saved %s", out)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "probe":
        print("Use: python tools/dump_tree.py --list | --window <regex> --out artifacts/probe/<name>")
        return EXIT_USAGE
    args = build_parser().parse_args(argv)
    if args.cmd == "extract":
        return cmd_extract(args)
    if args.cmd == "run":
        print("run: not implemented yet (milestones M4-M7)")
        return EXIT_USAGE
    return EXIT_USAGE
