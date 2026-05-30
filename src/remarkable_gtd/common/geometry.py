"""Geometry helpers for normalized ROI rectangles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def to_pixels(self, canvas_w: int, canvas_h: int) -> tuple[int, int, int, int]:
        """Return (x1, y1, x2, y2) in pixel coordinates."""
        x1 = int(self.x * canvas_w)
        y1 = int(self.y * canvas_h)
        x2 = int((self.x + self.w) * canvas_w)
        y2 = int((self.y + self.h) * canvas_h)
        return (x1, y1, x2, y2)

    def overlaps(self, other: Rect) -> bool:
        return not (
            self.x + self.w <= other.x
            or other.x + other.w <= self.x
            or self.y + self.h <= other.y
            or other.y + other.h <= self.y
        )


def normalize_rect(rect: dict, page_rect: dict) -> dict:
    """Normalize a getBoundingClientRect dict relative to a page rect.

    Both dicts have keys: left, top, width, height.
    Returns {x, y, w, h} in fractions of the page.
    """
    px = page_rect["left"]
    py = page_rect["top"]
    pw = page_rect["width"]
    ph = page_rect["height"]
    return {
        "x": (rect["left"] - px) / pw,
        "y": (rect["top"] - py) / ph,
        "w": rect["width"] / pw,
        "h": rect["height"] / ph,
    }
