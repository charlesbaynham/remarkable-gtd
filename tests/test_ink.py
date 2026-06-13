"""Unit tests for ink-fill detection (no rendering required)."""
from __future__ import annotations

import numpy as np

from remarkable_gtd.scan.ink import detect_box, measure_fill, roi_to_pixels, select_one


def make_box_canvas(size: int = 36, border: int = 2) -> np.ndarray:
    """A white canvas containing one printed (empty) box with a black border."""
    canvas = np.full((size * 3, size * 3), 255, dtype=np.uint8)
    x1 = y1 = size
    x2 = y2 = size * 2
    canvas[y1:y1 + border, x1:x2] = 0
    canvas[y2 - border:y2, x1:x2] = 0
    canvas[y1:y2, x1:x1 + border] = 0
    canvas[y1:y2, x2 - border:x2] = 0
    return canvas


def box_roi(size: int = 36) -> tuple[dict, tuple[int, int]]:
    canvas_size = (size * 3, size * 3)
    roi = {"x": 1 / 3, "y": 1 / 3, "w": 1 / 3, "h": 1 / 3}
    return roi, canvas_size


def draw_cross(canvas: np.ndarray, size: int = 36) -> np.ndarray:
    """Diagonal cross through the box interior, 3px pen."""
    out = canvas.copy()
    n = size * 3
    for t in np.linspace(0, 1, 200):
        x = int(size * 1.15 + t * size * 0.7)
        y1 = int(size * 1.15 + t * size * 0.7)
        y2 = int(size * 1.85 - t * size * 0.7)
        out[max(0, y1 - 1):y1 + 2, max(0, x - 1):x + 2] = 0
        out[max(0, y2 - 1):y2 + 2, max(0, x - 1):x + 2] = 0
    assert out.shape == (n, n)
    return out


def test_empty_printed_box_not_inked():
    canvas = make_box_canvas()
    roi, (w, h) = box_roi()
    fill, inked = detect_box(canvas, roi, (w, h))
    assert fill < 0.02
    assert not inked


def test_crossed_box_is_inked():
    canvas = draw_cross(make_box_canvas())
    roi, (w, h) = box_roi()
    fill, inked = detect_box(canvas, roi, (w, h))
    assert fill > 0.06
    assert inked


def test_inset_excludes_border_even_for_tiny_boxes():
    # 6px box: inset must still leave a >0 interior and exclude the border.
    size = 6
    canvas = np.full((18, 18), 255, dtype=np.uint8)
    canvas[6:12, 6] = 0
    canvas[6:12, 11] = 0
    canvas[6, 6:12] = 0
    canvas[11, 6:12] = 0
    roi = {"x": 6 / 18, "y": 6 / 18, "w": 6 / 18, "h": 6 / 18}
    fill, inked = detect_box(canvas, roi, (18, 18))
    assert not inked


def test_measure_fill_empty_crop():
    assert measure_fill(np.empty((0, 0), dtype=np.uint8)) == 0.0


def test_roi_to_pixels_clamps():
    roi = {"x": -0.1, "y": 0.5, "w": 0.4, "h": 0.9}
    x1, y1, x2, y2 = roi_to_pixels(roi, (100, 100))
    assert x1 == 0 and y1 == 50
    assert x2 <= 100 and y2 <= 100


def test_select_one_picks_max_inked():
    results = {
        "defer_1w": (0.01, False),
        "defer_1m": (0.40, True),
        "defer_1q": (0.10, True),
    }
    assert select_one(results) == "defer_1m"


def test_select_one_none_inked():
    results = {"defer_1w": (0.0, False), "defer_1m": (0.01, False)}
    assert select_one(results) is None
