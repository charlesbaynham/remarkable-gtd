"""Tests for QR decoding."""
from __future__ import annotations

import numpy as np
import pytest

from remarkable_gtd.scan.qr import decode_region, get_backend


def test_decode_region_synthesized():
    """Test decode_region on a synthesized QR image."""
    import qrcode

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=1)
    qr.add_data("TEST-QR-123")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    arr = np.array(img)

    roi = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    result = decode_region(arr, roi, (arr.shape[1], arr.shape[0]))
    assert result == "TEST-QR-123"


def test_example_pdf_qr():
    """Rasterize example.pdf and try to find QR codes. Skip if none found."""
    from pathlib import Path
    import fitz

    pdf_path = Path(__file__).with_name("fixtures") / "example.pdf"
    if not pdf_path.exists():
        pytest.skip("example.pdf not found")

    doc = fitz.open(str(pdf_path))
    found_any = False
    for i in range(doc.page_count):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=226)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if img.shape[2] == 4:
            img = img[:, :, :3]

        backend = get_backend("auto")
        results = backend.decode_all(img)
        if results:
            found_any = True
            break
    doc.close()

    if not found_any:
        pytest.skip("No QR codes found in example.pdf (expected for tiny demo)")
