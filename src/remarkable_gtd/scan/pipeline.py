"""Scan pipeline orchestrator: image -> rectify -> QR -> ink -> OCR -> decisions.

The manifest produced by the generator is the source of truth for *where*
every box lives; the pipeline rectifies the photo into the manifest's
normalized frame (via the four corner registration marks) and then samples
ink at exact rectangles. Handwriting recognition runs only where ink is
present in a write-in region, on a tight crop of that region.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from remarkable_gtd.common.schema import parse_page_key
from remarkable_gtd.scan import ink as ink_mod
from remarkable_gtd.scan import qr as qr_mod
from remarkable_gtd.scan.decisions import (
    ai_requested,
    build_decisions,
    build_suggestion,
    resolve_task,
)
from remarkable_gtd.scan.manifest_io import get_page, list_page_keys
from remarkable_gtd.scan.ocr import OcrEngine, get_engine
from remarkable_gtd.scan.rectify import find_reg_marks, rectify


@dataclass
class ScanConfig:
    """Tunables for the scan pipeline."""

    ink_fill_threshold: float = 0.06   # tick boxes
    # Write-in slots / capture rows (the OCR trigger). Unchanged when the
    # slots grew to stylus size: a single handwritten digit in a 10x8 mm
    # PRIORITY box is roughly 6 mm tall by 3 mm wide of stroke-covered area,
    # still ~6 % fill — comfortably above 0.03 — and a written word or date
    # far more. Raising it would only start losing sparse handwriting.
    slot_fill_threshold: float = 0.03
    inner_inset_frac: float = 0.22     # excludes the printed box border (tick boxes)
    slot_inset_frac: float = 0.15      # slots are wide; the border is only ~3px but
                                       # a 1px scale mismatch would leak it in
    ocr_engine: str | OcrEngine = "null"  # engine name, or an engine instance
    canvas_width: int = 1404           # reMarkable 2 panel width in px


# ROI-key prefixes that are not per-task entries.
_SPECIAL_PREFIXES = ("reg:", "page:", "capture:", "link:")
# Per-task ROI suffixes that are not gutter tick boxes.
_NON_TICK_SUFFIXES = ("qr", "act", "row")


def load_image(image_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Load a scan as (grayscale, otsu-binary) uint8 arrays.

    Accepts PNG/JPG (EXIF orientation honoured) and PDF (first page
    rasterized at 226 dpi via PyMuPDF).
    """
    import cv2

    image_path = Path(image_path)
    if image_path.suffix.lower() == ".pdf":
        import pymupdf

        doc = pymupdf.open(image_path)
        pix = doc[0].get_pixmap(dpi=226, colorspace=pymupdf.csGRAY)
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


def row_roi(
    task_rois: dict, page_w_frac_pad: float = 0.01, page_h_frac_pad: float = 0.003
) -> dict:
    """The ROI covering a task's whole row, for a ✦-AI crop.

    Uses the generator's own ``row`` ROI when present. Sheets printed before
    it existed carry no ``row`` ROI, so the fallback unions every ROI the
    task has (gutter ticks, slots, act, qr) and pads it slightly, since the
    printed row extends a little beyond its tightest bounding box.
    """
    if "row" in task_rois:
        return task_rois["row"]
    x1 = min(r["x"] for r in task_rois.values()) - page_w_frac_pad
    y1 = min(r["y"] for r in task_rois.values()) - page_h_frac_pad
    x2 = max(r["x"] + r["w"] for r in task_rois.values()) + page_w_frac_pad
    y2 = max(r["y"] + r["h"] for r in task_rois.values()) + page_h_frac_pad
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(1.0, x2), min(1.0, y2)
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


_FAILED_AI = {
    "handwriting": "",
    "understood": False,
    "confidence": 0.0,
    "operations": [],
}


def _ai_act_text(reading: dict) -> str | None:
    """The new wording an understood AI reading asks for, if any.

    ``gtd.ai/3`` has no top-level text: new wording travels on whichever
    operation carries it (an ``update``, or the ``text`` of a re-route).
    """
    if not reading.get("understood"):
        return None
    for op in reading.get("operations") or []:
        if isinstance(op, dict) and op.get("text"):
            return op["text"]
    return None


def _call_interpret(interpret_fn, image, task_entry, vocabulary, today, suggestion):
    """Call an engine's ``interpret``, passing ``suggestion`` if it takes one.

    The keyword arrived with the AI escape hatch; an engine written against
    the older signature still works, just without the deterministic hint.
    """
    import inspect

    kwargs: dict = {}
    try:
        if "suggestion" in inspect.signature(interpret_fn).parameters:
            kwargs["suggestion"] = suggestion
    except (TypeError, ValueError):  # a builtin or C-implemented callable
        pass
    return interpret_fn(image, task_entry, vocabulary, today, **kwargs)


def run_scan(
    image_path: Path,
    manifest: dict,
    cfg: ScanConfig | None = None,
    page_key: str | None = None,
    tasks: dict | None = None,
) -> dict:
    """Run the full pipeline on one sheet image.

    Args:
        image_path: Path to the scan (PNG/JPG/PDF first page).
        manifest: Parsed manifest dict (see ``manifest_io.load_manifest``).
        cfg: Pipeline tunables.
        page_key: Manifest page key (e.g. ``"GTD|next|2026-05-30"``). If
            omitted: auto-selected when the manifest has one page, else
            detected from QR codes in the image.
        tasks: The ``gtd.tasks/1`` document (``{"tasks": {id: entry},
            "context": {...}}``). Entries give the printed text for the
            legacy act-crop OCR fallback and the fields/vocabulary handed to
            an engine's ``interpret`` when a row's ✦ AI box is ticked.

    Returns:
        The decisions document (schema ``gtd.decisions/1``).

    Raises:
        ValueError: If the page cannot be determined.
        RegistrationError: If the corner marks cannot be found.
    """
    cfg = cfg or ScanConfig()
    tasks = tasks or {}
    task_entries = tasks.get("tasks") or {}
    vocabulary = tasks.get("context")
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

    def ocr_crop(roi: dict, hint: str, inset_px: int = 3, context: str | None = None) -> str:
        """Transcribe a region: crop just inside the printed border so the
        box stroke is not read as a character, and hand the engine a hint
        about what the region is."""
        x1, y1, x2, y2 = ink_mod.roi_to_pixels(roi, canvas)
        x1, y1, x2, y2 = x1 + inset_px, y1 + inset_px, x2 - inset_px, y2 - inset_px
        if x2 <= x1 or y2 <= y1:
            return ""
        return ocr.read(warped_gray[y1:y2, x1:x2], hint=hint, context=context)

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

        ai = ai_requested(ticks)

        # Slots: ink presence triggers OCR of that slot.
        field_texts: dict[str, dict] = {}
        for field, roi in slot_rois.items():
            fill, inked = ink_mod.detect_box(
                warped_binary, roi, canvas,
                inner_inset_frac=cfg.slot_inset_frac,
                threshold=cfg.slot_fill_threshold,
            )
            if inked:
                context = None
                if field == "project" and vocabulary and vocabulary.get("projects"):
                    context = ", ".join(vocabulary["projects"])
                field_texts[field] = {
                    "text": ocr_crop(roi, field, context=context),
                    "fill": round(fill, 4),
                }

        task_entry = task_entries.get(task_id, {})
        task_bucket = task_entry.get("bucket") or bucket

        # A capture row prints no text: its whole action area is the
        # write-in region. Measure it with the slot thresholds and
        # transcribe it only if there is ink.
        act_text = None
        capture_inked: bool | None = None
        if task_bucket in ("capture", "newproj") and "act" in t_rois:
            fill, capture_inked = ink_mod.detect_box(
                warped_binary, t_rois["act"], canvas,
                inner_inset_frac=cfg.slot_inset_frac,
                threshold=cfg.slot_fill_threshold,
            )
            if capture_inked:
                act_text = ocr_crop(t_rois["act"], "capture", inset_px=0)

        # ✦ AI ticked: the deterministic classifier is switched off for
        # this row — it still runs, but only to produce the `suggestion`
        # handed to the agent as a labelled hint. The agent's operations
        # are the row's single write (see decisions.resolve_task).
        ai_reading: dict | None = None
        if ai:
            suggestion = build_suggestion(
                task_id, ticks, task_bucket, field_texts or None, act_text
            )
            interpret_fn = getattr(ocr, "interpret", None)
            if interpret_fn is not None:
                roi = row_roi(t_rois)
                x1, y1, x2, y2 = ink_mod.roi_to_pixels(roi, canvas)
                try:
                    ai_reading = _call_interpret(
                        interpret_fn, warped_gray[y1:y2, x1:x2],
                        task_entry, vocabulary, parsed["date"], suggestion,
                    )
                except Exception as exc:
                    ai_reading = {**_FAILED_AI, "note": f"interpretation failed: {exc}"}
                    warnings.append(f"{task_id}: AI interpretation failed — {exc}")
            if interpret_fn is None or ai_reading is None:
                if "act" in t_rois:
                    act_text = ocr_crop(
                        t_rois["act"], "act", inset_px=0, context=task_entry.get("act")
                    )
            else:
                new_text = _ai_act_text(ai_reading)
                if new_text is not None:
                    act_text = new_text

        entry, task_warnings = resolve_task(
            task_id, ticks, task_bucket,
            field_texts=field_texts or None,
            act_text=act_text,
            ai_reading=ai_reading,
        )
        if capture_inked is not None:
            entry["inked"] = capture_inked
        qr_text = task_qrs.get(task_id)
        entry["qr_verified"] = qr_text == task_id
        tasks_out.append(entry)
        warnings.extend(task_warnings)

    # ---- legacy capture lines ------------------------------------------
    # Sheets printed before capture lines became full rows carry
    # ``capture:N1:box`` / ``:line`` ROIs instead. New sheets have none, so
    # this loop yields an empty list.
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
            text = ocr_crop(write_roi, "capture")

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
