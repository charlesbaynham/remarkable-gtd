"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def tasks_min_path() -> Path:
    return Path(__file__).with_name("fixtures") / "tasks.min.json"


@pytest.fixture(scope="session")
def _playwright_available():
    pytest.importorskip("playwright.sync_api")


@pytest.fixture
def rendered_sheet(tmp_path, tasks_min_path, _playwright_available):
    """Render tasks.min.json → (pdf_path, manifest_path)."""
    from datetime import date
    from remarkable_gtd.gen.generate import render_pdf
    import json

    data = json.loads(tasks_min_path.read_text(encoding="utf-8"))
    pdf_path = tmp_path / "sheet.pdf"
    manifest_path = tmp_path / "sheet.manifest.json"
    render_pdf(data, date(2026, 5, 30), pdf_path, manifest_path=manifest_path)
    return pdf_path, manifest_path


def rasterize_page(pdf_path: Path, page_index: int = 0, dpi: int = 226) -> np.ndarray:
    """Rasterize a PDF page to RGB ndarray via PyMuPDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(pdf_path))
    page = doc.load_page(page_index)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    doc.close()
    if img.shape[2] == 4:
        # RGBA → RGB
        img = img[:, :, :3]
    return img


def paint_ink(
    img_rgb: np.ndarray,
    manifest_page: dict,
    roi_key: str,
    style: str = "tick",
) -> np.ndarray:
    """Draw synthetic ink into an ROI rect.

    style:
        - "tick": diagonal cross
        - "text:<str>": write text
    """
    from PIL import Image, ImageDraw, ImageFont
    from remarkable_gtd.scan.ink import roi_to_pixels

    roi = manifest_page["rois"][roi_key]
    # Use actual image dimensions, not manifest render dims
    canvas_size = (img_rgb.shape[1], img_rgb.shape[0])
    x1, y1, x2, y2 = roi_to_pixels(roi, canvas_size)

    pil = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil)

    if style == "tick":
        draw.line([(x1, y1), (x2, y2)], fill=(0, 0, 0), width=3)
        draw.line([(x1, y2), (x2, y1)], fill=(0, 0, 0), width=3)
    elif style.startswith("text:"):
        text = style[5:]
        # Try to get a font, fall back to default
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            font = ImageFont.load_default()
        draw.text((x1 + 2, y1 + 2), text, fill=(0, 0, 0), font=font)

    return np.array(pil)
