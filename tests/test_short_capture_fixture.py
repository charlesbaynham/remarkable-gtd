"""Real device data: one short word written on a capture line.

``tests/fixtures/short_capture/`` is page 1 (Inbox) of the sheet printed on
2026-09-23, with "Test" written by hand on the CP-01 capture line and
nothing else on the page. The word fills 2.3 % of the 53x8 mm write-in
region — under ``slot_fill_threshold`` — so the run that produced it
silently captured nothing (charlesbaynham/gtd#3).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from remarkable_gtd.scan.manifest_io import load_manifest
from remarkable_gtd.scan.pipeline import ScanConfig, run_scan

FIX = Path(__file__).parent / "fixtures" / "short_capture"
PAGE_KEY = "GTD|inbox|2026-09-23"


class RecordingEngine:
    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def read(self, image, hint=None, context=None):
        self.calls.append((hint, image.shape[:2]))
        return f"<{hint}>"


@pytest.fixture(scope="module")
def scanned():
    engine = RecordingEngine()
    decisions = run_scan(
        FIX / "inbox_page.png",
        load_manifest(FIX / "sheet.manifest.json"),
        ScanConfig(ocr_engine=engine),
        page_key=PAGE_KEY,
        tasks=json.loads((FIX / "tasks.capture.json").read_text(encoding="utf-8")),
    )
    return decisions, engine


def test_a_short_word_on_a_capture_line_is_read(scanned):
    decisions, engine = scanned
    tasks = {t["id"]: t for t in decisions["tasks"]}

    assert tasks["CP-01"]["inked"] is True
    assert tasks["CP-01"]["act_text"] == "<capture>"
    assert [hint for hint, _ in engine.calls] == ["capture"]
    assert decisions["warnings"] == []


def test_the_untouched_capture_lines_stay_blank(scanned):
    decisions, _ = scanned
    blanks = [t for t in decisions["tasks"] if t["id"] != "CP-01"]
    assert len(blanks) == 5
    assert all(t["inked"] is False for t in blanks)
    assert all(t["action"] == "none" for t in blanks)
    assert all("act_text" not in t for t in blanks)
