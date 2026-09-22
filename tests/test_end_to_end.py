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
        assert t["ai"] is False
        assert "fields" not in t
        assert t["qr_verified"] is True
    assert decisions["header_qr"] == NEXT_KEY
    assert decisions["rectify"]["reg_marks_found"] == 4


def test_ticked_decisions_recovered(next_page_img, manifest, tmp_path):
    page = manifest["pages"][NEXT_KEY]
    img = paint_ink(next_page_img, page, "NA-01:done", "tick")
    img = paint_ink(img, page, "NA-02:defer_1m", "tick")
    img = paint_ink(img, page, "NA-02:ai", "tick")
    img = paint_ink(img, page, "NA-02:slot_due", "text:6 Jun")
    img_path = save_png(img, tmp_path / "ticked.png")

    engine = FakeEditEngine()
    decisions = run_scan(img_path, manifest, ScanConfig(ocr_engine=engine), page_key=NEXT_KEY)
    tasks = by_id(decisions)

    assert tasks["NA-01"]["action"] == "done"
    assert tasks["NA-01"]["ai"] is False

    # NA-02 has ✦ AI ticked, so its Defer tick is demoted to a suggestion:
    # the entry itself asks for nothing deterministic.
    assert tasks["NA-02"]["ai"] is True
    assert tasks["NA-02"]["action"] == "none"
    assert "defer_period" not in tasks["NA-02"]
    assert tasks["NA-02"]["suggestion"]["action"] == "defer"
    assert tasks["NA-02"]["suggestion"]["defer_period"] == "1m"
    # OCR trigger logic: the inked slot must appear even though the engine
    # only implements interpret() for the row crop.
    assert "due" in tasks["NA-02"]["fields"]
    # NA-01's slots were untouched.
    assert "fields" not in tasks["NA-01"]

    assert tasks["NA-02"]["ai_reading"]["operations"][0]["op"] == "update"
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


def test_inbox_row_can_be_handed_to_the_ai(rendered_sheet, manifest, tasks_doc, tmp_path):
    """An Inbox row carries ✦ AI, and it reaches the agent."""
    pdf_path, _ = rendered_sheet
    img = rasterize_page(pdf_path, 0)  # inbox page
    page = manifest["pages"][INBOX_KEY]

    img = paint_ink(img, page, "IN-01:ai", "tick")
    img_path = save_png(img, tmp_path / "inbox-ai.png")

    engine = FakeEditEngine()
    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=engine),
        page_key=INBOX_KEY, tasks=tasks_doc,
    )

    tasks = by_id(decisions)
    assert tasks["IN-01"]["ai"] is True
    assert tasks["IN-01"]["action"] == "none"
    assert tasks["IN-01"]["ai_reading"]["operations"][0]["op"] == "update"
    assert tasks["IN-01"]["act_text"] == "Amended"
    assert len(engine.interpret_crops) == 1
    # Inbox capture rows have no ✦ AI box at all.
    assert "CP-01:ai" not in page["rois"]


def test_ai_tick_suppresses_every_deterministic_instruction(
    rendered_sheet, manifest, tasks_doc, tmp_path
):
    """The acceptance criterion, as a test rather than an inspection.

    An Inbox row with ✦ AI ticked *and* a routing tick *and* the NEW box
    ticked must come back asking for nothing deterministic at all — no
    action, no new_project, no defer_period. The only thing downstream can
    act on is the agent's reading, so there is exactly one writer.
    """
    pdf_path, _ = rendered_sheet
    img = rasterize_page(pdf_path, 0)
    page = manifest["pages"][INBOX_KEY]
    for roi in ("IN-01:ai", "IN-01:to_deleg", "IN-01:new_project", "IN-01:defer_1w"):
        img = paint_ink(img, page, roi, "tick")
    img = paint_ink(img, page, "IN-01:slot_project", "text:Lab move")
    img_path = save_png(img, tmp_path / "inbox-ai-override.png")

    engine = FakeEditEngine()
    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=engine),
        page_key=INBOX_KEY, tasks=tasks_doc,
    )
    entry = by_id(decisions)["IN-01"]

    assert entry["ai"] is True
    assert entry["action"] == "none"
    assert entry["new_project"] is False
    assert "defer_period" not in entry
    # The deterministic reading survives, but only as a labelled hint.
    assert entry["suggestion"]["action"] == "to_deleg"
    assert entry["suggestion"]["new_project"] is True
    assert entry["ai_reading"]["operations"][0]["op"] == "update"


def test_new_projects_page_row_is_scanned(rendered_sheet, manifest, tasks_doc, tmp_path):
    """A blank New Projects row: the line is the first action, the slot the name."""
    pdf_path, _ = rendered_sheet
    np_key = make_page_key("new-projects", "2026-05-30")
    page_index = manifest["pages"][np_key]["page_no"] - 1
    img = rasterize_page(pdf_path, page_index)
    page = manifest["pages"][np_key]

    img = paint_ink(img, page, "NP-01:act", "text:Order a transformer")
    img = paint_ink(img, page, "NP-01:slot_project", "text:Rewire PSU")
    img_path = save_png(img, tmp_path / "newproj-page.png")

    engine = RecordingEngine()
    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=engine),
        page_key=np_key, tasks=tasks_doc,
    )
    tasks = by_id(decisions)

    assert decisions["bucket"] == "newproj"
    assert tasks["NP-01"]["inked"] is True
    assert tasks["NP-01"]["act_text"] == "<capture>"
    assert tasks["NP-01"]["action"] == "none"
    assert "project" in tasks["NP-01"]["fields"]
    # Untouched rows stay untouched.
    assert all(tasks[f"NP-0{i}"]["inked"] is False for i in range(2, 7))


def test_new_projects_row_can_be_routed_instead(rendered_sheet, manifest, tasks_doc, tmp_path):
    """"On reflection this is not a project" — the row routes like an Inbox item."""
    pdf_path, _ = rendered_sheet
    np_key = make_page_key("new-projects", "2026-05-30")
    img = rasterize_page(pdf_path, manifest["pages"][np_key]["page_no"] - 1)
    page = manifest["pages"][np_key]

    img = paint_ink(img, page, "NP-02:act", "text:Ask Louise for a quote")
    img = paint_ink(img, page, "NP-02:to_deleg", "tick")
    img_path = save_png(img, tmp_path / "newproj-routed.png")

    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=RecordingEngine()),
        page_key=np_key, tasks=tasks_doc,
    )
    entry = by_id(decisions)["NP-02"]
    assert entry["action"] == "to_deleg"
    assert entry["inked"] is True
    assert entry["ai"] is False


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
    assert tasks["P01-02"]["ai"] is False
    assert tasks["P01-03"]["action"] == "none"
    # The add-an-action line was written on: transcribed, but no gutter to tick.
    assert tasks["P01-C1"]["inked"] is True
    assert tasks["P01-C1"]["act_text"] == "<capture>"
    assert tasks["P01-C1"]["action"] == "none"
    assert all(tasks[f"P01-C{i}"]["inked"] is False for i in (2, 3, 4))


def test_project_step_delegated_and_project_row_edited(rendered_sheet, manifest, tasks_doc, tmp_path):
    """A project's step routes like any action; the project row carries
    the project itself — finish it, rename it, re-state its goal."""
    pdf_path, _ = rendered_sheet
    page_no = manifest["pages"][PROJ_KEY]["page_no"]
    img = rasterize_page(pdf_path, page_no - 1)
    page = manifest["pages"][PROJ_KEY]

    img = paint_ink(img, page, "P01-02:to_deleg", "tick")
    img = paint_ink(img, page, "P01-02:slot_to", "text:Oliver")
    img = paint_ink(img, page, "P01-02:slot_due", "text:1 Oct")
    img = paint_ink(img, page, "P01-03:defer_1q", "tick")
    img = paint_ink(img, page, "P01-PJ:done", "tick")
    img = paint_ink(img, page, "P01-PJ:slot_name", "text:EPSRC grant")
    img = paint_ink(img, page, "P01-PJ:slot_goal", "text:Grant submitted and funded")
    img_path = save_png(img, tmp_path / "project.png")

    engine = RecordingEngine()
    decisions = run_scan(
        img_path, manifest, ScanConfig(ocr_engine=engine),
        page_key=PROJ_KEY, tasks=tasks_doc,
    )
    tasks = by_id(decisions)

    assert tasks["P01-02"]["action"] == "to_deleg"
    assert tasks["P01-02"]["fields"]["to"]["text"] == "<to>"
    assert tasks["P01-02"]["fields"]["due"]["text"] == "<due>"
    assert tasks["P01-03"]["action"] == "defer"
    assert tasks["P01-03"]["defer_period"] == "1q"
    assert tasks["P01-PJ"]["action"] == "done"
    assert tasks["P01-PJ"]["fields"]["name"]["text"] == "<name>"
    assert tasks["P01-PJ"]["fields"]["goal"]["text"] == "<goal>"
    assert tasks_doc["tasks"]["P01-PJ"]["bucket"] == "projhead"
    assert tasks_doc["tasks"]["P01-PJ"]["proj"] == "EPSRC proposal"


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
