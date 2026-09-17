"""Unit tests for the decision resolver (pure logic, no images)."""
from __future__ import annotations

from remarkable_gtd.common.schema import DECISIONS_SCHEMA
from remarkable_gtd.scan.decisions import (
    BUCKET_ACTIONS,
    build_decisions,
    build_suggestion,
    resolve_action,
    resolve_task,
)


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
              defer_1q=False, ai=False),
        "next",
    )
    assert entry["action"] == "done"
    assert entry["ai"] is False
    assert "defer_period" not in entry
    assert warnings == []


def test_no_marks_is_none():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, defer_1w=False, defer_1m=False,
              defer_1q=False, ai=False),
        "next",
    )
    assert entry["action"] == "none"
    assert warnings == []


def test_defer_selects_period():
    entry, _ = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, defer_1w=False, defer_1m=True,
              defer_1q=False, ai=False),
        "next",
    )
    assert entry["action"] == "defer"
    assert entry["defer_period"] == "1m"


def test_tickler_redefer():
    entry, _ = resolve_task(
        "TK-01",
        ticks(activate=False, done=False, redefer_1w=False, redefer_1m=False,
              redefer_1q=True, ai=False),
        "tickler",
    )
    assert entry["action"] == "defer"
    assert entry["defer_period"] == "1q"


def test_done_beats_defer_with_warning():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=True, to_deleg=False, defer_1w=True, defer_1m=False,
              defer_1q=False, ai=False),
        "next",
    )
    assert entry["action"] == "done"
    assert len(warnings) == 1
    assert "precedence" in warnings[0]


def test_ai_short_circuits_the_deterministic_action():
    """✦ AI is an escape hatch: the gutter tick must not survive it.

    The deterministic reading is still computed — it is the agent's hint —
    but it is demoted into ``suggestion``, so nothing downstream can
    derive a vault write from it. One row, one writer.
    """
    entry, warnings = resolve_task(
        "DG-01",
        ticks(done=True, to_me=False, defer_1w=False, defer_1m=False,
              defer_1q=False, ai=True),
        "delegated",
    )
    assert entry["ai"] is True
    assert entry["action"] == "none"
    assert entry["suggestion"]["action"] == "done"
    # The escape hatch is not a second action, so it is not a conflict.
    assert warnings == []


def test_ai_short_circuits_new_project_and_defer_period():
    entry, _ = resolve_task(
        "IN-01",
        ticks(to_next=False, to_deleg=False, drop=False, defer_1w=True,
              defer_1m=False, defer_1q=False, ai=True, new_project=True),
        "inbox",
        field_texts={"project": {"text": "Lab move", "fill": 0.09}},
    )
    assert entry["action"] == "none"
    assert entry["new_project"] is False
    assert "defer_period" not in entry
    assert entry["suggestion"] == {
        "action": "defer",
        "new_project": True,
        "defer_period": "1w",
        "fields": {"project": "Lab move"},
    }


def test_ai_row_carries_no_deterministic_instruction():
    """Whatever an AI row's entry says, it cannot ask for a vault change."""
    entry, _ = resolve_task(
        "IN-02",
        ticks(to_next=True, to_deleg=True, drop=True, ai=True, new_project=True),
        "inbox",
    )
    assert entry["action"] == "none" and entry["new_project"] is False


def test_the_pre_rename_edit_roi_still_means_ai():
    """A sheet printed before the box was renamed carries ``edit`` ROIs."""
    entry, _ = resolve_task("NA-01", ticks(done=True, edit=True), "next")
    assert entry["ai"] is True and entry["action"] == "none"


def test_resolve_action_is_side_effect_free_and_agrees_with_resolve_task():
    t = ticks(done=False, to_deleg=False, defer_1w=False, defer_1m=True,
              defer_1q=False, ai=False)
    action, period, warnings = resolve_action("NA-01", t, "next")
    entry, _ = resolve_task("NA-01", t, "next")
    assert (action, period) == ("defer", "1m")
    assert entry["action"] == action and entry["defer_period"] == period
    assert warnings == []


def test_build_suggestion_carries_the_written_text():
    s = build_suggestion(
        "NP-01",
        ticks(to_next=False, to_deleg=False, drop=False, ai=True),
        "newproj",
        {"project": {"text": "Rewire the PSU", "fill": 0.07}},
        "Order a new transformer",
    )
    assert s["action"] == "none"
    assert s["text"] == "Order a new transformer"
    assert s["fields"] == {"project": "Rewire the PSU"}


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
              defer_1q=False, ai=False),
        "next",
    )
    assert entry["action"] == "defer"
    assert any("multiple defer" in w for w in warnings)


def test_fields_and_act_text_passthrough():
    entry, _ = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, ai=True),
        "next",
        field_texts={"due": {"text": "6 Jun", "fill": 0.1}},
        act_text="Amended action",
    )
    assert entry["fields"]["due"]["text"] == "6 Jun"
    assert entry["act_text"] == "Amended action"


def test_ai_reading_passthrough():
    reading = {
        "handwriting": "Tell Louise I pulled out", "understood": True, "confidence": 0.9,
        "note": "struck through and rewritten",
        "operations": [{
            "op": "update", "text": "Tell Louise I pulled out", "priority": None,
            "due": None, "project": None, "person": None, "to": None,
            "period": None, "name": None, "goal": None,
        }],
    }
    entry, _ = resolve_task(
        "NA-06",
        ticks(done=False, to_deleg=False, ai=True),
        "next",
        act_text="Tell Louise I pulled out",
        ai_reading=reading,
    )
    assert entry["ai_reading"] == reading


def test_ai_reading_absent_by_default():
    entry, _ = resolve_task(
        "NA-01",
        ticks(done=True, to_deleg=False, ai=False),
        "next",
    )
    assert "ai_reading" not in entry and "suggestion" not in entry


def test_new_project_flag_is_orthogonal():
    entry, warnings = resolve_task(
        "NA-01",
        ticks(done=False, to_deleg=False, ai=False, new_project=True),
        "next",
        field_texts={"project": {"text": "Wedding 2026", "fill": 0.08}},
    )
    assert entry["new_project"] is True
    # It is a flag, never an action, and never a conflict.
    assert entry["action"] == "none"
    assert warnings == []


def test_new_project_defaults_false():
    entry, _ = resolve_task("NA-01", ticks(done=True), "next")
    assert entry["new_project"] is False


def test_capture_and_newproj_buckets_use_inbox_verbs():
    assert BUCKET_ACTIONS["capture"] == BUCKET_ACTIONS["inbox"]
    assert BUCKET_ACTIONS["newproj"] == BUCKET_ACTIONS["inbox"]


def test_newproj_row_routing_tick_wins_over_creating_a_project():
    """A row on the New Projects page that turns out not to be a project."""
    entry, warnings = resolve_task(
        "NP-01",
        ticks(to_next=False, to_deleg=True, drop=False, defer_1w=False,
              defer_1m=False, defer_1q=False, ai=False),
        "newproj",
        field_texts={"to": {"text": "Louise", "fill": 0.06}},
        act_text="Get the PSU quote",
    )
    assert entry["action"] == "to_deleg" and warnings == []
    entry, _ = resolve_task(
        "CP-01",
        ticks(to_next=True, to_deleg=False, drop=False),
        "capture",
        act_text="Buy a new kettle",
    )
    assert entry["action"] == "to_next"
    assert entry["act_text"] == "Buy a new kettle"


def test_project_bucket_only_completes():
    entry, _ = resolve_task(
        "P01-03", ticks(done=True, ai=False), "project",
    )
    assert entry["action"] == "done"
    entry, warnings = resolve_task(
        "P01-03", ticks(done=False, ai=True), "project",
    )
    assert entry["action"] == "none" and entry["ai"] is True
    assert warnings == []


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
