"""Shared state for one run: settings, extracted order, the app handle, evidence, results."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from i2c.config import Settings
from i2c.errors import ConfigError, ElementNotFound
from i2c.models import OrderData
from i2c.ui.app import FakturamaApp
from i2c.ui.evidence import Evidence
from i2c.ui.find import Snapshot
from i2c.ui.geom import Rect


@dataclass
class RunContext:
    settings: Settings
    order: OrderData
    app: FakturamaApp
    evidence: Evidence
    run_dir: Path
    results: dict[str, Any] = field(default_factory=lambda: {"entities": {}})
    targets: list[Rect] = field(default_factory=list)   # outlined in the current step's screenshot
    current_step: str = ""

    # ------------------------------------------------------------ config access

    def label(self, dotted: str) -> Any:
        value = self.settings.get(f"labels.{dotted}")
        if value is None:
            raise ConfigError(f"labels.{dotted} missing in config/settings.yaml")
        return value

    def timeout(self, name: str = "default") -> float:
        return float(self.settings.get(f"timeouts.{name}", self.settings.get("timeouts.default", 10)))

    @property
    def interval(self) -> float:
        return float(self.settings.get("timeouts.poll_interval", 0.25))

    @property
    def text_methods(self) -> tuple[str, ...]:
        return tuple(self.settings.get("input.text_methods", ["value", "keys"]))

    @property
    def date_pattern(self) -> str:
        return str(self.settings.get("input.date_pattern", "{mon} {day}, {year}"))

    # ------------------------------------------------------------ UI access

    def snapshot(self) -> Snapshot:
        """Fresh snapshot of the whole main window."""
        return self.app.snapshot()

    def editor(self, pane_name: str, snap: Snapshot | None = None) -> Snapshot:
        """Fresh snapshot scoped to the editor/view pane named `pane_name` (e.g. 'New Order')."""
        snap = snap or self.snapshot()
        panes = snap.find("Pane", name=pane_name)
        if not panes:
            raise ElementNotFound(f"editor/view pane {pane_name!r} not found")
        if len(panes) > 1:  # nested panes can share the name; the outermost holds everything
            panes.sort(key=lambda n: n.rect.area, reverse=True)
        return snap.within(panes[0])
