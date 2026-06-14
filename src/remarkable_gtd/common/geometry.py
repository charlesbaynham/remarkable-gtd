"""Geometry helpers: Rect dataclass and coordinate normalization utilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class Rect:
    """Axis-aligned rectangle with normalized (fractional 0..1) coordinates."""

    x: float
    y: float
    w: float
    h: float

    def to_pixels(self, canvas_w: int, canvas_h: int) -> Tuple[int, int, int, int]:
        """Convert to pixel (x1, y1, x2, y2), clamped to canvas bounds."""
        x1 = max(0, int(self.x * canvas_w))
        y1 = max(0, int(self.y * canvas_h))
        x2 = min(canvas_w, int((self.x + self.w) * canvas_w))
        y2 = min(canvas_h, int((self.y + self.h) * canvas_h))
        return x1, y1, x2, y2

    def center_px(self, canvas_w: int, canvas_h: int) -> Tuple[float, float]:
        """Return the center of this rect in pixels."""
        return (self.x + self.w / 2) * canvas_w, (self.y + self.h / 2) * canvas_h

    @classmethod
    def from_dict(cls, d: dict) -> "Rect":
        return cls(x=d["x"], y=d["y"], w=d["w"], h=d["h"])

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def overlaps(self, other: "Rect") -> bool:
        """True if this rect overlaps with another."""
        return (
            self.x < other.x + other.w
            and self.x + self.w > other.x
            and self.y < other.y + other.h
            and self.y + self.h > other.y
        )


def normalize(x_px: float, y_px: float, w_px: float, h_px: float,
              ref_w: float, ref_h: float) -> Rect:
    """Normalize pixel rect relative to reference dimensions."""
    return Rect(
        x=x_px / ref_w,
        y=y_px / ref_h,
        w=w_px / ref_w,
        h=h_px / ref_h,
    )


def denormalize(rect: Rect, canvas_w: int, canvas_h: int) -> Tuple[int, int, int, int]:
    """Convert normalized Rect to pixel (x1, y1, x2, y2)."""
    return rect.to_pixels(canvas_w, canvas_h)
