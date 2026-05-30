"""Ink-fill detection in ROI boxes."""
from __future__ import annotations

import numpy as np


def roi_to_pixels(roi: dict, canvas_size: tuple) -> tuple[int, int, int, int]:
    """Convert normalized {x,y,w,h} to pixel (x1,y1,x2,y2)."""
    cw, ch = canvas_size
    x1 = int(roi["x"] * cw)
    y1 = int(roi["y"] * ch)
    x2 = int((roi["x"] + roi["w"]) * cw)
    y2 = int((roi["y"] + roi["h"]) * ch)
    return (x1, y1, x2, y2)


def measure_fill(binary_crop: np.ndarray) -> float:
    """Fraction of dark pixels in the crop."""
    if binary_crop.size == 0:
        return 0.0
    return float(np.mean(binary_crop < 128))


def detect_box(
    rectified_binary: np.ndarray,
    roi: dict,
    canvas_size: tuple,
    inner_inset_frac: float = 0.22,
    threshold: float = 0.06,
) -> tuple[float, bool]:
    """Returns (fill_ratio, inked). Insets crop to exclude printed border."""
    x1, y1, x2, y2 = roi_to_pixels(roi, canvas_size)
    w = x2 - x1
    h = y2 - y1
    inset_x = max(1, int(w * inner_inset_frac))
    inset_y = max(1, int(h * inner_inset_frac))
    cx1 = x1 + inset_x
    cy1 = y1 + inset_y
    cx2 = max(cx1 + 1, x2 - inset_x)
    cy2 = max(cy1 + 1, y2 - inset_y)
    crop = rectified_binary[cy1:cy2, cx1:cx2]
    fill = measure_fill(crop)
    return fill, fill > threshold


def select_one(results: dict[str, tuple[float, bool]]) -> str | None:
    """From a group of mutually-exclusive boxes, return key of max fill above threshold, or None."""
    candidates = [(k, f, i) for k, (f, i) in results.items() if i]
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[1], reverse=True)
    return candidates[0][0]
