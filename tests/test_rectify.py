"""Unit tests for reg-mark detection and rectification on synthetic pages."""
from __future__ import annotations

import numpy as np
import pytest

from remarkable_gtd.scan.rectify import RegistrationError, find_reg_marks, rectify


W, H = 1404, 1872  # reMarkable 2 panel
MARK = 45          # ~5mm at 226 dpi
BAR = 6            # ~0.6mm
INSET = 36         # ~4mm


def draw_bracket(img: np.ndarray, corner: str) -> None:
    """Draw one L-shaped corner bracket as printed by gtd.css .reg rules."""
    h, w = img.shape
    if corner == "tl":
        x, y = INSET, INSET
        img[y:y + BAR, x:x + MARK] = 0          # horizontal along top
        img[y:y + MARK, x:x + BAR] = 0          # vertical along left
    elif corner == "tr":
        x, y = w - INSET - MARK, INSET
        img[y:y + BAR, x:x + MARK] = 0
        img[y:y + MARK, x + MARK - BAR:x + MARK] = 0
    elif corner == "bl":
        x, y = INSET, h - INSET - MARK
        img[y + MARK - BAR:y + MARK, x:x + MARK] = 0
        img[y:y + MARK, x:x + BAR] = 0
    else:  # br
        x, y = w - INSET - MARK, h - INSET - MARK
        img[y + MARK - BAR:y + MARK, x:x + MARK] = 0
        img[y:y + MARK, x + MARK - BAR:x + MARK] = 0


def synthetic_page() -> np.ndarray:
    img = np.full((H, W), 255, dtype=np.uint8)
    for c in ("tl", "tr", "bl", "br"):
        draw_bracket(img, c)
    # some body content that must not be confused with the marks
    img[400:404, 100:1300] = 0
    img[800:806, 100:1300] = 0
    return img


def expected_centers() -> dict[str, tuple[float, float]]:
    c = INSET + MARK / 2
    return {
        "tl": (c, c),
        "tr": (W - c, c),
        "bl": (c, H - c),
        "br": (W - c, H - c),
    }


def synthetic_manifest_page() -> dict:
    """Manifest entry whose reg ROIs match the synthetic page geometry."""
    rois = {}
    for name, (cx, cy) in expected_centers().items():
        rois[f"reg:{name}"] = {
            "x": (cx - MARK / 2) / W,
            "y": (cy - MARK / 2) / H,
            "w": MARK / W,
            "h": MARK / H,
        }
    return {"bucket": "next", "page_no": 2,
            "render": {"w_px": W, "h_px": H}, "rois": rois}


def test_find_reg_marks_clean_page():
    img = synthetic_page()
    marks = find_reg_marks(img)
    for name, (ex, ey) in expected_centers().items():
        cx, cy = marks[name]
        assert abs(cx - ex) <= 2, f"{name} x off by {abs(cx - ex)}"
        assert abs(cy - ey) <= 2, f"{name} y off by {abs(cy - ey)}"


def test_find_reg_marks_after_known_warp():
    import cv2

    img = synthetic_page()
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 3.0, 0.97)
    rotated = cv2.warpAffine(img, M, (W, H), borderValue=255)
    marks = find_reg_marks(rotated)
    assert set(marks) == {"tl", "tr", "bl", "br"}


def test_rectify_recovers_geometry():
    import cv2

    img = synthetic_page()
    M = cv2.getRotationMatrix2D((W / 2, H / 2), 2.0, 0.98)
    rotated = cv2.warpAffine(img, M, (W, H), borderValue=255)

    marks = find_reg_marks(rotated)
    page = synthetic_manifest_page()
    _, warped_binary, (cw, ch), residual = rectify(rotated, rotated, marks, page)

    assert (cw, ch) == (1404, 1872)
    assert residual is not None
    assert residual <= 2.0, f"residual {residual}px too large"

    # Marks in the rectified image should sit at their expected positions.
    remarks = find_reg_marks(warped_binary)
    for name, (ex, ey) in expected_centers().items():
        cx, cy = remarks[name]
        assert abs(cx - ex) <= 2.5
        assert abs(cy - ey) <= 2.5


def test_missing_corner_raises():
    img = synthetic_page()
    # occlude the br bracket entirely with white
    img[H - INSET - MARK - 5:, W - INSET - MARK - 5:] = 255
    with pytest.raises(RegistrationError) as exc:
        find_reg_marks(img)
    assert "br" in str(exc.value)
