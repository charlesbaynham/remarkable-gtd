"""Scanning a reference-note sheet: the ✦ AI box, the notes page, ink elsewhere.

A reference page has no gutter to read. What the user can do is write on the
notes page (RF-C* lines, read like capture rows), write anywhere else on the
page, or tick ✦ AI to have the page interpreted. Ink anywhere else is only
*reported* (``ink_elsewhere``): nothing is read from the body without the AI
box, and the AI is the page's only writer, as on the daily sheet.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import numpy as np

from remarkable_gtd.scan import ink as ink_mod
from remarkable_gtd.scan import qr as qr_mod
from remarkable_gtd.scan.decisions import build_decisions
from remarkable_gtd.scan.ocr import get_engine
from remarkable_gtd.scan.pipeline import (
    _FAILED_AI, ScanConfig, crop_text, detect_write_in, prepare_page, task_ids_from_rois,
)

SOURCE_CAP_CHARS = 6000
SOURCE_CONTEXT_LINES = 2


def _plain(markdown_line: str) -> str:
    """A source line as it prints: markup dropped, wiki-links as their alias."""
    line = re.sub(r"!?\[\[([^\]|]*)(?:\|([^\]]*))?\]\]", lambda m: m[2] or m[1], markdown_line)
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>|~\[\]]|^\s*[-+] (\[.\])?", " ", line)).strip()


def source_for_page(source: str, page_text: str, cap: int = SOURCE_CAP_CHARS) -> str:
    """The note's markdown that this page prints: what the AI must quote ``old`` from.

    A short note goes in whole. Otherwise the span from the first to the last
    source line whose text appears on the page, plus a little context, capped.
    """
    if len(source) <= cap:
        return source
    printed = re.sub(r"\s+", " ", page_text)
    lines = source.splitlines()
    hits = [i for i, line in enumerate(lines)
            if len(plain := _plain(line)) >= 12 and plain[:40] in printed]
    if not hits:
        return ""
    first, last = max(0, hits[0] - SOURCE_CONTEXT_LINES), hits[-1] + SOURCE_CONTEXT_LINES + 1
    return "\n".join(lines[first:last])[:cap]


def _ink_elsewhere(prep, floor_px: int) -> tuple[float, bool]:
    """Handwriting anywhere on the page except the header band and the write-in lines.

    Only meaningful on a strokes-only layer, where there is no printed text to
    avoid, so margins count: that is where margin notes go.
    """
    binary = prep.ink_binary.copy()
    rois = prep.page["rois"]
    header_bottom = ink_mod.roi_to_pixels(rois["page:body"], prep.canvas)[1]
    binary[:header_bottom, :] = 255
    for key, roi in rois.items():
        if key.endswith(":act"):
            x1, y1, x2, y2 = ink_mod.roi_to_pixels(roi, prep.canvas)
            binary[y1:y2, x1:x2] = 255
    whole_page = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    return ink_mod.detect_box(
        binary, whole_page, prep.canvas, inner_inset_frac=0.0, threshold=1.0, min_ink_px=floor_px
    )


def _body_fill(prep) -> float:
    """Dark fraction of the body area, printed text included: informational only."""
    return ink_mod.detect_box(
        prep.ink_binary, prep.page["rois"]["page:body"], prep.canvas,
        inner_inset_frac=0.0, threshold=1.0,
    )[0]


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
    today: str | None = None,
) -> dict:
    """Scan one page of a reference sheet.

    Args:
        image_path: Raster of the page as annotated (PNG/JPG/PDF).
        strokes_path: Raster of the handwriting alone, white page and black
            ink, same geometry. With it ink is measured without the printed
            page in the way; without it ``ink_elsewhere`` is approximate.
        page_text: The page's printed text, handed to the AI as context.
        today: ISO date handed to the AI (default: today, not the render
            date: a sheet stays on the device for months).

    Returns:
        A decisions document (``gtd.decisions/2`` envelope) with ``note``,
        ``ai``, ``ai_reading`` (when ticked), ``ink_elsewhere`` (``None``
        without a strokes layer), ``ink_fill`` and, on the notes page, one
        entry per RF-C line under ``tasks``. On an AI page the lines carry a
        ``suggestion`` and no ``act_text``: the AI is the page's only writer.
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
            text = crop_text(ocr, reading_gray, act, canvas, "capture", inset_px=0)
            if ai:
                entry["suggestion"] = {"text": text}
            else:
                entry["act_text"] = text
        elif fill > 0:
            warnings.append(
                f"{task_id}: ink too faint to read as a note (fill={fill:.4f}); the line was left alone"
            )
        rows.append(entry)

    if strokes_path is not None:
        ink_fill, ink_elsewhere = _ink_elsewhere(prep, floor_px)
    else:
        ink_fill, ink_elsewhere = _body_fill(prep), None

    ai_reading = None
    if ai:
        interpret = getattr(ocr, "interpret_reference", None)
        image, side_by_side = _interpretation_image(prep)
        context = {
            "stem": note.get("stem"), "title": note.get("title"),
            "page_no": page["page_no"], "page_count": len(manifest["pages"]),
            "page_text": page_text, "side_by_side": side_by_side,
            "source": source_for_page((tasks or {}).get("note", {}).get("source", ""), page_text),
            "suggestions": {r["id"]: r["suggestion"]["text"] for r in rows if "suggestion" in r},
            "notes_page": page["bucket"] == "refnotes",
        }
        if interpret is not None:
            try:
                ai_reading = interpret(image, context, today or date.today().isoformat())
            except Exception as exc:
                ai_reading = {**_FAILED_AI, "note": f"interpretation failed: {exc}"}
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
