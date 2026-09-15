"""Real device data: a sheet ticked by hand on a reMarkable 2.

``tests/fixtures/annotated_scan/gtd_sheet.rmdoc`` is tasks.example.json
rendered for 2026-06-02, annotated on the device and downloaded with
``rmapi get``; ``sheet.manifest.json`` next to it is the matching manifest.
The example tasks say in their own text what was ticked, so the expected
decisions are known. No browser needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from remarkable_gtd.scan.manifest_io import load_manifest
from remarkable_gtd.scan.pipeline import ScanConfig
from remarkable_gtd.scan.sheet import scan_rmdoc, summarize

FIX = Path(__file__).parent / "fixtures" / "annotated_scan"

EXPECTED = {
    "IN-01": ("to_next", None), "IN-02": ("to_deleg", None), "IN-03": ("drop", None),
    "IN-04": ("to_next", None), "IN-05": ("defer", "1w"), "IN-06": ("defer", "1m"),
    "IN-07": ("none", None),
    "NA-01": ("done", None), "NA-02": ("to_deleg", None), "NA-03": ("defer", "1w"),
    "NA-04": ("defer", "1m"), "NA-05": ("defer", "1q"), "NA-06": ("none", None),
    "DG-01": ("done", None), "DG-02": ("to_me", None), "DG-03": ("defer", "1w"),
    "DG-04": ("none", None),
    "TK-01": ("activate", None), "TK-02": ("done", None), "TK-03": ("defer", "1w"),
    "TK-04": ("activate", None), "TK-05": ("defer", "1m"), "TK-06": ("done", None),
    "TK-07": ("none", None),
}


class RecordingEngine:
    """Records what the pipeline asks to transcribe instead of transcribing."""

    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, tuple[int, int]]] = []
        self.interpret_calls: list[tuple[int, int]] = []

    def read(self, image, hint=None, context=None):
        self.calls.append((hint, image.shape[:2]))
        return f"<{hint}>"

    def interpret(self, image, task, vocabulary=None, today=None):
        self.interpret_calls.append(image.shape[:2])
        return None


@pytest.fixture(scope="module")
def scanned(tmp_path_factory):
    manifest = load_manifest(FIX / "sheet.manifest.json")
    engine = RecordingEngine()
    decisions, _, _, annotated = scan_rmdoc(
        FIX / "gtd_sheet.rmdoc", tmp_path_factory.mktemp("scan"),
        ScanConfig(ocr_engine=engine), manifest=manifest, tasks=None,
    )
    assert annotated.exists()
    return decisions, engine


def test_every_hand_tick_is_recovered(scanned):
    decisions, _ = scanned
    tasks = {t["id"]: t for p in decisions["pages"] for t in p["tasks"]}
    assert set(tasks) == set(EXPECTED)
    got = {tid: (t["action"], t.get("defer_period")) for tid, t in tasks.items()}
    assert got == EXPECTED
    assert all(t["qr_verified"] for t in tasks.values())
    for page in decisions["pages"]:
        assert "error" not in page
        assert page["header_qr"] == page["page_key"]
        assert page["rectify"]["reg_marks_found"] == 4
        assert page["rectify"]["residual_px"] < 2.0


def test_edit_flag_stays_off_when_not_ticked(scanned):
    decisions, engine = scanned
    edited = [t["id"] for p in decisions["pages"] for t in p["tasks"] if t["edited"]]
    assert edited == []
    # No row's Edit box is ticked on this sheet, so interpret() is never called.
    assert engine.interpret_calls == []


def test_handwriting_regions_are_the_only_ocr_calls(scanned):
    decisions, engine = scanned
    tasks = {t["id"]: t for p in decisions["pages"] for t in p["tasks"]}
    # A name was written in the TO slot of the two delegated rows.
    assert "to" in tasks["NA-02"]["fields"]
    assert "to" in tasks["IN-02"]["fields"] or "to" in tasks["IN-04"]["fields"]
    hints = {h for h, _ in engine.calls}
    assert hints <= {"to", "priority", "due", "project", "capture"}
    # Nothing beyond a handful of small crops was sent for transcription.
    assert len(engine.calls) <= 12
    for _hint, (h, w) in engine.calls:
        assert h < 200 and w < 1200
    # The capture lines on this sheet were left blank: none may trigger OCR.
    inbox = decisions["pages"][0]
    assert [c["line"] for c in inbox["captures"] if c["inked"]] == []
    assert "capture" not in hints


def test_summary_counts(scanned):
    decisions, _ = scanned
    s = summarize(decisions)
    assert s["pages"] == 4 and s["errors"] == 0 and s["tasks"] == 24
    assert s["actions"] == sum(1 for a, _ in EXPECTED.values() if a != "none")
