"""CLI entry point: extract | probe | run."""
from __future__ import annotations

import argparse
import sys

STAGES = ("order", "debtor", "products", "save_order", "invoice")


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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "probe":
        print("Use: python tools/dump_tree.py --list | --window <regex> --out artifacts/probe/<name>")
        return 2
    args = build_parser().parse_args(argv)
    if args.cmd == "extract":
        print("extract: not implemented yet (milestone M2)")
        return 2
    if args.cmd == "run":
        print("run: not implemented yet (milestones M4-M7)")
        return 2
    return 2
