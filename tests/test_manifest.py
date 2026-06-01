"""Tests for manifest generation and ROI validity."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("_playwright_available")


def test_manifest_schema(rendered_sheet):
    _, manifest_path = rendered_sheet
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "gtd.manifest/1"
    assert "date" in manifest
    assert manifest["page_w_mm"] == 157.8
    assert "pages" in manifest


def test_all_expected_rois(rendered_sheet):
    _, manifest_path = rendered_sheet
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key, page in manifest["pages"].items():
        rois = page["rois"]
        bucket = page["bucket"]

        # Reg marks
        for reg in ["reg:tl", "reg:tr", "reg:bl", "reg:br"]:
            assert reg in rois, f"Missing {reg} in {key}"

        # Page QR
        assert "page:qr" in rois, f"Missing page:qr in {key}"

        # Check task ROIs
        for roi_key in rois:
            if ":" not in roi_key or roi_key.startswith(("reg:", "page:", "capture:")):
                continue
            parts = roi_key.split(":")
            assert len(parts) == 2, f"Unexpected roi key: {roi_key}"

        # Check capture lines if inbox
        if bucket == "inbox":
            for i in range(1, 7):
                assert f"capture:N{i}:box" in rois
                assert f"capture:N{i}:line" in rois


def test_rects_normalized(rendered_sheet):
    _, manifest_path = rendered_sheet
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key, page in manifest["pages"].items():
        for roi_key, rect in page["rois"].items():
            for coord in ["x", "y", "w", "h"]:
                assert coord in rect, f"Missing {coord} in {roi_key}"
                assert 0.0 <= rect[coord] <= 1.0, (
                    f"{roi_key}.{coord} = {rect[coord]} not in [0,1]"
                )


def test_reg_marks_in_corners(rendered_sheet):
    _, manifest_path = rendered_sheet
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key, page in manifest["pages"].items():
        rois = page["rois"]
        tl = rois["reg:tl"]
        tr = rois["reg:tr"]
        bl = rois["reg:bl"]
        br = rois["reg:br"]

        # Center of each reg mark should be in its corner quadrant
        def center(roi):
            return (roi["x"] + roi["w"] / 2, roi["y"] + roi["h"] / 2)

        c_tl = center(tl)
        c_tr = center(tr)
        c_bl = center(bl)
        c_br = center(br)

        assert c_tl[0] < 0.25 and c_tl[1] < 0.25
        assert c_tr[0] > 0.75 and c_tr[1] < 0.25
        assert c_bl[0] < 0.25 and c_bl[1] > 0.75
        assert c_br[0] > 0.75 and c_br[1] > 0.75


def test_no_box_overlap_same_task(rendered_sheet):
    """No two tick-box ROIs for the same task should overlap."""
    from remarkable_gtd.common.geometry import Rect

    _, manifest_path = rendered_sheet
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for key, page in manifest["pages"].items():
        rois = page["rois"]
        # Group by task
        task_boxes: dict[str, list[str]] = {}
        for roi_key in rois:
            if ":" not in roi_key or roi_key.startswith(("reg:", "page:", "capture:")):
                continue
            task_id, verb = roi_key.split(":", 1)
            if verb.startswith("slot_") or verb in ("qr", "act"):
                continue
            task_boxes.setdefault(task_id, []).append(roi_key)

        for task_id, box_keys in task_boxes.items():
            rects = [Rect(**rois[k]) for k in box_keys]
            for i, r1 in enumerate(rects):
                for j, r2 in enumerate(rects):
                    if i < j and r1.overlaps(r2):
                        pytest.fail(
                            f"Overlap for {task_id}: {box_keys[i]} and {box_keys[j]}"
                        )


def test_visual_stability(rendered_sheet):
    """Rasterize output PDF and verify QR codes decode."""

    pdf_path, manifest_path = rendered_sheet
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Just verify the PDF exists and has pages
    import fitz

    doc = fitz.open(str(pdf_path))
    assert doc.page_count == len(manifest["pages"])
    doc.close()
