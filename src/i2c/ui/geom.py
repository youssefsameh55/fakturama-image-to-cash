"""Plain geometry types shared by the UI layer (screen pixels, physical/DPI-aware)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    def offset(self, dx: int, dy: int) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def contains_point(self, x: float, y: float) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def v_overlap(self, other: Rect) -> int:
        return min(self.bottom, other.bottom) - max(self.top, other.top)

    def h_overlap(self, other: Rect) -> int:
        return min(self.right, other.right) - max(self.left, other.left)

    @classmethod
    def from_uia(cls, r: object) -> Rect:
        """From a pywinauto RECT (left/top/right/bottom attributes)."""
        return cls(int(r.left), int(r.top), int(r.right), int(r.bottom))  # type: ignore[attr-defined]

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(frozen=True)
class Word:
    """One OCR word with its box in screen coordinates."""
    text: str
    rect: Rect
