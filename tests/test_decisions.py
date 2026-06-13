"""Unit tests for the decision resolver (pure logic, no images)."""
from __future__ import annotations

from remarkable_gtd.common.schema import DECISIONS_SCHEMA
from remarkable_gtd.scan.decisions import build_decisions, resolve_task


def ticks(**kw) -> dict:
    """Build a ticks dict: inked verbs get fill 0.3, others 0.0."""
    base = {}
    for verb, inked in kw.items():
        base[verb] = (0.3 if inked else 0.005, bool(inked))
    return base


def test_single_done():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=True, to_deleg=False, defer_1w=False, defer_1m=False,
              defer_1q=False, edit=False),
        "next",
    )
    assert entry["action"] == "done"
    assert entry["edited"] is False
    assert "defer_period" not in entry
    assert warnings == []


def test_no_marks_is_none():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, defer_1w=False, defer_1m=False,
              defer_1q=False, edit=False),
        "next",
    )
    assert entry["action"] == "none"
    assert warnings == []


def test_defer_selects_period():
    entry, _ = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, defer_1w=False, defer_1m=True,
              defer_1q=False, edit=False),
        "next",
    )
    assert entry["action"] == "defer"
    assert entry["defer_period"] == "1m"


def test_tickler_redefer():
    entry, _ = resolve_task(
        "TK-01",
        ticks(activate=False, done=False, redefer_1w=False, redefer_1m=False,
              redefer_1q=True, edit=False),
        "tickler",
    )
    assert entry["action"] == "defer"
    assert entry["defer_period"] == "1q"


def test_done_beats_defer_with_warning():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=True, to_deleg=False, defer_1w=True, defer_1m=False,
              defer_1q=False, edit=False),
        "next",
    )
    assert entry["action"] == "done"
    assert len(warnings) == 1
    assert "precedence" in warnings[0]


def test_edit_is_orthogonal():
    entry, warnings = resolve_task(
        "DG-01",
        ticks(done=True, to_me=False, defer_1w=False, defer_1m=False,
              defer_1q=False, edit=True),
        "delegated",
    )
    assert entry["action"] == "done"
    assert entry["edited"] is True
    # edit must not produce a multi-action conflict warning
    assert warnings == []


def test_inbox_routing():
    entry, _ = resolve_task(
        "IN-01",
        ticks(to_next=True, to_deleg=False, defer_1w=False, defer_1m=False,
              defer_1q=False, drop=False),
        "inbox",
    )
    assert entry["action"] == "to_next"


def test_multiple_defer_boxes_warns():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, defer_1w=True, defer_1m=True,
              defer_1q=False, edit=False),
        "next",
    )
    assert entry["action"] == "defer"
    assert any("multiple defer" in w for w in warnings)


def test_fields_and_act_text_passthrough():
    entry, _ = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, edit=True),
        "next",
        field_texts={"due": {"text": "6 Jun", "fill": 0.1}},
        act_text="Amended action",
    )
    assert entry["fields"]["due"]["text"] == "6 Jun"
    assert entry["act_text"] == "Amended action"


def test_build_decisions_shape():
    doc = build_decisions(
        bucket="next", the_date="2026-05-30", header_qr="GTD|next|2026-05-30",
        tasks=[], captures=[], rectify_meta={"residual_px": 1.0, "reg_marks_found": 4},
        source_image="scan.png", manifest_path="m.json", warnings=[],
    )
    assert doc["schema"] == DECISIONS_SCHEMA
    for key in ("source_image", "manifest", "bucket", "date", "header_qr",
                "rectify", "tasks", "captures", "warnings"):
        assert key in doc
