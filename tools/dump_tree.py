"""UIA probe: dump the automation tree of a Fakturama window to text + screenshot.

Examples
--------
    python tools/dump_tree.py --list
    python tools/dump_tree.py --window ".*Fakturama.*" --out artifacts/probe/main
    python tools/dump_tree.py --window "Select the address" --depth 25 --out artifacts/probe/select_address

Writes <out>.txt (indented tree) and <out>.png (screenshot of the window rectangle).
Every line: ControlType "Name" [auto_id] {class} (l,t,r,b) patterns=[...] value=... legacy=...

Read-only: never clicks or types.
"""
from __future__ import annotations

import argparse
import ctypes
import re
import sys
import time
from pathlib import Path

DEFAULT_APP_REGEX = ".*Fakturama.*"

# Patterns worth knowing about for SWT / NatTable controls.
PATTERNS = (
    "Invoke", "Value", "Toggle", "SelectionItem", "Selection", "ExpandCollapse",
    "Grid", "GridItem", "Table", "TableItem", "Scroll", "ScrollItem", "Text",
    "RangeValue", "Window", "LegacyIAccessible",
)


def set_dpi_awareness() -> None:
    """Per-monitor DPI awareness so UIA rects == screenshot pixels (D15)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


set_dpi_awareness()

from pywinauto import Desktop  # noqa: E402  (import after DPI awareness)
from pywinauto import findwindows  # noqa: E402
from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface  # noqa: E402
from pywinauto.uia_element_info import UIAElementInfo  # noqa: E402


def _short(text: object, limit: int = 120) -> str:
    s = "" if text is None else str(text)
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    return s if len(s) <= limit else s[: limit - 3] + "..."


def describe(info: UIAElementInfo) -> str:
    elem = info.element
    patterns: list[str] = []
    value = None
    legacy = None
    for name in PATTERNS:
        try:
            iface = get_elem_interface(elem, name)
        except (NoPatternInterfaceError, Exception):
            continue
        patterns.append(name)
        try:
            if name == "Value":
                value = iface.CurrentValue
            elif name == "Toggle":
                value = f"toggle={iface.CurrentToggleState}"
            elif name == "LegacyIAccessible":
                parts = [
                    f"role={iface.CurrentRole}",
                    f"value={_short(iface.CurrentValue, 60)!r}" if iface.CurrentValue else "",
                    f"desc={_short(iface.CurrentDescription, 60)!r}" if iface.CurrentDescription else "",
                    f"help={_short(iface.CurrentHelp, 60)!r}" if iface.CurrentHelp else "",
                ]
                legacy = " ".join(p for p in parts if p)
            elif name == "Grid":
                value = f"grid={iface.CurrentRowCount}x{iface.CurrentColumnCount}"
        except Exception:
            pass

    r = info.rectangle
    flags = []
    if not info.enabled:
        flags.append("disabled")
    if not info.visible:
        flags.append("offscreen")
    out = (
        f'{info.control_type} "{_short(info.name)}"'
        f" [{info.automation_id or ''}] {{{info.class_name or ''}}}"
        f" ({r.left},{r.top},{r.right},{r.bottom})"
        f" patterns=[{','.join(patterns)}]"
    )
    if value not in (None, ""):
        out += f" value={_short(value)!r}"
    if legacy:
        out += f" legacy=<{legacy}>"
    if flags:
        out += " " + " ".join(flags)
    return out


def walk(info: UIAElementInfo, depth: int, max_depth: int, max_children: int,
         lines: list[str], stats: dict[str, int]) -> None:
    lines.append("  " * depth + describe(info))
    stats["nodes"] += 1
    if depth >= max_depth:
        try:
            if info.children():
                lines.append("  " * (depth + 1) + "... (depth limit)")
        except Exception:
            pass
        return
    try:
        children = info.children()
    except Exception as exc:  # stale element etc.
        lines.append("  " * (depth + 1) + f"!! children() failed: {exc}")
        return
    for i, child in enumerate(children):
        if i >= max_children:
            lines.append("  " * (depth + 1) + f"... {len(children) - max_children} more children omitted")
            break
        walk(child, depth + 1, max_depth, max_children, lines, stats)


def find_app_pid(app_regex: str) -> int:
    wins = Desktop(backend="uia").windows(title_re=app_regex, visible_only=False)
    if not wins:
        sys.exit(f"No top-level window matches {app_regex!r}. Is Fakturama running?")
    return wins[0].process_id()


def list_windows(app_regex: str) -> None:
    pid = find_app_pid(app_regex)
    elems = findwindows.find_elements(process=pid, backend="uia", top_level_only=True, visible_only=False)
    print(f"Top-level windows of process {pid}:")
    for e in elems:
        r = e.rectangle
        print(f'  {e.control_type:<10} "{e.name}" {{{e.class_name}}} ({r.left},{r.top},{r.right},{r.bottom})'
              f"{'' if e.visible else ' offscreen'}")


def find_window(title_regex: str, app_regex: str) -> UIAElementInfo:
    pid = find_app_pid(app_regex)
    rx = re.compile(title_regex)
    elems = findwindows.find_elements(process=pid, backend="uia", top_level_only=True, visible_only=False)
    matches = [e for e in elems if rx.fullmatch(e.name or "") or rx.search(e.name or "")]
    if not matches:
        names = ", ".join(repr(e.name) for e in elems)
        sys.exit(f"No window of the Fakturama process matches {title_regex!r}. Windows: {names}")
    if len(matches) > 1:
        print(f"Note: {len(matches)} windows match; using the first visible one.", file=sys.stderr)
        matches.sort(key=lambda e: not e.visible)
    return matches[0]


def bring_to_front(info: UIAElementInfo) -> None:
    """Best effort: raise the Fakturama window so the screenshot shows it, not the terminal.

    Windows may refuse SetForegroundWindow from a background process; --delay is the fallback.
    """
    try:
        hwnd = info.handle
        if not hwnd:
            return
        top = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT: dialogs -> their own root
        ctypes.windll.user32.SetForegroundWindow(top or hwnd)
    except Exception:
        pass


def screenshot(info: UIAElementInfo, path: Path) -> bool:
    try:
        import mss
        import mss.tools
    except ImportError:
        return False
    r = info.rectangle
    if r.width() <= 0 or r.height() <= 0:
        return False
    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        shot = sct.grab({"left": r.left, "top": r.top, "width": r.width(), "height": r.height()})
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list top-level windows of the Fakturama process")
    ap.add_argument("--window", help="regex for the window title to dump (searched within the Fakturama process)")
    ap.add_argument("--app", default=DEFAULT_APP_REGEX, help="regex identifying the Fakturama main window")
    ap.add_argument("--depth", type=int, default=20, help="max tree depth (default 20)")
    ap.add_argument("--max-children", type=int, default=300, help="max children listed per node")
    ap.add_argument("--out", help="output path prefix, e.g. artifacts/probe/main -> main.txt + main.png")
    ap.add_argument("--delay", type=int, default=0,
                    help="seconds to wait before capturing, so you can bring Fakturama to the front")
    args = ap.parse_args(argv)

    if args.list:
        list_windows(args.app)
        return 0
    if not args.window:
        ap.error("--window is required unless --list is given")

    info = find_window(args.window, args.app)
    bring_to_front(info)
    for remaining in range(args.delay, 0, -1):
        print(f"Capturing in {remaining}s - switch to Fakturama now...", flush=True)
        time.sleep(1)  # probe tool only: human countdown, not UI synchronisation
    t0 = time.perf_counter()
    lines: list[str] = []
    stats = {"nodes": 0}
    walk(info, 0, args.depth, args.max_children, lines, stats)
    elapsed = time.perf_counter() - t0
    header = [
        f"# window={info.name!r} pid={info.process_id} depth<={args.depth}",
        f"# nodes={stats['nodes']} elapsed={elapsed:.1f}s",
        "",
    ]
    text = "\n".join(header + lines) + "\n"

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        txt = out.with_suffix(".txt")
        txt.write_text(text, encoding="utf-8")
        png = out.with_suffix(".png")
        shot_ok = screenshot(info, png)
        print(f"Wrote {txt} ({stats['nodes']} nodes, {elapsed:.1f}s)")
        print(f"Wrote {png}" if shot_ok else "Screenshot skipped (mss missing or empty rect)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
