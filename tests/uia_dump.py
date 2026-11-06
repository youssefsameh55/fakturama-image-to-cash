"""Replay a tools/dump_tree.py text dump as a fake UIA element tree (offline tests for find.py)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

LINE = re.compile(r'^(?P<indent> *)(?P<type>\w+) "(?P<name>.*?)" \[(?P<auto>.*?)\] \{(?P<cls>.*?)\} '
                  r'\((?P<l>-?\d+),(?P<t>-?\d+),(?P<r>-?\d+),(?P<b>-?\d+)\)(?P<rest>.*)$')


@dataclass
class FakeRect:
    left: int
    top: int
    right: int
    bottom: int


@dataclass
class FakeInfo:
    control_type: str
    name: str
    automation_id: str
    class_name: str
    rectangle: FakeRect
    enabled: bool = True
    kids: list["FakeInfo"] = field(default_factory=list)

    def children(self) -> list["FakeInfo"]:
        return self.kids


def load_dump(path: Path) -> FakeInfo:
    roots: list[FakeInfo] = []
    stack: list[tuple[int, FakeInfo]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        depth = len(m["indent"]) // 2
        node = FakeInfo(m["type"], m["name"], m["auto"], m["cls"],
                        FakeRect(int(m["l"]), int(m["t"]), int(m["r"]), int(m["b"])),
                        enabled=" disabled" not in m["rest"].split("legacy=")[-1][-12:])
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].kids.append(node)
        else:
            roots.append(node)
        stack.append((depth, node))
    assert len(roots) >= 1, f"no nodes parsed from {path}"
    if len(roots) == 1:
        return roots[0]
    return FakeInfo("Desktop", "", "", "", FakeRect(0, 0, 0, 0), kids=roots)
