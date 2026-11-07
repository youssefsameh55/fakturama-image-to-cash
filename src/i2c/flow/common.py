"""UI building blocks shared by the stages: save-once, tabs, list views, selector dialogs."""
from __future__ import annotations

import logging
from typing import Callable

from i2c.errors import SelectorClosed, UIError
from i2c.flow.context import RunContext
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from i2c.ui.input import click, ensure_focus, escape_keys, get_value, select_tab, set_text
from i2c.ui.table import Table, read_table
from i2c.ui.wait import wait_stable, wait_until

log = logging.getLogger(__name__)


def click_point(x: int, y: int) -> None:
    """Mouse click at a point that came from an OCR word box (table rows only)."""
    from pywinauto import mouse

    mouse.click(button="left", coords=(int(x), int(y)))


def park_mouse(ctx: RunContext) -> None:
    """Move the pointer onto the main window's title bar (rect from UIA): hover tooltips such as
    'Pick an item from the list of all products' otherwise cover table rows that are OCR'd."""
    from pywinauto import mouse

    try:
        r = ctx.app.rect()
        mouse.move(coords=(r.left + r.width // 2, r.top + 20))
    except Exception:  # noqa: BLE001 - cosmetic, never fail a step for it
        log.debug("could not park the mouse", exc_info=True)


def save_enabled(ctx: RunContext) -> bool:
    return ctx.snapshot().one("Button", ctx.label("toolbar.save")).enabled


def save_once(ctx: RunContext, what: str) -> None:
    """Click Save exactly once (rule 4) and verify it took: Save becomes disabled again."""
    save = ctx.snapshot().one("Button", ctx.label("toolbar.save"))
    if not save.enabled:
        raise UIError(f"Save is disabled before saving {what}: nothing was changed?")
    ctx.targets.append(save.rect)
    click(save.wrapper())
    wait_until(lambda: not save_enabled(ctx), ctx.timeout("save"), ctx.interval,
               desc=f"{what} to be saved (Save disabled again)")
    log.info("saved %s", what)


def tab_bar_rect(snap: Snapshot, item) -> Rect:  # noqa: ANN001 - find.Node
    """The strip of a tab folder that holds the tab labels: from the folder's top down to its
    first content child. The folder is the TabItem's parent `Tab` node (UIA tree)."""
    folder = snap.parent(item)
    if folder is None:
        raise UIError(f"tab {item.name!r} has no parent folder")
    r = folder.rect
    tops = [c.rect.top for c in snap.children(folder)
            if c.control_type != "TabItem" and c.rect.area and c.rect.top > r.top + 8]
    bottom = min(tops) if tops else r.top + 60
    return Rect(r.left, r.top, r.right, min(bottom, r.top + 120))


def activate_tab(ctx: RunContext, snap: Snapshot, name: str, ready: Callable[[], bool], what: str) -> None:
    """Activate tab `name` and wait for `ready()`.

    1. accessibility (SelectionItem / default action) - may be unsupported by SWT
    2. OCR the folder's tab bar (rect from UIA), find the label, click its word box
    """
    from i2c.ui.ocr import ocr_rect
    from i2c.ui.table import find_phrase

    item = snap.tab_item(name)
    if select_tab(item.wrapper()):
        try:
            wait_until(ready, 2.0, ctx.interval, desc=what)
            return
        except Exception:  # noqa: BLE001
            log.info("accessibility select did not switch to %r; using OCR on the tab bar", name)
    bar = tab_bar_rect(snap, item)
    words, _ = ocr_rect(bar, float(ctx.settings.get("ocr.upscale", 1)), ctx.settings.get("ocr.language", "en-US"))
    box = find_phrase(words, name)
    if box is None:
        raise UIError(f"tab label {name!r} not found by OCR in tab bar {bar.as_tuple()}: "
                      f"{[w.text for w in words]}")
    ctx.targets.append(box)
    click_point(*box.center)
    park_mouse(ctx)
    wait_until(ready, ctx.timeout(), ctx.interval, desc=what)


def activate_editor(ctx: RunContext, name: str) -> Snapshot:
    """Bring an open editor tab (e.g. 'New Order', also '*New Order') to the front.
    Only the active editor's controls are exposed via UIA (probe), so wait for its pane."""
    snap = ctx.snapshot()
    if snap.find("Pane", name=name):
        return ctx.editor(name, snap)          # already active: reuse this snapshot
    activate_tab(ctx, snap, name, lambda: bool(ctx.snapshot().find("Pane", name=name)),
                 f"editor {name!r} to become active")
    return ctx.editor(name)


def activate_subtab(ctx: RunContext, editor: str, tab: str, ready: Callable[[Snapshot], bool]) -> Snapshot:
    """Select a tab inside an editor (Addresses / Miscellaneous / additional address #1)."""
    ed = ctx.editor(editor)
    if not ready(ed):
        activate_tab(ctx, ed, tab, lambda: ready(ctx.editor(editor)), f"tab {tab!r}")
    return ctx.editor(editor)


def open_view(ctx: RunContext, left_panel_key: str, view_name: str) -> Snapshot:
    """Open a Data list view from the left navigation panel and return its scoped snapshot."""
    snap = ctx.snapshot()
    if not snap.find("Pane", name=view_name):
        item = snap.one("Text", ctx.label(f"left_panel.{left_panel_key}"))
        click(item.wrapper())
        wait_until(lambda: ctx.snapshot().find("Pane", name=view_name), ctx.timeout("window"),
                   ctx.interval, desc=f"view {view_name!r}")
    full = ctx.snapshot()
    return full.within(full.one("Pane", view_name))


def read_rect(ctx: RunContext, rect: Rect, kind: str, equal_reads: int = 2) -> Table:
    """OCR a table until it is stable (D9)."""
    expected = ctx.settings.get(f"tables.{kind}")
    kw = dict(scale=float(ctx.settings.get("ocr.upscale", 1)),
              language=ctx.settings.get("ocr.language", "en-US"),
              row_tolerance=int(ctx.settings.get("ocr.row_merge_tolerance_px", 8)))
    last: dict = {}

    def read() -> tuple:
        table, _ = read_table(rect, expected, **kw)
        last["t"] = table
        return table.signature()

    wait_stable(read, ctx.timeout("table_stable"), ctx.interval, equal_reads, desc=f"{kind} table")
    table = last["t"]
    log.info("%s table: %d row(s) %s", kind, len(table.rows), table.as_dicts())
    return table


def read_view_table(ctx: RunContext, view: Snapshot, kind: str) -> Table:
    return read_rect(ctx, view.table_rect(), kind)


# ------------------------------------------------------------------ selector dialogs

def open_selector(ctx: RunContext, editor: str, label_key: str, dialog_title: str):  # noqa: ANN201
    """Click the UPPER icon under an editor label (select existing; never the green +)."""
    ed = ctx.editor(editor)
    icon = ed.icons_below(ctx.label(label_key))[0]
    ctx.targets.append(icon.rect)
    click(icon.wrapper())
    dialog = ctx.app.wait_dialog(dialog_title, ctx.timeout("window"), ready="Edit")
    park_mouse(ctx)
    return dialog


def search_selector(ctx: RunContext, dialog_title: str, text: str, kind: str) -> tuple[Table, Snapshot]:
    """Type `text` into the dialog's search box (real keystrokes, so the filter fires) and
    read the result table once it is stable."""
    dialog = ctx.app.wait_dialog(dialog_title, ready="Edit")
    snap = ctx.app.snapshot(dialog)
    box = snap.by_label(ctx.label("dialogs.search"), control_types=["Edit"])
    ctx.targets.append(box.rect)
    w = box.wrapper()
    ensure_focus(w)
    w.type_keys("^a{BACKSPACE}" + escape_keys(text), with_spaces=True, set_foreground=False)

    def typed_or_closed() -> str | None:          # react at once to either outcome (0.1 s polls)
        if ctx.app.find_dialog(dialog_title) is None:
            return "closed"
        return "typed" if get_value(w).strip() == text else None

    if wait_until(typed_or_closed, 3.0, 0.1, desc=f"search text {text!r}") == "closed":
        raise SelectorClosed(f"{dialog_title!r} closed while typing {text!r}")
    table = read_rect(ctx, snap.table_rect(), kind, equal_reads=3)
    ctx.targets.extend([box.rect] + [r.rect for r in table.rows])
    return table, snap


def pick_row_and_ok(ctx: RunContext, dialog_title: str, table: Table, index: int,
                    click_column: str = "No.") -> None:
    """Choose a row by double-clicking one of its data cells (point from the OCR word box);
    the dialog takes the row and closes. Live: single clicks did not select the row (neither
    on the row number nor on a data cell), double-click does (found by the user). If the
    dialog is still open afterwards, OK is pressed once. Callers verify the result."""
    from pywinauto import mouse

    row = table.rows[index]
    point = table.cell_point(row, click_column) or row.click_point()
    ctx.targets.append(row.rect)
    mouse.double_click(button="left", coords=(int(point[0]), int(point[1])))
    park_mouse(ctx)
    try:
        ctx.app.wait_dialog_closed(dialog_title, 3.0)
        return
    except Exception:  # noqa: BLE001
        log.info("dialog %r still open after double-click; pressing OK", dialog_title)
    snap = ctx.app.snapshot(ctx.app.wait_dialog(dialog_title))
    click(snap.one("Button", ctx.label("dialogs.ok")).wrapper())
    ctx.app.wait_dialog_closed(dialog_title, ctx.timeout("window"))


def cancel_dialog(ctx: RunContext, dialog_title: str) -> None:
    snap = ctx.app.snapshot(ctx.app.wait_dialog(dialog_title))
    click(snap.one("Button", ctx.label("dialogs.cancel")).wrapper())
    ctx.app.wait_dialog_closed(dialog_title, ctx.timeout("window"))
