"""Read-only benchmark of UIA access strategies on the running Fakturama (no clicks, no typing).

    python tools/bench_uia.py

Measures what each kind of read costs on THIS machine, so speed work is based on numbers:
full-window snapshot, snapshot of one editor/view pane, FindFirst of one control, live
IsEnabled reads, FindAll of tab names. Leave Fakturama as it is during a normal run (e.g. an
order open plus the bottom views).
"""
from __future__ import annotations

import statistics
import sys
import time

from i2c.ui.app import FakturamaApp, set_dpi_awareness

set_dpi_awareness()

from i2c.config import load_settings  # noqa: E402
from i2c.ui.find import Snapshot  # noqa: E402


def bench(label: str, fn, n: int = 3) -> object:  # noqa: ANN001
    times, out = [], None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t0) * 1000)
    print(f"  {label:<44} {statistics.median(times):8.1f} ms  (n={n})")
    return out


def main() -> int:
    settings = load_settings()
    app = FakturamaApp(settings.window_title).attach()
    sys.stdout.reconfigure(encoding="utf-8")
    print("UIA access cost on this machine (median):")
    full = bench("full window snapshot (cached request)", app.snapshot)
    print(f"      -> {len(full.nodes)} controls")
    bench("full window snapshot (property-by-property)", lambda: Snapshot._take_walk(app.main.element_info, 25), n=1)

    panes = [n for n in full.find("Pane") if n.name and n.rect.area > 0]
    for name in sorted({p.name for p in panes})[:6]:
        info = app.find_first("Pane", name)
        if info is not None:
            sub = bench(f"pane snapshot {name!r}", lambda i=info: Snapshot.take(i))
            print(f"      -> {len(sub.nodes)} controls")
    save = settings.get("labels.toolbar.save")
    el = bench(f"FindFirst Button {save!r}", lambda: app.find_first("Button", save), n=5)
    if el is not None:
        bench("live IsEnabled read (Save)", lambda: el.element.CurrentIsEnabled, n=20)
    tabs = bench("FindAll TabItem names", lambda: app.names("TabItem"))
    print(f"      -> {tabs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
