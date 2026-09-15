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
from remarkable_gtd.scan.ink import roi_to_pixels
from remarkable_gtd.scan.pipeline import ScanConfig, run_scan
from tests.conftest import needs_chromium, paint_ink, rasterize_page, warp_image

pytestmark = needs_chromium

NEXT_KEY = make_page_key("next", "2026-05-30")
INBOX_KEY = make_page_key("inbox", "2026-05-30")
PROJ_KEY = make_page_key("project-01", "2026-05-30")


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


class RecordingEngine:
    """Transcribes nothing: returns the hint it was given, and records it."""

    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def read(self, image, hint=None, context=None) -> str:
        self.calls.append((hint, image.shape[:2]))
        return f"<{hint}>"


class FakeEditEngine:
    """Records the crop it's asked to interpret; returns a fixed reading."""

    name = "fake-edit"

    def __init__(self):
        self.interpret_crops: list[tuple[int, int]] = []

    def read(self, image, hint=None, context=None) -> str:
        return ""

    def interpret(self, image, task, vocabulary=None, today=None) -> dict:
        self.interpret_crops.append(image.shape[:2])
        return {
            "handwriting": "Amended",
            "understood": True,
            "confidence": 0.95,
            "note": "struck through and rewritten",
            "operations": [{
                "op": "update", "text": "Amended", "priority": None, "due": None,
                "project": None, "person": None, "to": None, "period": None,
                "name": None, "goal": None,
            }],
        }


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

    engine = FakeEditEngine()
    decisions = run_scan(img_path, manifest, ScanConfig(ocr_engine=engine), page_key=NEXT_KEY)
    tasks = by_id(decisions)

    assert tasks["NA-01"]["action"] == "done"
    assert tasks["NA-01"]["edited"] is False

    assert tasks["NA-02"]["action"] == "defer"
    assert tasks["NA-02"]["defer_period"] == "1m"
    assert tasks["NA-02"]["edited"] is True
    # OCR trigger logic: the inked slot must appear even though the engine
    # only implements interpret() for the row crop.
    assert "due" in tasks["NA-02"]["fields"]
    # NA-01's slots were untouched.
    assert "fields" not in tasks["NA-01"]

    assert tasks["NA-02"]["edit"]["operations"][0]["op"] == "update"
    assert tasks["NA-02"]["act_text"] == "Amended"
    assert len(engine.interpret_crops) == 1
    crop_h, crop_w = engine.interpret_crops[0]
    act_x1, act_y1, act_x2, act_y2 = roi_to_pixels(
        page["rois"]["NA-02:act"],
        (1404, round(1404 * page["render"]["h_px"] / page["render"]["w_px"])),
    )
    assert crop_h > act_y2 - act_y1
    assert crop_w > act_x2 - act_x1


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


def test_inbox_capture_row(rendered_sheet, manifest, tasks_doc, tmp_path):
    """A capture row is a full inbox row: write on it and tick where it goes."""
    pdf_path, _ = rendered_sheet
    img = rasterize_page(pdf_path, 0)  # inbox page
    page = manifest["pages"][INBOX_KEY]

    img = paint_ink(img, page, "CP-01:act", "text:Buy a new kettle")
    img = paint_ink(img, page, "CP-01:to_next", "tick")
    img = paint_ink(img, page, "IN-01:to_next", "tick")
    img_path = save_png(img, tmp_path / "inbox.png")

    engine = RecordingEngine()
    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=engine),
        page_key=INBOX_KEY, tasks=tasks_doc,
    )

    tasks = by_id(decisions)
    assert tasks["IN-01"]["action"] == "to_next"

    written = tasks["CP-01"]
    assert written["inked"] is True
    assert written["action"] == "to_next"           # capture uses the inbox verbs
    assert written["act_text"] == "<capture>"       # OCR'd with the capture hint
    assert "capture" in {hint for hint, _ in engine.calls}

    # Every other capture row was left blank: no ink, no OCR, no action.
    blanks = [tasks[f"CP-{i:02d}"] for i in range(2, 7)]
    assert all(t["inked"] is False for t in blanks)
    assert all(t["action"] == "none" for t in blanks)
    assert all("act_text" not in t for t in blanks)
    # The legacy capture list is empty on a modern sheet.
    assert decisions["captures"] == []


def test_new_project_box_is_recovered(next_page_img, manifest, tasks_doc, tmp_path):
    page = manifest["pages"][NEXT_KEY]
    img = paint_ink(next_page_img, page, "NA-01:new_project", "tick")
    img = paint_ink(img, page, "NA-01:slot_project", "text:Kitchen")
    img_path = save_png(img, tmp_path / "newproj.png")

    decisions = run_scan(
        img_path, manifest, ScanConfig(), page_key=NEXT_KEY, tasks=tasks_doc
    )
    tasks = by_id(decisions)
    assert tasks["NA-01"]["new_project"] is True
    assert "project" in tasks["NA-01"]["fields"]
    # It is a flag, not an action, and it does not leak to other rows.
    assert tasks["NA-01"]["action"] == "none"
    assert tasks["NA-02"]["new_project"] is False


def test_project_page_item_ticked_done(rendered_sheet, manifest, tasks_doc, tmp_path):
    pdf_path, _ = rendered_sheet
    page_no = manifest["pages"][PROJ_KEY]["page_no"]
    img = rasterize_page(pdf_path, page_no - 1)
    page = manifest["pages"][PROJ_KEY]

    img = paint_ink(img, page, "P01-02:done", "tick")
    img = paint_ink(img, page, "P01-C1:act", "text:Chase the finance office")
    img_path = save_png(img, tmp_path / "project.png")

    engine = RecordingEngine()
    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=engine),
        page_key=PROJ_KEY, tasks=tasks_doc,
    )
    tasks = by_id(decisions)

    assert decisions["bucket"] == "project"
    assert tasks["P01-02"]["action"] == "done"
    assert tasks["P01-02"]["edited"] is False
    assert tasks["P01-03"]["action"] == "none"
    # The add-an-action line was written on: transcribed, but no gutter to tick.
    assert tasks["P01-C1"]["inked"] is True
    assert tasks["P01-C1"]["act_text"] == "<capture>"
    assert tasks["P01-C1"]["action"] == "none"
    assert all(tasks[f"P01-C{i}"]["inked"] is False for i in (2, 3, 4))


def test_summary_page_is_skipped_by_the_scanner(rendered_sheet, manifest, tasks_doc, tmp_path):
    from remarkable_gtd.scan.sheet import scan_pdf, summarize

    pdf_path, _ = rendered_sheet
    decisions = scan_pdf(
        pdf_path, manifest, ScanConfig(), work_dir=tmp_path, tasks=tasks_doc
    )
    by_key = {p["page_key"]: p for p in decisions["pages"]}
    summary = by_key[make_page_key("projects", "2026-05-30")]
    assert summary["skipped"] is True
    assert "tasks" not in summary

    proj = by_key[PROJ_KEY]
    assert proj["project"] == {"index": 1, "name": "EPSRC proposal"}
    assert summarize(decisions)["skipped"] == 1
