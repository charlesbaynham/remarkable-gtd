"""Tests for the layout manifest produced by the generator (needs Chromium)."""
from __future__ import annotations

import pytest

from remarkable_gtd.common.schema import MANIFEST_SCHEMA, make_page_key
from tests.conftest import needs_chromium

pytestmark = needs_chromium

EXPECTED_GUTTERS = {
    "inbox": {"to_next", "to_deleg", "drop", "defer_1w", "defer_1m", "defer_1q"},
    "next": {"done", "to_deleg", "edit", "defer_1w", "defer_1m", "defer_1q"},
    "delegated": {"done", "to_me", "edit", "defer_1w", "defer_1m", "defer_1q"},
    "tickler": {"activate", "done", "edit", "redefer_1w", "redefer_1m", "redefer_1q"},
}
PAGE_LEVEL = {"reg:tl", "reg:tr", "reg:bl", "reg:br", "page:qr"}


def test_manifest_shape(manifest):
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["date"] == "2026-05-30"
    assert manifest["page_w_mm"] == 157.8
    assert set(manifest["pages"]) == {
        make_page_key(b, "2026-05-30")
        for b in ("inbox", "next", "delegated", "tickler")
    }


def test_every_task_has_full_roi_set(manifest, tasks_min):
    page = manifest["pages"][make_page_key("next", "2026-05-30")]
    rois = page["rois"]
    for t in tasks_min["next"]:
        tid = t["id"]
        for verb in EXPECTED_GUTTERS["next"]:
            assert f"{tid}:{verb}" in rois, f"missing {tid}:{verb}"
        for extra in ("qr", "act", "slot_priority", "slot_due", "slot_project", "slot_to"):
            assert f"{tid}:{extra}" in rois


def test_page_level_rois_present(manifest):
    for key, page in manifest["pages"].items():
        for roi_key in PAGE_LEVEL:
            assert roi_key in page["rois"], f"{key} missing {roi_key}"


def test_rois_normalized(manifest):
    for page in manifest["pages"].values():
        for key, r in page["rois"].items():
            assert -0.01 <= r["x"] <= 1.01, (key, r)
            assert -0.01 <= r["y"] <= 1.01, (key, r)
            assert 0 < r["w"] <= 1.0, (key, r)
            assert 0 < r["h"] <= 1.0, (key, r)
            assert r["x"] + r["w"] <= 1.02, (key, r)
            assert r["y"] + r["h"] <= 1.02, (key, r)


def test_reg_marks_in_corners(manifest):
    for page in manifest["pages"].values():
        rois = page["rois"]
        assert rois["reg:tl"]["x"] < 0.2 and rois["reg:tl"]["y"] < 0.2
        assert rois["reg:tr"]["x"] > 0.8 and rois["reg:tr"]["y"] < 0.2
        assert rois["reg:bl"]["x"] < 0.2 and rois["reg:bl"]["y"] > 0.8
        assert rois["reg:br"]["x"] > 0.8 and rois["reg:br"]["y"] > 0.8


def test_tick_boxes_do_not_overlap(manifest):
    from remarkable_gtd.common.geometry import Rect

    page = manifest["pages"][make_page_key("next", "2026-05-30")]
    rois = page["rois"]
    box_keys = [
        k for k in rois
        if not k.startswith(("reg:", "page:", "capture:"))
        and not k.endswith((":qr", ":act"))
        and ":slot_" not in k
    ]
    rects = {k: Rect.from_dict(rois[k]) for k in box_keys}
    keys = sorted(rects)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert not rects[a].overlaps(rects[b]), f"{a} overlaps {b}"


def test_capture_lines_on_inbox(manifest):
    page = manifest["pages"][make_page_key("inbox", "2026-05-30")]
    rois = page["rois"]
    for i in range(1, 7):
        assert f"capture:N{i}:box" in rois
        assert f"capture:N{i}:line" in rois
        # the box sits inside the line's horizontal span
        line = rois[f"capture:N{i}:line"]
        box = rois[f"capture:N{i}:box"]
        assert line["x"] <= box["x"] <= line["x"] + line["w"]


def test_header_qr_decodes_from_render(rendered_sheet, manifest):
    """Rasterize page 2 (next) and decode its header QR — proves the printed
    QR matches the manifest page key."""
    from remarkable_gtd.common.schema import make_page_key
    from remarkable_gtd.scan.qr import decode_region
    from tests.conftest import rasterize_page

    pdf_path, _ = rendered_sheet
    img = rasterize_page(pdf_path, 1)  # page index 1 = next
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    page = manifest["pages"][make_page_key("next", "2026-05-30")]
    h, w = gray.shape
    decoded = decode_region(gray, page["rois"]["page:qr"], (w, h))
    assert decoded == "GTD|next|2026-05-30"
