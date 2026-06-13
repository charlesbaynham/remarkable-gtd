"""Per-ROI ink detection: fill-ratio measurement inside known boxes.

The printed tick boxes are empty bordered rectangles in the same ink as the
user's pen. Because the manifest tells us each box's exact rectangle, ink
detection reduces to a fill-ratio measurement of the box *interior*: the
crop is inset by ``inner_inset_frac`` so the printed border (~0.45 mm
stroke) is excluded, and an empty printed box reads as ~0 fill.
"""
from __future__ import annotations

import numpy as np


def roi_to_pixels(roi: dict, canvas_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Convert a normalized ``{x,y,w,h}`` ROI to pixel ``(x1, y1, x2, y2)``.

    Coordinates are clamped to the canvas bounds.
    """
    canvas_w, canvas_h = canvas_size
    x1 = max(0, int(roi["x"] * canvas_w))
    y1 = max(0, int(roi["y"] * canvas_h))
    x2 = min(canvas_w, int(round((roi["x"] + roi["w"]) * canvas_w)))
    y2 = min(canvas_h, int(round((roi["y"] + roi["h"]) * canvas_h)))
    return x1, y1, x2, y2


def measure_fill(binary_crop: np.ndarray) -> float:
    """Fraction of dark pixels (< 128) in a binary crop. 0.0 if empty."""
    if binary_crop.size == 0:
        return 0.0
    return float((binary_crop < 128).mean())


def detect_box(
    rectified_binary: np.ndarray,
    roi: dict,
    canvas_size: tuple[int, int],
    inner_inset_frac: float = 0.22,
    threshold: float = 0.06,
) -> tuple[float, bool]:
    """Measure ink fill inside a tick box, excluding its printed border.

    Args:
        rectified_binary: Full rectified binary image (0=ink, 255=paper).
        roi: Normalized rect ``{x, y, w, h}`` from the manifest.
        canvas_size: ``(canvas_w, canvas_h)`` in pixels.
        inner_inset_frac: Fraction of box width/height to inset on each side
            (excludes the printed 0.45 mm border plus slop from rectification).
        threshold: Fill ratio above which the box counts as inked.

    Returns:
        ``(fill_ratio, inked)``.
    """
    x1, y1, x2, y2 = roi_to_pixels(roi, canvas_size)
    w = x2 - x1
    h = y2 - y1
    if w <= 2 or h <= 2:
        return 0.0, False

    # Inset at least 1px per side even for tiny boxes.
    ix = max(1, int(round(w * inner_inset_frac)))
    iy = max(1, int(round(h * inner_inset_frac)))
    inner = rectified_binary[y1 + iy : y2 - iy, x1 + ix : x2 - ix]

    fill = measure_fill(inner)
    return fill, fill > threshold


def select_one(results: dict[str, tuple[float, bool]]) -> str | None:
    """From mutually-exclusive boxes, pick the inked one with max fill.

    Args:
        results: Mapping of key -> ``(fill_ratio, inked)``.

    Returns:
        The key of the highest-fill inked box, or ``None`` if none inked.
    """
    inked = {k: fill for k, (fill, ok) in results.items() if ok}
    if not inked:
        return None
    return max(inked, key=inked.get)
