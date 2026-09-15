"""Debug overlays: draw every manifest ROI onto the page the scanner sees.

Used to check by eye that the boxes the pipeline samples sit exactly on the
printed boxes. By default the page is put through the same registration /
rectification as a real scan, so the overlay shows what the scanner actually
measures; ``--raw`` skips that and draws onto the plain raster, which tells
a manifest problem apart from a rectification problem.

Colours:
    blue    tick boxes (gutter, defer trio, capture tick box); the thin
            inner rectangle is the region whose fill is measured
    red     write-in regions (metadata slots, capture lines)
    green   QR codes (header + per-row)
    magenta corner registration marks
    grey    action-text region (transcribed only when Edit is ticked, or
            always on a blank capture row)
    cyan    internal hyperlink targets on the project pages (never sampled)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from remarkable_gtd.scan.ink import roi_to_pixels

COLOURS_BGR = {
    "tick": (255, 80, 0),
    "slot": (0, 0, 255),
    "qr": (0, 170, 0),
    "reg": (200, 0, 200),
    "act": (140, 140, 140),
    "link": (0, 140, 200),
}


def classify(key: str) -> str:
    if key.startswith("reg:"):
        return "reg"
    if key.startswith("link:"):
        return "link"
    if key == "page:qr" or key.endswith(":qr"):
        return "qr"
    if key.endswith(":act"):
        return "act"
    if ":slot_" in key or key.endswith(":line"):
        return "slot"
    return "tick"


def draw_rois(
    gray: np.ndarray,
    rois: dict,
    canvas: tuple[int, int],
    inner_inset_frac: float = 0.22,
    slot_inset_frac: float = 0.15,
    labels: bool = False,
) -> np.ndarray:
    """Return a BGR image of ``gray`` with every ROI outlined."""
    import cv2

    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for key, roi in rois.items():
        kind = classify(key)
        colour = COLOURS_BGR[kind]
        x1, y1, x2, y2 = roi_to_pixels(roi, canvas)
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, 2)
        inset = inner_inset_frac if kind == "tick" else slot_inset_frac if kind == "slot" else None
        if inset is not None:
            ix = max(1, int(round((x2 - x1) * inset)))
            iy = max(1, int(round((y2 - y1) * inset)))
            if x2 - x1 > 2 * ix and y2 - y1 > 2 * iy:
                cv2.rectangle(img, (x1 + ix, y1 + iy), (x2 - ix, y2 - iy), colour, 1)
        if labels and kind in ("slot", "tick"):
            cv2.putText(img, key.split(":", 1)[-1], (x1, max(10, y1 - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, colour, 1, cv2.LINE_AA)
    return img


def overlay_page(
    image_path: Path,
    page: dict,
    raw: bool = False,
    canvas_width: int = 1404,
    labels: bool = False,
) -> tuple[np.ndarray, dict]:
    """Overlay one page. Returns ``(bgr_image, info)`` where ``info`` carries
    the registration residual (``None`` when ``raw``)."""
    from remarkable_gtd.scan.pipeline import load_image
    from remarkable_gtd.scan.rectify import find_reg_marks, rectify

    gray, binary = load_image(image_path)
    if raw:
        h, w = gray.shape[:2]
        return draw_rois(gray, page["rois"], (w, h), labels=labels), {"residual_px": None}
    marks = find_reg_marks(binary)
    warped_gray, _warped_binary, canvas, residual = rectify(
        gray, binary, marks, page, canvas_width=canvas_width
    )
    return draw_rois(warped_gray, page["rois"], canvas, labels=labels), {
        "residual_px": residual, "reg_marks_found": len(marks)
    }


def overlay_pdf(
    pdf_path: Path,
    manifest: dict,
    out_dir: Path,
    raw: bool = False,
    dpi: int = 226,
    labels: bool = False,
) -> list[Path]:
    """Overlay every page of a PDF (pages matched to manifest keys by order)."""
    import cv2
    import pymupdf

    from remarkable_gtd.scan.manifest_io import list_page_keys

    out_dir.mkdir(parents=True, exist_ok=True)
    keys = list_page_keys(manifest)
    doc = pymupdf.open(str(pdf_path))
    outputs: list[Path] = []
    for i, page in enumerate(doc):
        if i >= len(keys):
            break
        raster = out_dir / f"{pdf_path.stem}.page{i + 1}.png"
        page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72)).save(str(raster))
        img, info = overlay_page(raster, manifest["pages"][keys[i]], raw=raw, labels=labels)
        out = out_dir / f"{pdf_path.stem}.page{i + 1}.overlay.png"
        cv2.imwrite(str(out), img)
        raster.unlink()
        outputs.append(out)
        print(f"  {out.name}  {keys[i]}  residual_px={info.get('residual_px')}")
    doc.close()
    return outputs
