"""Scan pipeline orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .decisions import (
    BUCKET_ACTIONS,
    DEFER_KEYS,
    REDEFER_KEYS,
    build_decisions,
    resolve_task,
)
from .ink import detect_box, roi_to_pixels, select_one
from .manifest_io import get_page, list_page_keys
from .ocr import get_engine
from .qr import decode_header, decode_task_qrs
from .rectify import find_reg_marks, rectify


@dataclass
class ScanConfig:
    ink_fill_threshold: float = 0.06
    inner_inset_frac: float = 0.22
    ocr_engine: str = "null"
    canvas_width: int = 1404


def run_scan(
    image_path: Path,
    manifest: dict,
    cfg: ScanConfig,
    page_key: str | None = None,
) -> dict:
    """Full pipeline: load → rectify → QR → ink → OCR → decisions."""
    # 1. Load image
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not load image: {image_path}")

    # EXIF transpose (simple)
    try:
        import PIL.Image as Image
        import PIL.ImageOps as ImageOps

        pil_img = Image.open(image_path)
        pil_img = ImageOps.exif_transpose(pil_img)
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        img = np.array(pil_img)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    except Exception:
        pass

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 2. Select page
    keys = list_page_keys(manifest)
    if page_key is None:
        if len(keys) == 1:
            page_key = keys[0]
        else:
            raise ValueError("Multiple pages in manifest; specify --page")
    manifest_page = get_page(manifest, page_key)
    bucket = manifest_page["bucket"]
    the_date = manifest["date"]

    # 3. Reg marks + rectify
    marks = find_reg_marks(binary)
    warped_gray, warped_binary, canvas_size, residual = rectify(
        gray, binary, marks, manifest_page
    )
    rectify_meta = {
        "residual_px": round(residual, 2),
        "reg_marks_found": {
            k: [round(v[0], 2), round(v[1], 2)] for k, v in marks.items()
        },
    }

    # 4. QR decode
    header_qr = decode_header(warped_gray, manifest_page, canvas_size)
    task_qrs = decode_task_qrs(warped_gray, manifest_page, canvas_size)

    # 5. Ink detection per task
    rois = manifest_page.get("rois", {})
    actions = BUCKET_ACTIONS.get(bucket, [])
    defer_keys = REDEFER_KEYS if bucket == "tickler" else DEFER_KEYS

    # Group ROIs by task
    task_rois: dict[str, dict] = {}
    for key, roi in rois.items():
        if ":" not in key or key.startswith(("reg:", "page:", "capture:")):
            continue
        task_id, verb = key.split(":", 1)
        task_rois.setdefault(task_id, {})[verb] = roi

    ocr = get_engine(cfg.ocr_engine)
    task_results = []

    for task_id, verbs in task_rois.items():
        ticks = {}
        for verb, roi in verbs.items():
            if verb.startswith("slot_") or verb == "act" or verb == "qr":
                continue
            fill, inked = detect_box(
                warped_binary,
                roi,
                canvas_size,
                cfg.inner_inset_frac,
                cfg.ink_fill_threshold,
            )
            ticks[verb] = (fill, inked)

        # Handle defer trio with select_one
        defer_results = {k: ticks.get(k, (0.0, False)) for k in defer_keys}
        selected_defer = select_one(defer_results)

        # Determine edited
        edited = False
        field_texts = {}
        if "edit" in ticks and ticks["edit"][1]:
            edited = True
            # OCR act region if edit ticked
            act_roi = verbs.get("act")
            if act_roi:
                x1, y1, x2, y2 = roi_to_pixels(act_roi, canvas_size)
                act_img = warped_gray[y1:y2, x1:x2]
                if act_img.size > 0:
                    rgb = cv2.cvtColor(act_img, cv2.COLOR_GRAY2RGB)
                    field_texts["act"] = {
                        "text": ocr.read(rgb, hint="paragraph"),
                        "ocr_conf": None,
                    }

        # OCR slots if ink present
        for slot_key in ["slot_priority", "slot_due", "slot_project", "slot_to"]:
            if slot_key in verbs:
                fill, inked = detect_box(
                    warped_binary,
                    verbs[slot_key],
                    canvas_size,
                    cfg.inner_inset_frac,
                    cfg.ink_fill_threshold,
                )
                if inked:
                    x1, y1, x2, y2 = roi_to_pixels(verbs[slot_key], canvas_size)
                    slot_img = warped_gray[y1:y2, x1:x2]
                    if slot_img.size > 0:
                        rgb = cv2.cvtColor(slot_img, cv2.COLOR_GRAY2RGB)
                        text = ocr.read(rgb, hint="slot")
                        field_texts[slot_key.replace("slot_", "")] = {
                            "text": text,
                            "ocr_conf": None,
                        }

        task_entry = resolve_task(task_id, ticks, bucket, field_texts, edited)
        task_results.append(task_entry)

    # 6. Capture lines
    captures = []
    for key, roi in rois.items():
        if key.startswith("capture:") and key.endswith(":line"):
            fill, inked = detect_box(
                warped_binary,
                roi,
                canvas_size,
                cfg.inner_inset_frac,
                cfg.ink_fill_threshold,
            )
            cap_text = ""
            if inked:
                x1, y1, x2, y2 = roi_to_pixels(roi, canvas_size)
                cap_img = warped_gray[y1:y2, x1:x2]
                if cap_img.size > 0:
                    rgb = cv2.cvtColor(cap_img, cv2.COLOR_GRAY2RGB)
                    cap_text = ocr.read(rgb, hint="single_line")
            captures.append(
                {
                    "line": key,
                    "text": cap_text,
                    "inked": inked,
                    "ocr_conf": None,
                }
            )

    decisions = build_decisions(
        bucket,
        task_results,
        captures,
        rectify_meta,
        header_qr,
        str(image_path),
        "",
        the_date,
    )
    return decisions
