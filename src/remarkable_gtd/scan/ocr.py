"""Pluggable OCR engines for handwriting fallback.

The vision pipeline only needs OCR where ink is present in a write-in
region (metadata slots, capture lines, or the action text of a row whose
Edit box is ticked). The engine is pluggable so a stronger backend (e.g. a
vision LLM) can slot in later; ``TesseractEngine`` is the offline default
and ``NullEngine`` keeps the trigger logic testable without OCR installed.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class OcrEngine(Protocol):
    """Protocol for OCR backends."""

    name: str

    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str:
        """Transcribe handwriting/text in an RGB image crop.

        Args:
            image_rgb: HxWx3 uint8 RGB crop (or HxW grayscale).
            hint: Optional region hint — ``"line"`` for single-line slots
                and capture lines, ``"block"`` for multi-line action text.

        Returns:
            The transcribed text (stripped); empty string if nothing read.
        """
        ...


class NullEngine:
    """No-op engine: flags regions for OCR without transcribing them."""

    name = "null"

    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str:
        return ""


class TesseractEngine:
    """Offline OCR via pytesseract (requires the tesseract binary)."""

    name = "tesseract"

    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str:
        import cv2
        import pytesseract

        img = image_rgb
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Upscale small crops — tesseract wants ~30px+ glyph height.
        h, w = img.shape[:2]
        if h < 60:
            scale = max(2, int(np.ceil(60 / max(1, h))))
            img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

        psm = 7 if hint == "line" else 6  # 7 = single line, 6 = block
        text = pytesseract.image_to_string(img, config=f"--psm {psm}")
        return text.strip()


_ENGINES: dict[str, type] = {
    "null": NullEngine,
    "tesseract": TesseractEngine,
}


def get_engine(name: str = "null") -> OcrEngine:
    """Instantiate an OCR engine by name (``"null"`` or ``"tesseract"``)."""
    try:
        return _ENGINES[name]()
    except KeyError:
        raise ValueError(
            f"Unknown OCR engine {name!r}; available: {sorted(_ENGINES)}"
        ) from None
