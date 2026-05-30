"""OCR engine protocol + implementations."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class OcrEngine(Protocol):
    name: str
    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str: ...


class NullEngine:
    name = "null"

    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str:
        return ""


class TesseractEngine:
    name = "tesseract"

    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str:
        import pytesseract

        if hint in ("slot", "single_line"):
            config = "--psm 7"
        elif hint == "paragraph":
            config = "--psm 6"
        else:
            config = "--psm 7"
        try:
            text = pytesseract.image_to_string(image_rgb, config=config)
            return text.strip()
        except Exception:
            return ""


def get_engine(name: str = "null") -> OcrEngine:
    if name == "null":
        return NullEngine()
    if name == "tesseract":
        return TesseractEngine()
    raise ValueError(f"Unknown OCR engine: {name}")
