"""Tests for registration mark detection and rectification."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from remarkable_gtd.scan.rectify import RegistrationError, find_reg_marks, rectify


def _make_cross(img: np.ndarray, cx: int, cy: int, size: int = 20, thickness: int = 2):
    """Draw a plus-shaped cross on the image."""
    half = size // 2
    cv2.line(img, (cx - half, cy), (cx + half, cy), 0, thickness)
    cv2.line(img, (cx, cy - half), (cx, cy + half), 0, thickness)


def test_find_reg_marks_synthetic():
    """Create a synthetic page image with known reg marks, apply a known warp,
    then assert find_reg_marks finds them and rectify recovers ≤2px residual."""
    h, w = 800, 600
    img = np.ones((h, w), dtype=np.uint8) * 255

    # Draw crosses in four corners
    marks_px = {
        "tl": (50, 50),
        "tr": (w - 50, 50),
        "bl": (50, h - 50),
        "br": (w - 50, h - 50),
    }
    for name, (cx, cy) in marks_px.items():
        _make_cross(img, cx, cy, size=30, thickness=3)

    # Apply a mild perspective transform (keystone)
    src_pts = np.float32([
        [0, 0], [w, 0], [0, h], [w, h]
    ])
    dst_pts = np.float32([
        [20, 10], [w - 15, 25], [5, h - 20], [w - 10, h - 15]
    ])
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(img, M, (w, h), borderValue=255)

    # Threshold to binary
    _, binary = cv2.threshold(warped, 200, 255, cv2.THRESH_BINARY)

    # Find marks
    found = find_reg_marks(binary)
    assert len(found) == 4

    for name in ["tl", "tr", "bl", "br"]:
        assert name in found
        # Should be close to the warped position
        expected = cv2.perspectiveTransform(
            np.float32([[marks_px[name]]]), M
        )[0][0]
        error = np.linalg.norm(np.array(found[name]) - expected)
        assert error < 5, f"{name} mark off by {error:.1f}px"


def test_find_reg_marks_occluded_corner():
    """Occlude one corner → RegistrationError."""
    h, w = 800, 600
    img = np.ones((h, w), dtype=np.uint8) * 255

    marks_px = {
        "tl": (50, 50),
        "tr": (w - 50, 50),
        "bl": (50, h - 50),
        "br": (w - 50, h - 50),
    }
    for name, (cx, cy) in marks_px.items():
        if name == "tr":
            continue  # occlude
        _make_cross(img, cx, cy, size=30, thickness=3)

    _, binary = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY)

    with pytest.raises(RegistrationError):
        find_reg_marks(binary)


def test_rectify_residual():
    """Apply known rotation+keystone, then rectify and check residual ≤2px."""
    h, w = 800, 600
    img = np.ones((h, w), dtype=np.uint8) * 255

    marks_px = {
        "tl": (50, 50),
        "tr": (w - 50, 50),
        "bl": (50, h - 50),
        "br": (w - 50, h - 50),
    }
    for name, (cx, cy) in marks_px.items():
        _make_cross(img, cx, cy, size=30, thickness=3)

    # Mild keystone
    src_pts = np.float32([
        [0, 0], [w, 0], [0, h], [w, h]
    ])
    dst_pts = np.float32([
        [20, 10], [w - 15, 25], [5, h - 20], [w - 10, h - 15]
    ])
    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped = cv2.warpPerspective(img, M, (w, h), borderValue=255)

    gray = warped.copy()
    _, binary = cv2.threshold(warped, 200, 255, cv2.THRESH_BINARY)

    found = find_reg_marks(binary)

    # Build a fake manifest page with reg marks at normalized positions
    manifest_page = {
        "render": {"w_px": w, "h_px": h},
        "rois": {
            "reg:tl": {"x": 50 / w, "y": 50 / h, "w": 0.05, "h": 0.05},
            "reg:tr": {"x": (w - 50) / w - 0.05, "y": 50 / h, "w": 0.05, "h": 0.05},
            "reg:bl": {"x": 50 / w, "y": (h - 50) / h - 0.05, "w": 0.05, "h": 0.05},
            "reg:br": {"x": (w - 50) / w - 0.05, "y": (h - 50) / h - 0.05, "w": 0.05, "h": 0.05},
            "page:qr": {"x": 0.5, "y": 0.1, "w": 0.1, "h": 0.1},
        },
    }

    warped_gray, warped_binary, canvas_size, residual = rectify(
        gray, binary, found, manifest_page
    )

    assert residual <= 2.0, f"Residual too large: {residual}px"
