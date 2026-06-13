"""Unit tests for QR decoding helpers."""
from __future__ import annotations

import numpy as np
import pytest

from remarkable_gtd.scan.qr import OpenCVBackend, PyzbarBackend, decode_region, get_backend


def make_qr_image(text: str, canvas: tuple[int, int] = (400, 400),
                  pos: tuple[int, int] = (150, 150)) -> np.ndarray:
    """White canvas with one QR code pasted at a known location.

    The QR is pasted at its natural rendered size (crisp module edges) —
    no lossy resize, so both backends should read it.

    Returns:
        (canvas_img, size): the image and the pasted QR's pixel size.
    """
    import qrcode

    qr = qrcode.QRCode(border=2, box_size=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    arr = np.asarray(img)
    size = arr.shape[0]

    canvas_img = np.full(canvas, 255, dtype=np.uint8)
    x, y = pos
    canvas_img[y:y + size, x:x + size] = arr
    return canvas_img, size


def test_decode_region_finds_qr():
    img, size = make_qr_image("NA-05")
    roi = {"x": 150 / 400, "y": 150 / 400, "w": size / 400, "h": size / 400}
    assert decode_region(img, roi, (400, 400)) == "NA-05"


def test_decode_region_empty_area():
    img = np.full((400, 400), 255, dtype=np.uint8)
    roi = {"x": 0.25, "y": 0.25, "w": 0.25, "h": 0.25}
    assert decode_region(img, roi, (400, 400)) is None


def test_opencv_backend_decode_all():
    img, _ = make_qr_image("GTD|next|2026-05-30")
    results = OpenCVBackend().decode_all(img)
    texts = [t for t, _ in results]
    assert "GTD|next|2026-05-30" in texts


def test_pyzbar_backend_if_available():
    try:
        from pyzbar import pyzbar  # noqa: F401
    except Exception:
        pytest.skip("pyzbar/zbar not available")
    img, _ = make_qr_image("DG-01")
    results = PyzbarBackend().decode_all(img)
    texts = [t for t, _ in results]
    assert "DG-01" in texts


def test_get_backend_names():
    assert isinstance(get_backend("opencv"), OpenCVBackend)
    assert isinstance(get_backend("pyzbar"), PyzbarBackend)
    assert get_backend("auto") is not None
