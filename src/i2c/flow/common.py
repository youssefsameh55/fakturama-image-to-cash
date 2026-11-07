"""UI building blocks shared by the stages: save-once, tabs, list views, selector dialogs."""
from __future__ import annotations

import logging
from typing import Callable

from i2c.errors import UIError
from i2c.flow.context import RunContext
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from i2c.ui.input import click, select_tab, set_text
from i2c.ui.table import Table, read_table
from i2c.ui.wait import wait_stable, wait_until

log = logging.getLogger(__name__)


def click_point(x: int, y: int) -> None:
    """Mouse click at a point that came from an OCR word box (table rows only)."""
    from pywinauto import mouse

    mouse.click(button="left", coords=(int(x), int(y)))


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


def activate_editor(ctx: RunContext, name: str) -> Snapshot:
    """Bring an open editor tab (e.g. 'New Order', also '*New Order') to the front.
    Only the active editor's controls are exposed via UIA (probe), so wait for its pane."""
    snap = ctx.snapshot()
    if not snap.find("Pane", name=name):
        select_tab(snap.tab_item(name).wrapper())
        wait_until(lambda: ctx.snapshot().find("Pane", name=name), ctx.timeout("window"), ctx.interval,
                   desc=f"editor {name!r} to become active")
    return ctx.editor(name)


def activate_subtab(ctx: RunContext, editor: str, tab: str, ready: Callable[[Snapshot], bool]) -> Snapshot:
    """Select a tab inside an editor (Addresses / Miscellaneous / additional address #1)."""
    ed = ctx.editor(editor)
    if not ready(ed):
        select_tab(ed.tab_item(tab).wrapper())
        wait_until(lambda: ready(ctx.editor(editor)), ctx.timeout(), ctx.interval, desc=f"tab {tab!r}")
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
    return ctx.app.wait_dialog(dialog_title, ctx.timeout("window"))


def search_selector(ctx: RunContext, dialog_title: str, text: str, kind: str) -> tuple[Table, Snapshot]:
    """Type `text` into the dialog's search box (real keystrokes, so the filter fires) and
    read the result table once it is stable."""
    dialog = ctx.app.wait_dialog(dialog_title)
    snap = ctx.app.snapshot(dialog)
    box = snap.by_label(ctx.label("dialogs.search"), control_types=["Edit"])
    set_text(box.wrapper(), text, methods=("keys",))
    table = read_rect(ctx, snap.table_rect(), kind, equal_reads=3)
    ctx.targets.extend([box.rect] + [r.rect for r in table.rows])
    return table, snap


def pick_row_and_ok(ctx: RunContext, dialog_title: str, table: Table, index: int) -> None:
    row = table.rows[index]
    click_point(*row.click_point())
    snap = ctx.app.snapshot(ctx.app.wait_dialog(dialog_title))
    click(snap.one("Button", ctx.label("dialogs.ok")).wrapper())
    ctx.app.wait_dialog_closed(dialog_title, ctx.timeout("window"))


def cancel_dialog(ctx: RunContext, dialog_title: str) -> None:
    snap = ctx.app.snapshot(ctx.app.wait_dialog(dialog_title))
    click(snap.one("Button", ctx.label("dialogs.cancel")).wrapper())
    ctx.app.wait_dialog_closed(dialog_title, ctx.timeout("window"))
