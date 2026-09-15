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
    assert set(t) == {"IN-01", "NA-01", "DG-01", "TK-01", "TK-02"}
    assert t["IN-01"]["handle"] == "inbox:0:00000000" and t["IN-01"]["bucket"] == "inbox"
    assert t["TK-01"] == {"act": "w", "id": "TK-01", "bucket": "tickler", "period": "week"}
    assert t["TK-02"]["period"] == "quarter"
    assert t["DG-01"]["to"] == "Dave"
