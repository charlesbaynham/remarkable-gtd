"""QR code decoding for the scan pipeline."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class QRBackend(Protocol):
    """Protocol for QR code decoder backends."""

    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]:
        """Decode all QR codes in an image.

        Args:
            img: Grayscale or BGR uint8 image.

        Returns:
            List of ``(data_str, polygon_points)`` tuples.
        """
        ...


class OpenCVBackend:
    """QR backend using ``cv2.QRCodeDetector``."""

    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]:
        import cv2

        detector = cv2.QRCodeDetector()
        try:
            retval, decoded_info, points, _ = detector.detectAndDecodeMulti(img)
        except Exception:
            retval = False

        if not retval:
            return []

        results = []
        for text, pts in zip(decoded_info, points if points is not None else []):
            if text:
                poly = pts.tolist() if hasattr(pts, "tolist") else list(pts)
                results.append((text, poly))
        return results


class PyzbarBackend:
    """QR backend using ``pyzbar``."""

    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]:
        try:
            from pyzbar import pyzbar
        except ImportError:
            return []

        import cv2

        # pyzbar works best on grayscale
        if img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img

        try:
            objects = pyzbar.decode(gray)
        except Exception:
            return []

        results = []
        for obj in objects:
            text = obj.data.decode("utf-8", errors="replace")
            poly = [(p.x, p.y) for p in obj.polygon]
            results.append((text, poly))
        return results


def get_backend(name: str = "auto") -> QRBackend:
    """Get a QR decoder backend by name.

    Args:
        name: ``"auto"``, ``"opencv"``, or ``"pyzbar"``.
            ``"auto"`` tries OpenCV first, falls back to pyzbar.

    Returns:
        A :class:`QRBackend` instance.
    """
    if name == "pyzbar":
        return PyzbarBackend()
    if name == "opencv":
        return OpenCVBackend()
    # auto: prefer OpenCV (always available), use pyzbar as additional pass
    return OpenCVBackend()


def decode_region(
    img: np.ndarray,
    roi: dict,
    canvas_size: tuple[int, int],
) -> str | None:
    """Decode a QR code from a specific ROI in a rectified image.

    Tries OpenCV then pyzbar on the cropped region (with a small margin).

    Args:
        img: Full rectified image (grayscale or BGR).
        roi: Normalised rect ``{x, y, w, h}`` (fractions 0..1).
        canvas_size: ``(canvas_w, canvas_h)`` in pixels.

    Returns:
        Decoded string, or ``None`` if no QR found.
    """
    import cv2

    canvas_w, canvas_h = canvas_size
    # Add 20% margin around the ROI for robustness
    margin_x = roi["w"] * canvas_w * 0.2
    margin_y = roi["h"] * canvas_h * 0.2

    x1 = max(0, int(roi["x"] * canvas_w - margin_x))
    y1 = max(0, int(roi["y"] * canvas_h - margin_y))
    x2 = min(canvas_w, int((roi["x"] + roi["w"]) * canvas_w + margin_x))
    y2 = min(canvas_h, int((roi["y"] + roi["h"]) * canvas_h + margin_y))

    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    # Upscale small crops for better detection
    h, w = crop.shape[:2]
    if max(h, w) < 100:
        scale = 150 / max(h, w)
        crop = cv2.resize(crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    for backend in (OpenCVBackend(), PyzbarBackend()):
        results = backend.decode_all(crop)
        if results:
            return results[0][0]
    return None


def decode_header(
    rectified: np.ndarray,
    manifest_page: dict,
    canvas_size: tuple[int, int],
) -> str:
    """Decode the page-level header QR code (``page:qr`` ROI).

    Args:
        rectified: Rectified grayscale image.
        manifest_page: Single page entry from the manifest.
        canvas_size: ``(canvas_w, canvas_h)``.

    Returns:
        Decoded QR string (e.g. ``"GTD|inbox|2026-05-30"``), or empty string.
    """
    rois = manifest_page.get("rois", {})
    roi = rois.get("page:qr")
    if roi is None:
        return ""
    result = decode_region(rectified, roi, canvas_size)
    return result or ""


def decode_task_qrs(
    rectified: np.ndarray,
    manifest_page: dict,
    canvas_size: tuple[int, int],
) -> dict[str, str]:
    """Decode all per-task QR codes from their ``<id>:qr`` ROIs.

    Args:
        rectified: Rectified grayscale image.
        manifest_page: Single page entry from the manifest.
        canvas_size: ``(canvas_w, canvas_h)``.

    Returns:
        Dict mapping task id to decoded QR string (only tasks where decode
        succeeded are included).
    """
    rois = manifest_page.get("rois", {})
    results: dict[str, str] = {}
    for key, roi in rois.items():
        if not key.endswith(":qr") or key == "page:qr":
            continue
        task_id = key[: -len(":qr")]
        decoded = decode_region(rectified, roi, canvas_size)
        if decoded:
            results[task_id] = decoded
    return results
