"""Tests for the layout manifest produced by the generator (needs Chromium)."""
from __future__ import annotations

import pytest

from remarkable_gtd.common.schema import MANIFEST_SCHEMA, make_page_key
from tests.conftest import needs_chromium

pytestmark = needs_chromium

# A blank capture row carries the inbox routing boxes but no ✦ AI —
# there is no printed row to re-read.
INBOX_ROUTING = {"to_next", "to_deleg", "drop", "defer_1w", "defer_1m", "defer_1q"}
EXPECTED_GUTTERS = {
    "inbox": INBOX_ROUTING | {"ai"},
    "next": {"done", "to_deleg", "ai", "defer_1w", "defer_1m", "defer_1q"},
    "delegated": {"done", "to_me", "ai", "defer_1w", "defer_1m", "defer_1q"},
    "tickler": {"activate", "done", "ai", "redefer_1w", "redefer_1m", "redefer_1q"},
    "project": {"done", "ai"},
    "newproj": INBOX_ROUTING | {"ai"},
}
PAGE_LEVEL = {"reg:tl", "reg:tr", "reg:bl", "reg:br", "page:qr"}

# tasks.min.json carries two projects, so the sheet is 4 + 1 + 2 pages.
EXPECTED_PAGES = ("inbox", "next", "delegated", "tickler",
                  "projects", "project-01", "project-02", "new-projects")


def test_manifest_shape(manifest):
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["date"] == "2026-05-30"
    assert manifest["page_w_mm"] == 157.8
    assert set(manifest["pages"]) == {
        make_page_key(b, "2026-05-30") for b in EXPECTED_PAGES
    }
    # ...and in that order, so the scanner can match PDF pages by position.
    assert list(manifest["pages"]) == [
        make_page_key(b, "2026-05-30") for b in EXPECTED_PAGES
    ]


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
        if not k.startswith(("reg:", "page:", "capture:", "link:"))
        and not k.endswith((":qr", ":act", ":row"))
        and ":slot_" not in k
    ]
    rects = {k: Rect.from_dict(rois[k]) for k in box_keys}
    keys = sorted(rects)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert not rects[a].overlaps(rects[b]), f"{a} overlaps {b}"


def test_capture_rows_on_inbox(manifest):
    """Capture lines are full inbox rows now: QR, act write-in area, gutter."""
    page = manifest["pages"][make_page_key("inbox", "2026-05-30")]
    rois = page["rois"]
    assert not [k for k in rois if k.startswith("capture:")]
    for i in range(1, 7):
        tid = f"CP-{i:02d}"
        for suffix in ("qr", "act", "row", "slot_project", "new_project",
                       *INBOX_ROUTING):
            assert f"{tid}:{suffix}" in rois, f"missing {tid}:{suffix}"
        assert f"{tid}:ai" not in rois
        # the blank action area is a decent slab to write on
        assert rois[f"{tid}:act"]["w"] > 0.3


def test_new_project_box_on_actionable_pages(manifest):
    for bucket, ids in (("inbox", ["IN-01"]), ("next", ["NA-01", "NA-02"]),
                        ("delegated", ["DG-01"])):
        rois = manifest["pages"][make_page_key(bucket, "2026-05-30")]["rois"]
        for tid in ids:
            assert f"{tid}:new_project" in rois
    # tickler rows have no metadata line, so no NEW box either
    tk = manifest["pages"][make_page_key("tickler", "2026-05-30")]["rois"]
    assert not [k for k in tk if k.endswith(":new_project")]


def test_project_pages_and_summary(manifest):
    summary = manifest["pages"][make_page_key("projects", "2026-05-30")]
    assert summary["scan"] is False
    assert summary["bucket"] == "projects"
    assert {"link:P01", "link:P02", "link:new-projects"} <= set(summary["rois"])

    proj = manifest["pages"][make_page_key("project-01", "2026-05-30")]
    assert proj["scan"] is True
    assert proj["bucket"] == "project"
    assert proj["project"] == {"index": 1, "name": "EPSRC proposal"}
    assert "link:projects" in proj["rois"]
    # open items only (item 1 of the fixture project is done)
    ids = {k.split(":", 1)[0] for k in proj["rois"] if ":" in k
           and not k.startswith(("reg:", "page:", "link:"))}
    assert ids == {"P01-02", "P01-03", "P01-C1", "P01-C2", "P01-C3", "P01-C4"}
    for verb in EXPECTED_GUTTERS["project"]:
        assert f"P01-02:{verb}" in proj["rois"]
    # add-lines are bare: an action area and nothing else to tick
    assert "P01-C1:act" in proj["rois"]
    assert "P01-C1:done" not in proj["rois"]


def test_new_projects_page_is_last_and_scannable(manifest):
    """Blank rows for projects that do not exist yet, on the sheet's last page.

    It is appended, never inserted: ``scan_pdf`` matches PDF pages to
    manifest keys by position, so every existing page must keep its index.
    """
    keys = list(manifest["pages"])
    key = make_page_key("new-projects", "2026-05-30")
    assert keys[-1] == key
    page = manifest["pages"][key]
    assert page["scan"] is True
    assert page["bucket"] == "newproj"
    assert page["page_no"] == len(keys)
    assert "project" not in page
    assert "link:projects" in page["rois"]

    ids = {k.split(":", 1)[0] for k in page["rois"] if ":" in k
           and not k.startswith(("reg:", "page:", "link:"))}
    assert ids == {f"NP-0{i}" for i in range(1, 7)}
    # Every row is a write-in line carrying the Inbox routing gutter, the
    # ✦ AI escape hatch and the metadata slots — but no NEW box, because
    # the whole page means "new".
    for verb in EXPECTED_GUTTERS["newproj"]:
        assert f"NP-01:{verb}" in page["rois"], f"missing NP-01:{verb}"
    for extra in ("qr", "act", "row", "slot_priority", "slot_due",
                  "slot_project", "slot_to"):
        assert f"NP-01:{extra}" in page["rois"]
    assert not [k for k in page["rois"] if k.endswith(":new_project")]


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
