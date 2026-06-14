"""End-to-end: render -> rasterize -> paint synthetic ink -> scan -> assert.

The generator is the scanner's ground-truth simulator: we choose decisions
in code, paint them into manifest ROIs, and assert the pipeline recovers
exactly those decisions. NullEngine keeps the OCR-trigger logic
deterministic (fields appear iff the slot is inked).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from remarkable_gtd.common.schema import make_page_key
from remarkable_gtd.scan.pipeline import ScanConfig, run_scan
from tests.conftest import needs_chromium, paint_ink, rasterize_page, warp_image

pytestmark = needs_chromium

NEXT_KEY = make_page_key("next", "2026-05-30")
INBOX_KEY = make_page_key("inbox", "2026-05-30")


def save_png(img, path: Path) -> Path:
    from PIL import Image

    Image.fromarray(img).save(path)
    return path


@pytest.fixture()
def next_page_img(rendered_sheet):
    pdf_path, _ = rendered_sheet
    return rasterize_page(pdf_path, 1)  # page 2 of 4 = next


def by_id(decisions: dict) -> dict:
    return {t["id"]: t for t in decisions["tasks"]}


def test_clean_page_all_none(next_page_img, manifest, tmp_path):
    img_path = save_png(next_page_img, tmp_path / "clean.png")
    decisions = run_scan(img_path, manifest, ScanConfig(), page_key=NEXT_KEY)

    tasks = by_id(decisions)
    assert set(tasks) == {"NA-01", "NA-02"}
    for t in tasks.values():
        assert t["action"] == "none"
        assert t["edited"] is False
        assert "fields" not in t
        assert t["qr_verified"] is True
    assert decisions["header_qr"] == NEXT_KEY
    assert decisions["rectify"]["reg_marks_found"] == 4


def test_ticked_decisions_recovered(next_page_img, manifest, tmp_path):
    page = manifest["pages"][NEXT_KEY]
    img = paint_ink(next_page_img, page, "NA-01:done", "tick")
    img = paint_ink(img, page, "NA-02:defer_1m", "tick")
    img = paint_ink(img, page, "NA-02:edit", "tick")
    img = paint_ink(img, page, "NA-02:slot_due", "text:6 Jun")
    img_path = save_png(img, tmp_path / "ticked.png")

    decisions = run_scan(img_path, manifest, ScanConfig(), page_key=NEXT_KEY)
    tasks = by_id(decisions)

    assert tasks["NA-01"]["action"] == "done"
    assert tasks["NA-01"]["edited"] is False

    assert tasks["NA-02"]["action"] == "defer"
    assert tasks["NA-02"]["defer_period"] == "1m"
    assert tasks["NA-02"]["edited"] is True
    # OCR trigger logic: the inked slot must appear even with NullEngine.
    assert "due" in tasks["NA-02"]["fields"]
    # NA-01's slots were untouched.
    assert "fields" not in tasks["NA-01"]


def test_survives_rotation_and_keystone(next_page_img, manifest, tmp_path):
    page = manifest["pages"][NEXT_KEY]
    img = paint_ink(next_page_img, page, "NA-01:done", "tick")
    img = warp_image(img, angle_deg=2.0, keystone=0.008)
    img_path = save_png(img, tmp_path / "warped.png")

    decisions = run_scan(img_path, manifest, ScanConfig(), page_key=NEXT_KEY)
    tasks = by_id(decisions)
    assert tasks["NA-01"]["action"] == "done"
    assert tasks["NA-02"]["action"] == "none"
    assert decisions["rectify"]["residual_px"] is not None
    assert decisions["rectify"]["residual_px"] < 5.0


def test_page_autodetected_from_qr(next_page_img, manifest, tmp_path):
    img_path = save_png(next_page_img, tmp_path / "auto.png")
    decisions = run_scan(img_path, manifest, ScanConfig())  # no page_key
    assert decisions["bucket"] == "next"


def test_inbox_capture_line(rendered_sheet, manifest, tmp_path):
    pdf_path, _ = rendered_sheet
    img = rasterize_page(pdf_path, 0)  # inbox page
    page = manifest["pages"][INBOX_KEY]

    img = paint_ink(img, page, "capture:N1:box", "tick")
    img = paint_ink(img, page, "IN-01:to_next", "tick")
    img_path = save_png(img, tmp_path / "inbox.png")

    decisions = run_scan(img_path, manifest, ScanConfig(), page_key=INBOX_KEY)

    tasks = by_id(decisions)
    assert tasks["IN-01"]["action"] == "to_next"

    captures = {c["line"]: c for c in decisions["captures"]}
    assert captures["N1"]["inked"] is True
    assert all(not c["inked"] for line, c in captures.items() if line != "N1")
