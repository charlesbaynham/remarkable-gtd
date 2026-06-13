"""Shared fixtures: render a real sheet, rasterize it, and paint synthetic ink.

The generator doubles as the scanner's ground-truth simulator: tests render
a sheet + manifest, rasterize the PDF, draw ticks/text into ROIs chosen
from the manifest, and assert the scanner recovers exactly those choices.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return bool(pw.chromium.executable_path)
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)


@pytest.fixture(scope="session")
def tasks_min() -> dict:
    return json.loads((FIXTURES / "tasks.min.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def rendered_sheet(tmp_path_factory, tasks_min):
    """Render tasks.min.json once per session -> (pdf_path, manifest_path)."""
    if not _chromium_available():
        pytest.skip("Playwright Chromium not installed")
    from remarkable_gtd.gen.generate import render_pdf

    out_dir = tmp_path_factory.mktemp("sheet")
    pdf_path = out_dir / "sheet.pdf"
    manifest_path = out_dir / "sheet.manifest.json"
    render_pdf(tasks_min, date(2026, 5, 30), pdf_path, manifest_path=manifest_path)
    assert pdf_path.exists() and manifest_path.exists()
    return pdf_path, manifest_path


@pytest.fixture(scope="session")
def manifest(rendered_sheet) -> dict:
    from remarkable_gtd.scan.manifest_io import load_manifest

    return load_manifest(rendered_sheet[1])


def rasterize_page(pdf_path: Path, page_index: int, dpi: int = 226) -> np.ndarray:
    """Rasterize one PDF page to an RGB uint8 ndarray via PyMuPDF."""
    import fitz

    doc = fitz.open(pdf_path)
    pix = doc[page_index].get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )[:, :, :3].copy()
    doc.close()
    return img


def paint_ink(
    img_rgb: np.ndarray,
    page_entry: dict,
    roi_key: str,
    style: str = "tick",
) -> np.ndarray:
    """Draw synthetic 'handwriting' into a manifest ROI.

    style: ``"tick"`` draws a diagonal cross filling the box interior;
    ``"text:<str>"`` writes the string into the region.
    """
    from PIL import Image, ImageDraw

    roi = page_entry["rois"][roi_key]
    h, w = img_rgb.shape[:2]
    x1 = int(roi["x"] * w)
    y1 = int(roi["y"] * h)
    x2 = int((roi["x"] + roi["w"]) * w)
    y2 = int((roi["y"] + roi["h"]) * h)

    pil = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil)

    if style == "tick":
        # Diagonal cross across the interior, pen-like 3px stroke.
        pad_x = max(2, (x2 - x1) // 5)
        pad_y = max(2, (y2 - y1) // 5)
        draw.line([x1 + pad_x, y1 + pad_y, x2 - pad_x, y2 - pad_y], fill=0, width=3)
        draw.line([x1 + pad_x, y2 - pad_y, x2 - pad_x, y1 + pad_y], fill=0, width=3)
    elif style.startswith("text:"):
        text = style[len("text:"):]
        # Scale the text to the region height like real handwriting would
        # be, and draw inside the writable interior (clear of the border).
        from PIL import ImageFont

        size = max(10, int((y2 - y1) * 0.55))
        font = ImageFont.load_default(size=size)
        draw.text(
            (x1 + max(6, (x2 - x1) // 8), y1 + (y2 - y1 - size) // 2),
            text, fill=0, font=font,
        )
    else:
        raise ValueError(f"unknown ink style {style!r}")

    return np.asarray(pil)


def warp_image(img_rgb: np.ndarray, angle_deg: float = 2.0, keystone: float = 0.01) -> np.ndarray:
    """Apply a small known rotation + perspective so rectification has work to do.

    The canvas is padded first so no page content (in particular the corner
    registration marks of a tall page) rotates off the image edge.
    """
    import cv2

    h, w = img_rgb.shape[:2]
    # Pad enough for the corner sweep of a tall page under small rotation.
    pad = int(max(w, h) * abs(np.sin(np.radians(angle_deg)))) + int(keystone * w) + 8
    padded = cv2.copyMakeBorder(
        img_rgb, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )
    ph, pw = padded.shape[:2]
    # Rotate about centre with white border fill.
    M = cv2.getRotationMatrix2D((pw / 2, ph / 2), angle_deg, 1.0)
    out = cv2.warpAffine(padded, M, (pw, ph), borderValue=(255, 255, 255))
    # Mild keystone.
    dx = keystone * pw
    src = np.float32([[0, 0], [pw, 0], [0, ph], [pw, ph]])
    dst = np.float32([[dx, 0], [pw - dx, 0], [0, ph], [pw, ph]])
    P = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(out, P, (pw, ph), borderValue=(255, 255, 255))
