"""Live check for the UI toolkit (M3): read a Fakturama table via UIA rect + Windows OCR.

Examples
--------
    python tools/read_table.py --view "VATs" --kind vats
    python tools/read_table.py --view "terms of payment" --kind payments
    python tools/read_table.py --dialog "Select the address" --kind select_address
    python tools/read_table.py --dialog "Select the address" --kind select_address --search "acme"

The view/dialog must already be open. Does not click anything; with --search it only types
into the table's search box. Writes an annotated screenshot + log to artifacts/<run-id>/.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from i2c.ui.app import FakturamaApp, set_dpi_awareness

set_dpi_awareness()

from i2c.config import load_settings  # noqa: E402
from i2c.logging_setup import new_run_id, setup_logging  # noqa: E402
from i2c.ui.evidence import Evidence  # noqa: E402
from i2c.ui.input import set_text  # noqa: E402
from i2c.ui.table import read_table  # noqa: E402
from i2c.ui.wait import wait_stable  # noqa: E402

log = logging.getLogger("read_table")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--dialog", help="exact dialog title, e.g. 'Select the address'")
    where.add_argument("--view", help="name of an open list view pane, e.g. 'VATs', 'Documents'")
    ap.add_argument("--kind", help="key under 'tables' in settings.yaml for canonical headers")
    ap.add_argument("--search", help="type this into the table's Search box first")
    args = ap.parse_args(argv)

    settings = load_settings()
    run_dir = settings.artifacts_dir / f"{new_run_id()}-read_table"
    setup_logging(run_dir)
    expected = settings.get(f"tables.{args.kind}") if args.kind else None
    timeout = float(settings.get("timeouts.table_stable", 8))
    interval = float(settings.get("timeouts.poll_interval", 0.25))

    app = FakturamaApp(settings.window_title, float(settings.get("timeouts.window", 15))).attach()
    app.focus()
    evidence = Evidence(run_dir, area=app.rect)

    if args.dialog:
        dialog = app.wait_dialog(args.dialog)
        snap = app.snapshot(dialog)
    else:
        full = app.snapshot()
        snap = full.within(full.one("Pane", args.view))
    rect = snap.table_rect()
    log.info("table rect from UIA: %s", rect.as_tuple())

    if args.search is not None:
        box = snap.by_label(settings.get("labels.dialogs.search", "Search:"), control_types=["Edit"])
        set_text(box.wrapper(), args.search, methods=tuple(settings.get("input.text_methods", ["value", "keys"])))
        log.info("typed %r into search box", args.search)

    kw = dict(scale=float(settings.get("ocr.upscale", 1)), language=settings.get("ocr.language", "en-US"),
              row_tolerance=int(settings.get("ocr.row_merge_tolerance_px", 8)))
    last: dict = {}

    def read():  # noqa: ANN202
        table, img = read_table(rect, expected, **kw)
        last.update(table=table, img=img)
        return table.signature()

    wait_stable(read, timeout, interval, desc="table content")
    table, img = last["table"], last["img"]

    sys.stdout.reconfigure(encoding="utf-8")
    print(f"headers (OCR raw): {table.raw_headers}")
    print(f"headers (canon.):  {table.headers}")
    print(f"rows: {len(table.rows)}")
    for i, row in enumerate(table.rows, 1):
        print(f"  {i:2d}. {json.dumps(row.cells, ensure_ascii=False)}  click={row.click_point()}")

    targets = [rect] + [r.rect for r in table.rows]
    caption = f"{args.dialog or args.view}: {len(table.rows)} row(s) read via UIA rect + OCR"
    evidence.shot("read_table", caption, targets)
    evidence.shot("read_table_crop", caption, [r.rect for r in table.rows], image=img,
                  origin=(rect.left, rect.top))
    (run_dir / "table.json").write_text(json.dumps(
        {"rect": rect.as_tuple(), "raw_headers": table.raw_headers, "headers": table.headers,
         "rows": table.as_dicts()}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"evidence: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
