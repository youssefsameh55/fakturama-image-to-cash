"""Locate controls via UIA (D7): control type + name, or label adjacency. No coordinates.

A `Snapshot` walks the UIA tree once (each property read is a cross-process COM call, so
re-walking per query is slow) and answers many queries from that cached copy. Take a fresh
snapshot after the UI changes.

Probe facts this relies on (artifacts/probe, 2026-11-07):
- Many Edits/ComboBoxes carry their label as UIA name (Edit "Cust.Ref."), some don't
  (No., Date, price-mode combo) -> `by_label` falls back to geometry: nearest control to the
  right of (or below) the Text label, vertically overlapping it.
- NatTable tables are `Pane ""` leaves with no children except scrollbars -> `table_rect`.
- Left-panel entries are Text controls flagged offscreen although visible, so offscreen
  flags are not used for filtering by default.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from i2c.errors import AmbiguousElement, ElementNotFound
from i2c.ui.geom import Rect

INPUT_TYPES = ("Edit", "ComboBox")


@dataclass
class Node:
    index: int
    info: object                 # pywinauto UIAElementInfo
    control_type: str
    name: str
    auto_id: str
    class_name: str
    rect: Rect
    parent: int | None
    depth: int
    enabled: bool = True

    def wrapper(self):  # noqa: ANN201 - pywinauto wrapper (specialised by control type)
        from pywinauto.controls.uiawrapper import UIAWrapper

        return UIAWrapper(self.info)

    def __str__(self) -> str:
        return f'{self.control_type} "{self.name}" {self.rect.as_tuple()}'


class Snapshot:
    def __init__(self, nodes: list[Node]) -> None:
        self.nodes = nodes
        self._children: dict[int, list[int]] = {}
        for n in nodes:
            if n.parent is not None:
                self._children.setdefault(n.parent, []).append(n.index)

    # ------------------------------------------------------------ construction

    @classmethod
    def take(cls, root: object, max_depth: int = 25) -> Snapshot:
        """Walk `root` (a pywinauto wrapper or UIAElementInfo) depth-first."""
        info = getattr(root, "element_info", root)
        nodes: list[Node] = []

        def visit(el: object, parent: int | None, depth: int) -> None:
            try:
                rect = Rect.from_uia(el.rectangle)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - element vanished mid-walk
                return
            node = Node(len(nodes), el, _safe(el, "control_type"), _safe(el, "name"),
                        _safe(el, "automation_id"), _safe(el, "class_name"), rect, parent, depth,
                        bool(_safe(el, "enabled", True)))
            nodes.append(node)
            if depth >= max_depth:
                return
            try:
                kids = el.children()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                return
            for kid in kids:
                visit(kid, node.index, depth + 1)

        visit(info, None, 0)
        return cls(nodes)

    # ------------------------------------------------------------ tree helpers

    def node_at(self, index: int) -> Node:
        return self.nodes[index]

    def parent(self, node: Node) -> Node | None:
        return None if node.parent is None else self.node_at(node.parent)

    def children(self, node: Node) -> list[Node]:
        return [self.nodes[i] for i in self._children.get(node.index, [])]

    def descendants(self, node: Node) -> list[Node]:
        out: list[Node] = []
        stack = list(reversed(self._children.get(node.index, [])))
        while stack:
            n = self.nodes[stack.pop()]
            out.append(n)
            stack.extend(reversed(self._children.get(n.index, [])))
        return out

    def ancestors(self, node: Node) -> list[Node]:
        out: list[Node] = []
        p = node.parent
        while p is not None:
            out.append(self.nodes[p])
            p = self.nodes[p].parent
        return out

    def within(self, container: Node) -> Snapshot:
        """Sub-snapshot of `container`'s subtree (same Node objects, re-indexed lookups)."""
        return _SubSnapshot(self, [container, *self.descendants(container)])

    # ------------------------------------------------------------ queries

    def find(self, control_type: str | Iterable[str] | None = None, name: str | None = None,
             name_re: str | None = None, auto_id: str | None = None, enabled: bool | None = None,
             inside: Rect | None = None) -> list[Node]:
        types = {control_type} if isinstance(control_type, str) else set(control_type or ())
        rx = re.compile(name_re) if name_re else None
        out = []
        for n in self.nodes:
            if types and n.control_type not in types:
                continue
            if name is not None and n.name != name:
                continue
            if rx and not rx.fullmatch(n.name):
                continue
            if auto_id is not None and n.auto_id != auto_id:
                continue
            if enabled is not None and n.enabled != enabled:
                continue
            if inside and not inside.contains_point(*n.rect.center):
                continue
            out.append(n)
        return out

    def one(self, control_type: str | Iterable[str] | None = None, name: str | None = None,
            **kw: object) -> Node:
        found = self.find(control_type, name, **kw)  # type: ignore[arg-type]
        what = f"{control_type} name={name!r} {kw or ''}".strip()
        if not found:
            raise ElementNotFound(f"no control matches {what}")
        if len(found) > 1:
            raise AmbiguousElement(f"{len(found)} controls match {what}: " + "; ".join(map(str, found[:5])))
        return found[0]

    def by_label(self, label: str, control_types: Iterable[str] = INPUT_TYPES,
                 direction: str = "right") -> Node:
        """The input control belonging to the visible label `label`.

        1. A control of `control_types` whose UIA name equals the label, if exactly one.
        2. Otherwise geometry: for each Text named `label`, the nearest control of
           `control_types` to its right (vertically overlapping) or below it
           (horizontally overlapping). Must resolve to exactly one control.
        """
        types = tuple(control_types)
        named = self.find(types, name=label)
        if len(named) == 1:
            return named[0]

        candidates = self.find(types)
        results: dict[int, Node] = {}
        for lab in self.find("Text", name=label):
            best = _nearest(lab.rect, candidates, direction)
            if best is not None:
                results[best.index] = best
        if not results:
            raise ElementNotFound(f"no {'/'.join(types)} {direction} of label {label!r}")
        if len(results) > 1:
            raise AmbiguousElement(f"label {label!r} resolves to {len(results)} controls: "
                                   + "; ".join(map(str, results.values())))
        return next(iter(results.values()))

    def inputs_after_label(self, label: str, control_types: Iterable[str] = ("Edit",)) -> list[Node]:
        """All inputs on the label's line, left to right, up to the next Text label.

        For one label owning several fields ("ZIP - City", "First Name Last Name") on a
        two-column form, where the next label's fields sit further right on the same line.
        """
        lab = self.one("Text", label)
        line = [n for n in self.nodes
                if n.rect.area and n.rect.left >= lab.rect.right - 4
                and n.rect.v_overlap(lab.rect) >= min(n.rect.height, lab.rect.height) / 2]
        stop = min((n.rect.left for n in line if n.control_type == "Text" and n.name), default=None)
        types = set(control_types)
        out = [n for n in line if n.control_type in types and (stop is None or n.rect.left < stop)]
        if not out:
            raise ElementNotFound(f"no {'/'.join(types)} after label {label!r}")
        return sorted(out, key=lambda n: n.rect.left)

    def icons_below(self, label: str, control_type: str = "Image") -> list[Node]:
        """Unnamed icon controls stacked under a Text label, top to bottom.

        Probe: the order editor's 'Addresses' and 'Items' labels have their action icons as
        unnamed Images directly below them (upper = select existing, then the green +).
        """
        lab = self.one("Text", label)
        icons = [n for n in self.find(control_type)
                 if n.rect.area and n.rect.top >= lab.rect.bottom - 4 and n.rect.h_overlap(lab.rect) > 0]
        icons.sort(key=lambda n: n.rect.top)
        # Keep only the contiguous stack: stop at the first big vertical gap.
        stack: list[Node] = []
        for n in icons:
            if stack and n.rect.top - stack[-1].rect.bottom > 2 * max(n.rect.height, 20):
                break
            stack.append(n)
        if not stack:
            raise ElementNotFound(f"no {control_type} icons below label {label!r}")
        return stack

    def tab_item(self, name: str) -> Node:
        """TabItem by name, ignoring the '*' Fakturama prefixes to dirty editors."""
        found = [n for n in self.find("TabItem") if n.name.lstrip("*") == name]
        if not found:
            raise ElementNotFound(f"no tab {name!r}")
        if len(found) > 1:
            raise AmbiguousElement(f"{len(found)} tabs named {name!r}")
        return found[0]

    def is_leaf_pane(self, node: Node) -> bool:
        """A Pane whose subtree holds nothing but scrollbars: how NatTable shows up in UIA."""
        if node.control_type != "Pane":
            return False
        for d in self.descendants(node):
            if d.control_type == "ScrollBar" or any(a.control_type == "ScrollBar"
                                                    for a in self.ancestors(d)):
                continue
            return False
        return True

    def table_rect(self, min_height: int = 60) -> Rect:
        """Rectangle of the largest leaf Pane (the NatTable) in this snapshot.

        Scrollbars are cut off so OCR sees only cells.
        """
        panes = [n for n in self.nodes if n.rect.height >= min_height and self.is_leaf_pane(n)]
        if not panes:
            raise ElementNotFound("no table-like Pane found")
        best = max(panes, key=lambda n: n.rect.area)
        rect = best.rect
        for sb in self.descendants(best):
            if sb.control_type != "ScrollBar":
                continue
            if sb.name == "Horizontal" and sb.rect.top > rect.top:
                rect = Rect(rect.left, rect.top, rect.right, min(rect.bottom, sb.rect.top))
            elif sb.name == "Vertical" and sb.rect.left > rect.left:
                rect = Rect(rect.left, rect.top, min(rect.right, sb.rect.left), rect.bottom)
        return rect


class _SubSnapshot(Snapshot):
    def __init__(self, parent: Snapshot, nodes: list[Node]) -> None:
        keep = {n.index for n in nodes}
        self.nodes = nodes
        self._children = {k: [c for c in v if c in keep]
                          for k, v in parent._children.items() if k in keep}
        self._all = parent.nodes

    def node_at(self, index: int) -> Node:
        return self._all[index]

    def descendants(self, node: Node) -> list[Node]:
        out: list[Node] = []
        stack = list(reversed(self._children.get(node.index, [])))
        while stack:
            n = self._all[stack.pop()]
            out.append(n)
            stack.extend(reversed(self._children.get(n.index, [])))
        return out

    def children(self, node: Node) -> list[Node]:
        return [self._all[i] for i in self._children.get(node.index, [])]

    def ancestors(self, node: Node) -> list[Node]:
        out: list[Node] = []
        p = node.parent
        while p is not None:
            out.append(self._all[p])
            p = self._all[p].parent
        return out


def _nearest(label: Rect, candidates: list[Node], direction: str) -> Node | None:
    best: tuple[float, Node] | None = None
    for c in candidates:
        r = c.rect
        if r.area == 0:
            continue
        if direction == "right":
            if r.v_overlap(label) < min(r.height, label.height) / 2 or r.left < label.right - 4:
                continue
            dist = r.left - label.right
        elif direction == "below":
            if r.h_overlap(label) <= 0 or r.top < label.bottom - 4:
                continue
            dist = r.top - label.bottom
        else:
            raise ValueError(f"direction must be 'right' or 'below', not {direction!r}")
        if best is None or dist < best[0]:
            best = (dist, c)
    return best[1] if best else None


def _safe(el: object, attr: str, default: object = "") -> object:
    try:
        value = getattr(el, attr)
        return value if value is not None else default
    except Exception:  # noqa: BLE001
        return default
