"""Scan pipeline orchestrator: image -> rectify -> QR -> ink -> OCR -> decisions.

The manifest produced by the generator is the source of truth for *where*
every box lives; the pipeline rectifies the photo into the manifest's
normalized frame (via the four corner registration marks) and then samples
ink at exact rectangles. OCR runs only where ink is present in a write-in
region.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from remarkable_gtd.common.schema import parse_page_key
from remarkable_gtd.scan import ink as ink_mod
from remarkable_gtd.scan import qr as qr_mod
from remarkable_gtd.scan.decisions import build_decisions, resolve_task
from remarkable_gtd.scan.manifest_io import get_page, list_page_keys
from remarkable_gtd.scan.ocr import get_engine
from remarkable_gtd.scan.rectify import find_reg_marks, rectify


@dataclass
class ScanConfig:
    """Tunables for the scan pipeline."""

    ink_fill_threshold: float = 0.06   # tick boxes
    slot_fill_threshold: float = 0.02  # write-in slots / capture lines (OCR trigger)
    inner_inset_frac: float = 0.22     # excludes the printed box border (tick boxes)
    slot_inset_frac: float = 0.08      # slots are wide; border is only ~3px,
                                       # and handwriting often starts at the edge
    ocr_engine: str = "null"
    canvas_width: int = 1404           # reMarkable 2 panel width in px


# ROI-key prefixes that are not per-task entries.
_SPECIAL_PREFIXES = ("reg:", "page:", "capture:")
# Per-task ROI suffixes that are not gutter tick boxes.
_NON_TICK_SUFFIXES = ("qr", "act")


def load_image(image_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Load a scan as (grayscale, otsu-binary) uint8 arrays.

    Accepts PNG/JPG (EXIF orientation honoured) and PDF (first page
    rasterized at 226 dpi via PyMuPDF).
    """
    import cv2

    image_path = Path(image_path)
    if image_path.suffix.lower() == ".pdf":
        import fitz  # PyMuPDF

        doc = fitz.open(image_path)
        pix = doc[0].get_pixmap(dpi=226, colorspace=fitz.csGRAY)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        gray = gray.copy()
        doc.close()
    else:
        from PIL import Image, ImageOps

        pil = Image.open(image_path)
        pil = ImageOps.exif_transpose(pil)
        gray = np.asarray(pil.convert("L"))

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return gray, binary


def detect_page_key(gray: np.ndarray, manifest: dict) -> str | None:
    """Find which manifest page this image is by decoding QR codes anywhere.

    Used before rectification when the caller didn't say which page the
    image shows. Returns the first decoded string that matches a manifest
    page key, or ``None``.
    """
    keys = set(list_page_keys(manifest))
    for backend in (qr_mod.OpenCVBackend(), qr_mod.PyzbarBackend()):
        for text, _poly in backend.decode_all(gray):
            if text in keys:
                return text
    return None


def task_ids_from_rois(rois: dict) -> list[str]:
    """Extract ordered task ids from a manifest page's ROI keys."""
    seen: dict[str, None] = {}
    for key in rois:
        if key.startswith(_SPECIAL_PREFIXES):
            continue
        task_id = key.split(":", 1)[0]
        seen.setdefault(task_id)
    return list(seen)


def _task_rois(rois: dict, task_id: str) -> dict[str, dict]:
    """All ROIs of one task, keyed by their verb/suffix (e.g. ``done``)."""
    prefix = f"{task_id}:"
    return {k[len(prefix):]: v for k, v in rois.items() if k.startswith(prefix)}


def run_scan(
    image_path: Path,
    manifest: dict,
    cfg: ScanConfig | None = None,
    page_key: str | None = None,
) -> dict:
    """Run the full pipeline on one sheet image.

    Args:
        image_path: Path to the scan (PNG/JPG/PDF first page).
        manifest: Parsed manifest dict (see ``manifest_io.load_manifest``).
        cfg: Pipeline tunables.
        page_key: Manifest page key (e.g. ``"GTD|next|2026-05-30"``). If
            omitted: auto-selected when the manifest has one page, else
            detected from QR codes in the image.

    Returns:
        The decisions document (schema ``gtd.decisions/1``).

    Raises:
        ValueError: If the page cannot be determined.
        RegistrationError: If the corner marks cannot be found.
    """
    cfg = cfg or ScanConfig()
    image_path = Path(image_path)
    gray, binary = load_image(image_path)

    # ---- which page is this? -------------------------------------------
    if page_key is None:
        keys = list_page_keys(manifest)
        if len(keys) == 1:
            page_key = keys[0]
        else:
            page_key = detect_page_key(gray, manifest)
            if page_key is None:
                raise ValueError(
                    "Could not determine the page: no QR matched a manifest "
                    f"key. Pass page_key explicitly; available: {keys}"
                )
    page = get_page(manifest, page_key)
    parsed = parse_page_key(page_key)
    bucket = page["bucket"]
    rois = page["rois"]
    warnings: list[str] = []

    # ---- rectify into the manifest frame --------------------------------
    marks = find_reg_marks(binary)
    warped_gray, warped_binary, canvas, residual = rectify(
        gray, binary, marks, page, canvas_width=cfg.canvas_width
    )
    rectify_meta = {"residual_px": residual, "reg_marks_found": len(marks)}
    if residual is None or residual > 10:
        warnings.append(
            f"rectification quality is poor (residual_px={residual}); "
            "tick/ink results may be unreliable — check the scan covers the "
            "whole page including all four corner marks"
        )

    # ---- header QR verification ------------------------------------------
    header_qr = qr_mod.decode_header(warped_gray, page, canvas)
    if header_qr and header_qr != page_key:
        warnings.append(
            f"header QR {header_qr!r} does not match selected page {page_key!r}"
        )
    elif not header_qr:
        warnings.append("header QR could not be decoded")

    # ---- per-task QR verification ----------------------------------------
    task_qrs = qr_mod.decode_task_qrs(warped_gray, page, canvas)

    ocr = get_engine(cfg.ocr_engine)

    def ocr_crop(roi: dict, hint: str, inset_frac: float = 0.0) -> str:
        """OCR a region; inset bordered regions so the printed box stroke
        doesn't get read as a character (e.g. '|')."""
        x1, y1, x2, y2 = ink_mod.roi_to_pixels(roi, canvas)
        if inset_frac > 0:
            ix = max(2, int((x2 - x1) * inset_frac))
            iy = max(2, int((y2 - y1) * inset_frac))
            x1, y1, x2, y2 = x1 + ix, y1 + iy, x2 - ix, y2 - iy
        if x2 <= x1 or y2 <= y1:
            return ""
        return ocr.read(warped_gray[y1:y2, x1:x2], hint=hint)

    # ---- tasks ------------------------------------------------------------
    tasks_out: list[dict] = []
    for task_id in task_ids_from_rois(rois):
        t_rois = _task_rois(rois, task_id)

        ticks: dict[str, tuple[float, bool]] = {}
        slot_rois: dict[str, dict] = {}
        for suffix, roi in t_rois.items():
            if suffix in _NON_TICK_SUFFIXES:
                continue
            if suffix.startswith("slot_"):
                slot_rois[suffix[len("slot_"):]] = roi
                continue
            ticks[suffix] = ink_mod.detect_box(
                warped_binary, roi, canvas,
                inner_inset_frac=cfg.inner_inset_frac,
                threshold=cfg.ink_fill_threshold,
            )

        edited = ticks.get("edit", (0.0, False))[1]

        # Slots: ink presence triggers OCR of that slot.
        field_texts: dict[str, dict] = {}
        for field, roi in slot_rois.items():
            fill, inked = ink_mod.detect_box(
                warped_binary, roi, canvas,
                inner_inset_frac=cfg.slot_inset_frac,
                threshold=cfg.slot_fill_threshold,
            )
            if inked:
                field_texts[field] = {
                    "text": ocr_crop(roi, "line", inset_frac=cfg.slot_inset_frac),
                    "fill": round(fill, 4),
                }

        # Edit ticked: re-read the (possibly amended) action text.
        act_text = None
        if edited and "act" in t_rois:
            act_text = ocr_crop(t_rois["act"], "block")

        entry, task_warnings = resolve_task(
            task_id, ticks, bucket,
            field_texts=field_texts or None,
            act_text=act_text,
        )
        qr_text = task_qrs.get(task_id)
        entry["qr_verified"] = qr_text == task_id
        tasks_out.append(entry)
        warnings.extend(task_warnings)

    # ---- capture lines ------------------------------------------------------
    captures_out: list[dict] = []
    capture_boxes = sorted(
        k for k in rois if k.startswith("capture:") and k.endswith(":box")
    )
    for box_key in capture_boxes:
        line_no = box_key.split(":")[1]  # "N1"
        box_roi = rois[box_key]
        line_roi = rois.get(f"capture:{line_no}:line")

        fill, box_inked = ink_mod.detect_box(
            warped_binary, box_roi, canvas,
            inner_inset_frac=cfg.inner_inset_frac,
            threshold=cfg.ink_fill_threshold,
        )

        # Writing area: the line right of the box (excludes the printed
        # "N1" label and checkbox), inset vertically to skip the printed
        # bottom border rule.
        write_fill = 0.0
        write_roi = None
        if line_roi is not None:
            wx = box_roi["x"] + box_roi["w"] * 1.5 - line_roi["x"]
            write_roi = {
                "x": line_roi["x"] + wx,
                "y": line_roi["y"],
                "w": max(0.0, line_roi["w"] - wx),
                "h": line_roi["h"],
            }
            wfill, _ = ink_mod.detect_box(
                warped_binary, write_roi, canvas,
                inner_inset_frac=0.12,
                threshold=cfg.slot_fill_threshold,
            )
            write_fill = wfill

        inked = box_inked or write_fill > cfg.slot_fill_threshold
        text = ""
        if inked and write_roi is not None:
            text = ocr_crop(write_roi, "line")

        captures_out.append({
            "line": line_no,
            "inked": inked,
            "text": text,
            "box_fill": round(fill, 4),
            "line_fill": round(write_fill, 4),
        })

    return build_decisions(
        bucket=bucket,
        the_date=parsed["date"],
        header_qr=header_qr,
        tasks=tasks_out,
        captures=captures_out,
        rectify_meta=rectify_meta,
        source_image=str(image_path),
        manifest_path=manifest.get("_path", ""),
        warnings=warnings,
    )
