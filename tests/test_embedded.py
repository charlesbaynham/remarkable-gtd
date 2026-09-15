"""Manifest + tasks travel inside the PDF as embedded files."""
from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from remarkable_gtd.common.embedded import (
    ATTACH_MANIFEST,
    ATTACH_TASKS,
    attach_state,
    read_state,
    tasks_document,
)
from remarkable_gtd.gen.generate import build_buckets

FIXTURES = Path(__file__).parent / "fixtures"


def test_round_trip_through_pdf():
    writer = PdfWriter()
    writer.append(PdfReader(FIXTURES / "example.pdf"))
    manifest = {"schema": "gtd.manifest/1", "pages": {"GTD|next|2026-06-02": {}}}
    tasks = {"schema": "gtd.tasks/1", "tasks": {"NA-01": {"act": "x", "handle": "next-actions:0:abcd1234"}}}
    attach_state(writer, manifest, tasks)
    buf = io.BytesIO()
    writer.write(buf)

    got_manifest, got_tasks = read_state(buf.getvalue())
    assert got_manifest == manifest
    assert got_tasks == tasks
    names = set(PdfReader(io.BytesIO(buf.getvalue())).attachments)
    assert names == {ATTACH_MANIFEST, ATTACH_TASKS}


def test_read_state_absent():
    assert read_state((FIXTURES / "example.pdf").read_bytes()) == (None, None)


def test_tasks_document_ids_match_sheet():
    data = {
        "inbox": [{"act": "a", "handle": "inbox:0:00000000"}],
        "next": [{"act": "b", "pri": 3}],
        "delegated": [{"act": "c", "to": "Dave"}],
        "tickler": {"week": [{"act": "w"}], "month": [], "quarter": [{"act": "q"}]},
    }
    doc = tasks_document(build_buckets(data), "2026-06-02")
    assert doc["schema"] == "gtd.tasks/1"
    t = doc["tasks"]
    assert set(t) == {
        "IN-01", "NA-01", "DG-01", "TK-01", "TK-02",
        *(f"CP-0{i}" for i in range(1, 7)),
    }
    assert t["IN-01"]["handle"] == "inbox:0:00000000" and t["IN-01"]["bucket"] == "inbox"
    assert t["TK-01"] == {"act": "w", "id": "TK-01", "bucket": "tickler", "period": "week"}
    assert t["TK-02"]["period"] == "quarter"
    assert t["DG-01"]["to"] == "Dave"


def test_capture_rows_are_their_own_bucket():
    doc = tasks_document(build_buckets({"inbox": [{"act": "a"}]}), "2026-06-02")
    cp = doc["tasks"]["CP-03"]
    assert cp["bucket"] == "capture"
    assert cp["act"] == "" and cp["capture"] is True
    assert "proj" not in cp


def test_project_pages_contribute_item_and_capture_entries():
    data = {
        "projects": [{
            "name": "Wedding 2026",
            "goal": "Get married without losing my mind",
            "status": ["booked the venue"],
            "stalled": False,
            "items": [
                {"text": "Book venue", "done": True, "handle": "proj:w:0:aa"},
                {"text": "Send invitations", "done": False, "handle": "proj:w:1:bb",
                 "surfaced": "next"},
                {"text": "Order cake", "done": False, "handle": "proj:w:2:cc"},
            ],
        }]
    }
    doc = tasks_document(build_buckets(data), "2026-06-02")
    t = doc["tasks"]

    # Ids are positions in the project's own list: the done first item keeps
    # position 1, so the two open items are -02 and -03.
    assert {"P01-02", "P01-03"} <= set(t)
    assert "P01-01" not in t
    assert t["P01-02"] == {
        "id": "P01-02", "act": "Send invitations", "proj": "Wedding 2026",
        "handle": "proj:w:1:bb", "surfaced": "next", "badge": "NA",
        "bucket": "project",
    }
    assert t["P01-03"]["bucket"] == "project" and "surfaced" not in t["P01-03"]

    for i in range(1, 5):
        add = t[f"P01-C{i}"]
        assert add["bucket"] == "capture"
        assert add["proj"] == "Wedding 2026"
        assert add["bare"] is True


def test_summary_page_contributes_nothing():
    buckets = build_buckets({"projects": [{"name": "P", "items": []}]})
    summary = [b for b in buckets if b["kind"] == "summary"][0]
    assert summary["key"] == "projects"
    ids = set(tasks_document(buckets, "2026-06-02")["tasks"])
    assert not any(i.startswith("link") for i in ids)
