"""Unit tests for ink detection without rendering."""

from __future__ import annotations

import numpy as np

from remarkable_gtd.scan.ink import detect_box, measure_fill, select_one


def test_white_box_no_fill():
    """White 36×36 crop with a 2px black border → measure_fill ≈ 0, detect_box inked=False."""
    crop = np.ones((36, 36), dtype=np.uint8) * 255
    # 2px black border
    crop[:2, :] = 0
    crop[-2:, :] = 0
    crop[:, :2] = 0
    crop[:, -2:] = 0

    fill = measure_fill(crop)
    # The border is a small fraction; after inset it should be near zero
    assert fill < 0.25  # 2px border on 36×36 ≈ 21% of total

    # detect_box with default inset of 0.22 should exclude border
    roi = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    fill2, inked = detect_box(
        crop, roi, (36, 36), inner_inset_frac=0.22, threshold=0.06
    )
    assert fill2 < 0.06
    assert inked is False


def test_diagonal_cross_inked():
    """Same crop with a diagonal cross drawn → detect_box inked=True."""
    crop = np.ones((36, 36), dtype=np.uint8) * 255
    # 2px black border
    crop[:2, :] = 0
    crop[-2:, :] = 0
    crop[:, :2] = 0
    crop[:, -2:] = 0
    # Diagonal cross
    for i in range(36):
        crop[i, i] = 0
        if i + 1 < 36:
            crop[i, i + 1] = 0
        if i > 0:
            crop[i, i - 1] = 0
    for i in range(36):
        crop[i, 35 - i] = 0
        if 35 - i + 1 < 36:
            crop[i, 35 - i + 1] = 0
        if 35 - i > 0:
            crop[i, 35 - i - 1] = 0

    roi = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    fill, inked = detect_box(crop, roi, (36, 36), inner_inset_frac=0.22, threshold=0.06)
    assert inked is True


def test_select_one_only_one_inked():
    """Only one of three boxes inked → returns that key."""
    results = {
        "a": (0.02, False),
        "b": (0.12, True),
        "c": (0.01, False),
    }
    assert select_one(results) == "b"


def test_select_one_none_inked():
    """None inked → None."""
    results = {
        "a": (0.02, False),
        "b": (0.01, False),
        "c": (0.03, False),
    }
    assert select_one(results) is None
