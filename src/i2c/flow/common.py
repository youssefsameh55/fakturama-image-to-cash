"""UI building blocks shared by the stages: save-once, tabs, list views, selector dialogs."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable

from i2c.errors import SelectorClosed, UIError
from i2c.flow.context import RunContext
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect
from i2c.ui import input as input_mod
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
    """Live IsEnabled of the toolbar Save button (one COM call; the button is found once)."""
    el = ctx.results.get("_save_button")
    if el is None:
        el = ctx.app.find_first("Button", ctx.label("toolbar.save"))
        if el is None:
            raise UIError("toolbar Save button not found")
        ctx.results["_save_button"] = el
    return bool(el.element.CurrentIsEnabled)


def save_once(ctx: RunContext, what: str) -> None:
    """Click Save exactly once (rule 4) and verify it took: Save becomes disabled again."""
    if not save_enabled(ctx):
        raise UIError(f"Save is disabled before saving {what}: nothing was changed?")
    from pywinauto.controls.uiawrapper import UIAWrapper

    save = ctx.results["_save_button"]
    ctx.targets.append(Rect.from_uia(save.rectangle))
    dialogs_before = _child_windows(ctx)
    click(UIAWrapper(save))

    def saved_or_error() -> str | None:
        new = _child_windows(ctx) - dialogs_before
        if new:                                  # Fakturama reports save problems in a dialog
            return "dialog:" + "; ".join(sorted(new))
        return "saved" if not save_enabled(ctx) else None

    outcome = wait_until(saved_or_error, ctx.timeout("save"), ctx.interval,
                         desc=f"{what} to be saved (Save disabled again)")
    if outcome != "saved":
        texts = _dialog_texts(ctx, dialogs_before)
        raise UIError(f"Fakturama showed a dialog while saving {what}: {outcome[7:]} {texts}")
    log.info("saved %s", what)


def _gone(element) -> bool:  # noqa: ANN001 - raw IUIAutomationElement
    """True once a control is no longer shown (its editor was closed: element disposed/offscreen)."""
    try:
        return bool(element.CurrentIsOffscreen) or element.CurrentBoundingRectangle.right <= 0
    except Exception:  # noqa: BLE001 - UIA_E_ELEMENTNOTAVAILABLE: disposed
        return True


def close_saved_editor(ctx: RunContext, anchor, what: str) -> bool:  # noqa: ANN001 - pywinauto wrapper
    """Close a master-data editor (payment method, VAT, product, Debtor) AFTER its save was verified.

    Why: every editor left open makes each later UIA read slower (live: ~100 ms per FindFirst with
    nothing created, ~260 ms after 5 editors were left open; a create-everything run took 188 s).
    Safe by construction: only when Save is disabled (nothing unsaved), keys only with proven focus
    on a control inside that editor (`anchor`), closed = that control is gone. A dialog after the
    key (e.g. 'Save Parts') is never answered: the run stops with its text. If no close key
    works, the editor just stays open (a warning) - closing is cleanup, not a brief requirement.

    Only non-character keys: live, Ctrl+W is not bound in Fakturama and typed a 'w' into the
    focused Name box (editor dirty again -> Ctrl+F4 then asked 'Save Parts'). Ctrl+F4 closes the
    editor and types nothing. Should a key ever change the editor anyway (Save enabled again),
    the run stops at once instead of trying another key."""
    if save_enabled(ctx):
        log.warning("not closing the %s editor: it has unsaved changes", what)
        return False
    element = anchor.element_info.element
    dialogs_before = _child_windows(ctx)
    for keys in ctx.settings.get("input.close_editor_keys", ["^{F4}"]):
        if _gone(element):
            break
        ensure_focus(anchor)
        anchor.type_keys(keys, set_foreground=False)
        try:
            wait_until(lambda: _gone(element) or bool(_child_windows(ctx) - dialogs_before),
                       2.0, 0.1, desc=f"{what} editor to close")
        except Exception:  # noqa: BLE001 - try the next key
            log.info("%s did not close the %s editor", keys, what)
        new = _child_windows(ctx) - dialogs_before
        if new:
            raise UIError(f"Fakturama showed a dialog when closing the saved {what} editor: "
                          f"{sorted(new)} {_dialog_texts(ctx, dialogs_before)}")
        if not _gone(element) and save_enabled(ctx):
            raise UIError(f"close key {keys!r} changed the saved {what} editor (Save enabled again); "
                          f"not saved, left open for review")
    if _gone(element):
        log.info("closed the saved %s editor", what)
        return True
    msg = f"the saved {what} editor could not be closed (left open; later UI reads are slower)"
    log.warning(msg)
    ctx.results.setdefault("warnings", []).append(msg)
    return False


def _child_windows(ctx: RunContext) -> set[str]:
    """Titles of dialog windows owned by Fakturama (children of the main window)."""
    main = ctx.app.main
    try:
        return {c.name or "<untitled>" for c in main.element_info.children() if c.control_type == "Window"}
    except Exception:  # noqa: BLE001
        return set()


def _dialog_texts(ctx: RunContext, before: set[str]) -> list[str]:
    out: list[str] = []
    try:
        for c in ctx.app.main.element_info.children():
            if c.control_type == "Window" and (c.name or "<untitled>") not in before:
                snap = ctx.app.snapshot(c, max_depth=8)
                out += [n.name for n in snap.find("Text") if n.name][:6]
    except Exception:  # noqa: BLE001
        pass
    return out


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
    folder = snap.parent(item)
    # Remembered label position: only valid while the folder holds exactly the same tabs in the
    # same order and the bar has not moved (UIA, cheap) - so a stale position (e.g. over a
    # tab's close button after tabs changed) is never clicked. The switch is verified either way.
    key = (bar.as_tuple(), tuple(n.name for n in snap.children(folder) if n.control_type == "TabItem")
           if folder else (), name)
    cached = ctx.tab_cache.get(key)
    if cached is not None:
        ctx.targets.append(cached)
        click_point(*cached.center)
        park_mouse(ctx)
        try:
            wait_until(ready, 2.0, ctx.interval, desc=what)
            return
        except Exception:  # noqa: BLE001
            log.info("remembered position of tab %r did not switch; reading the tab bar again", name)
            ctx.tab_cache.pop(key, None)
    words, _ = ocr_rect(bar, float(ctx.settings.get("ocr.upscale", 1)), ctx.settings.get("ocr.language", "en-US"))
    box = find_phrase(words, name)
    if box is None:
        raise UIError(f"tab label {name!r} not found by OCR in tab bar {bar.as_tuple()}: "
                      f"{[w.text for w in words]}")
    ctx.targets.append(box)
    click_point(*box.center)
    park_mouse(ctx)
    wait_until(ready, ctx.timeout(), ctx.interval, desc=what)
    ctx.tab_cache[key] = box


def activate_editor(ctx: RunContext, name: str) -> Snapshot:
    """Bring an open editor tab (e.g. 'New Order', also '*New Order') to the front.
    Only the active editor's controls are exposed via UIA (probe), so wait for its pane."""
    if ctx.app.find_first("Pane", name) is not None:
        return ctx.editor(name)                 # already active: read only its pane
    snap = ctx.snapshot()                       # full read only to switch tabs
    activate_tab(ctx, snap, name, lambda: ctx.app.find_first("Pane", name) is not None,
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
    if ctx.app.find_first("Pane", view_name) is None:
        item = ctx.app.find_first("Text", ctx.label(f"left_panel.{left_panel_key}"))
        if item is None:
            raise UIError(f"navigation entry for {view_name!r} not found")
        from pywinauto.controls.uiawrapper import UIAWrapper

        click(UIAWrapper(item))
        wait_until(lambda: ctx.app.find_first("Pane", view_name) is not None, ctx.timeout("window"),
                   ctx.interval, desc=f"view {view_name!r}")
    return ctx.editor(view_name)


@contextmanager
def maximized_view(ctx: RunContext, view_name: str):  # noqa: ANN201
    """Maximize a list view (its own 'Maximize' button in the view's tab folder) for full-width
    columns, and always restore it. Live: in a narrow Documents view every cell was truncated
    ('POOO...', '$678...'), so exact values could not be checked."""
    snap = ctx.snapshot()
    pane = snap.one("Pane", view_name)
    folder = snap.parent(pane)
    if folder is None or folder.control_type != "Tab":
        raise UIError(f"view {view_name!r} has no tab folder")
    buttons = snap.within(folder).find("Button", name=ctx.label("views.maximize"))
    maximized = False
    if buttons:
        click(buttons[0].wrapper())
        wait_until(lambda: Rect.from_uia(ctx.app.find_first("Pane", view_name).rectangle).height
                   > pane.rect.height + 50, ctx.timeout(), ctx.interval, desc=f"{view_name!r} maximized")
        maximized = True
        park_mouse(ctx)
    try:
        yield ctx.editor(view_name)
    finally:
        if maximized:
            full = ctx.snapshot()
            f = full.parent(full.one("Pane", view_name))
            restore = full.within(f).find("Button", name=ctx.label("views.restore")) if f else []
            if restore:
                click(restore[0].wrapper())
                park_mouse(ctx)


def read_rect(ctx: RunContext, rect: Rect, kind: str, equal_reads: int = 2) -> Table:
    """OCR a table until it is stable (D9)."""
    expected = ctx.settings.get(f"tables.{kind}")
    kw = dict(scale=float(ctx.settings.get("ocr.upscale", 1)),
              language=ctx.settings.get("ocr.language", "en-US"),
              row_tolerance=int(ctx.settings.get("ocr.row_merge_tolerance_px", 8)),
              recover_columns=ctx.settings.get(f"tables_checked.{kind}"))
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
    w.type_keys("^a{BACKSPACE}" + escape_keys(text), with_spaces=True, set_foreground=False,
                pause=input_mod.KEY_PAUSE)

    def typed_or_closed() -> str | None:          # react at once to either outcome (0.1 s polls)
        if ctx.app.find_dialog(dialog_title) is None:
            return "closed"
        return "typed" if get_value(w).strip() == text else None

    if wait_until(typed_or_closed, 3.0, 0.1, desc=f"search text {text!r}") == "closed":
        raise SelectorClosed(f"{dialog_title!r} closed while typing {text!r}")
    table = read_rect(ctx, snap.table_rect(), kind)          # D9: 2 identical reads
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
