"""Scanning a reference-note sheet: the ✦ AI box, the notes page, ink elsewhere.

A reference page has no gutter to read. What the user can do is write on the
notes page (RF-C* lines, read like capture rows), write anywhere else on the
page, or tick ✦ AI to have the page interpreted. Ink anywhere else is only
*reported* (``ink_elsewhere``): nothing is read from the body without the AI
box, and the AI is the page's only writer, as on the daily sheet.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from remarkable_gtd.scan import ink as ink_mod
from remarkable_gtd.scan import qr as qr_mod
from remarkable_gtd.scan.decisions import build_decisions
from remarkable_gtd.scan.ocr import get_engine
from remarkable_gtd.scan.pipeline import (
    ScanConfig, crop_text, detect_write_in, prepare_page, task_ids_from_rois,
)

_FAILED_REF_AI = {"handwriting": "", "understood": False, "confidence": 0.0, "operations": []}


def _ink_elsewhere(prep, cfg: ScanConfig, floor_px: int, strokes: bool) -> tuple[float, bool]:
    """Ink in the body area, outside the notes page's own write-in rows."""
    binary = prep.ink_binary
    row_rois = [r for k, r in prep.page["rois"].items() if k.endswith(":row")]
    if row_rois:
        binary = binary.copy()
        for roi in row_rois:
            x1, y1, x2, y2 = ink_mod.roi_to_pixels(roi, prep.canvas)
            binary[y1:y2, x1:x2] = 255
    body = prep.page["rois"]["page:body"]
    if strokes:
        return ink_mod.detect_box(
            binary, body, prep.canvas, inner_inset_frac=0.0, threshold=1.0, min_ink_px=floor_px
        )
    # No stroke layer: the printed text is in the measurement too, so only
    # a body far fuller than any printed page can be called inked.
    fill, _ = ink_mod.detect_box(binary, body, prep.canvas, inner_inset_frac=0.0, threshold=1.0)
    return fill, fill > cfg.printed_body_fill_ceiling


def _interpretation_image(prep) -> tuple[np.ndarray, bool]:
    """The page as annotated, beside the handwriting alone when we have it."""
    if prep.strokes_gray is None:
        return prep.gray, False
    return np.hstack([prep.gray, prep.strokes_gray]), True


def run_reference_scan(
    image_path: Path,
    manifest: dict,
    cfg: ScanConfig | None = None,
    page_key: str | None = None,
    tasks: dict | None = None,
    strokes_path: Path | None = None,
    page_text: str = "",
) -> dict:
    """Scan one page of a reference sheet.

    Args:
        image_path: Raster of the page as annotated (PNG/JPG/PDF).
        strokes_path: Raster of the handwriting alone, white page and black
            ink, same geometry. With it ink is measured without the printed
            page in the way; without it ``ink_elsewhere`` is approximate.
        page_text: The page's printed text, handed to the AI as context.

    Returns:
        A decisions document (``gtd.decisions/2`` envelope) with ``note``,
        ``ai``, ``ai_reading`` (when ticked), ``ink_elsewhere``, ``ink_fill``
        and, on the notes page, one entry per RF-C line under ``tasks``.
    """
    cfg = cfg or ScanConfig()
    prep = prepare_page(image_path, manifest, cfg, page_key, strokes_path)
    page, rois, canvas, warnings = prep.page, prep.page["rois"], prep.canvas, prep.warnings
    note = manifest.get("note") or (tasks or {}).get("note") or {}
    ocr = get_engine(cfg.ocr_engine)
    floor_px = ink_mod.ink_floor_px(canvas, cfg.write_in_ink_mm2, manifest.get("page_w_mm", 157.8))
    reading_gray = prep.strokes_gray if prep.strokes_gray is not None else prep.gray

    _fill, ai = ink_mod.detect_box(
        prep.ink_binary, rois["page:ai"], canvas,
        inner_inset_frac=cfg.inner_inset_frac, threshold=cfg.ink_fill_threshold,
    )

    task_qrs = qr_mod.decode_task_qrs(prep.gray, page, canvas)
    rows: list[dict] = []
    for task_id in task_ids_from_rois(rois):
        act = rois.get(f"{task_id}:act")
        if act is None:
            continue
        fill, inked = detect_write_in(prep.ink_binary, act, canvas, cfg, floor_px)
        entry = {
            "id": task_id, "action": "none", "ai": False, "new_project": False, "ticks": {},
            "inked": inked, "ink_fill": round(fill, 4),
            "qr_verified": task_qrs.get(task_id) == task_id,
        }
        if inked:
            entry["act_text"] = crop_text(ocr, reading_gray, act, canvas, "capture", inset_px=0)
        elif fill > 0:
            warnings.append(
                f"{task_id}: ink too faint to read as a note (fill={fill:.4f}); the line was left alone"
            )
        rows.append(entry)

    ink_fill, ink_elsewhere = _ink_elsewhere(prep, cfg, floor_px, strokes_path is not None)

    ai_reading = None
    if ai:
        interpret = getattr(ocr, "interpret_reference", None)
        image, side_by_side = _interpretation_image(prep)
        context = {
            "stem": note.get("stem"), "title": note.get("title"),
            "page_no": page["page_no"], "page_count": len(manifest["pages"]),
            "page_text": page_text, "side_by_side": side_by_side,
            "notes_page": page["bucket"] == "refnotes",
        }
        if interpret is not None:
            try:
                ai_reading = interpret(image, context, manifest.get("date"))
            except Exception as exc:
                ai_reading = {**_FAILED_REF_AI, "note": f"interpretation failed: {exc}"}
                warnings.append(f"page {page['page_no']}: AI interpretation failed — {exc}")
        if ai_reading is None:
            warnings.append(
                f"page {page['page_no']}: ✦ AI is ticked but the {ocr.name!r} engine gave no reading"
            )

    decisions = build_decisions(
        bucket=page["bucket"], the_date=manifest.get("date", ""), header_qr=prep.header_qr,
        tasks=rows, captures=[], rectify_meta=prep.rectify_meta,
        source_image=str(image_path), manifest_path=manifest.get("_path", ""), warnings=warnings,
    )
    decisions.update(
        note={k: note.get(k) for k in ("stem", "path", "source_sha", "title")},
        ai=ai, ink_elsewhere=ink_elsewhere, ink_fill=round(ink_fill, 5),
    )
    if strokes_path is None:
        decisions["ink_approximate"] = True
    if ai_reading is not None:
        decisions["ai_reading"] = ai_reading
    return decisions
