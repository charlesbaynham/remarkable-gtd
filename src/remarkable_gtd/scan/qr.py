"""QR code decoding with pluggable backends."""
from __future__ import annotations

from typing import Protocol

import cv2
import numpy as np


class QRBackend(Protocol):
    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]:
        """Return list of (data, points quadrilateral)."""
        ...


class OpenCVBackend:
    def __init__(self):
        self.detector = cv2.QRCodeDetector()

    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]:
        results = []
        data, pts, _ = self.detector.detectAndDecode(img)
        if data and pts is not None:
            for d, p in zip([data] if isinstance(data, str) else data, pts):
                if d:
                    results.append((d, p.tolist()))
        return results


class PyzbarBackend:
    def __init__(self):
        try:
            from pyzbar import pyzbar as _pyzbar
            self._pyzbar = _pyzbar
        except ImportError as exc:
            raise ImportError("pyzbar not installed") from exc

    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]:
        results = []
        codes = self._pyzbar.decode(img)
        for code in codes:
            text = code.data.decode("utf-8") if isinstance(code.data, bytes) else str(code.data)
            # pyzbar gives rect; approximate as 4 corners
            x, y, w, h = code.rect
            pts = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            results.append((text, pts))
        return results


def get_backend(name: str = "auto") -> QRBackend:
    if name == "opencv":
        return OpenCVBackend()
    if name == "pyzbar":
        return PyzbarBackend()
    if name == "auto":
        try:
            return OpenCVBackend()
        except Exception:
            pass
        try:
            return PyzbarBackend()
        except Exception:
            pass
        raise RuntimeError("No QR backend available")
    raise ValueError(f"Unknown QR backend: {name}")


def decode_region(img: np.ndarray, roi: dict, canvas_size: tuple) -> str | None:
    """Decode QR in a specific ROI region."""
    from .ink import roi_to_pixels
    x1, y1, x2, y2 = roi_to_pixels(roi, canvas_size)
    # Add margin
    h, w = img.shape[:2]
    margin = 10
    x1 = max(0, x1 - margin)
    y1 = max(0, y1 - margin)
    x2 = min(w, x2 + margin)
    y2 = min(h, y2 + margin)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    backend = get_backend("auto")
    results = backend.decode_all(crop)
    return results[0][0] if results else None


def decode_header(rectified: np.ndarray, manifest_page: dict, canvas_size: tuple) -> str:
    """Decode header QR and return page key."""
    roi = manifest_page["rois"].get("page:qr")
    if roi is None:
        raise ValueError("page:qr ROI not found in manifest")
    text = decode_region(rectified, roi, canvas_size)
    if text is None:
        raise ValueError("Could not decode header QR")
    return text


def decode_task_qrs(rectified: np.ndarray, manifest_page: dict, canvas_size: tuple) -> dict[str, str]:
    """Decode all task row QR codes. Returns {task_id: decoded_text}."""
    results = {}
    for key, roi in manifest_page.get("rois", {}).items():
        if ":qr" in key and not key.startswith("page:"):
            task_id = key.split(":")[0]
            text = decode_region(rectified, roi, canvas_size)
            if text:
                results[task_id] = text
    return results
