"""Unit tests for decision resolution."""

from __future__ import annotations

from remarkable_gtd.scan.decisions import build_decisions, resolve_task


def test_resolve_task_done_wins_over_defer():
    """If both done and defer_1m are ticked, done wins and a warning is added."""
    ticks = {
        "done": (0.15, True),
        "to_deleg": (0.0, False),
        "defer_1w": (0.0, False),
        "defer_1m": (0.12, True),
        "defer_1q": (0.0, False),
        "edit": (0.0, False),
    }
    result = resolve_task("NA-01", ticks, "next", {}, edited=False)
    assert result["action"] == "done"
    assert any("takes precedence" in w for w in result["warnings"])


def test_resolve_task_precedence_done_over_activate():
    """done > activate for tickler (done has higher precedence)."""
    ticks = {
        "activate": (0.15, True),
        "done": (0.10, True),
        "redefer_1w": (0.0, False),
        "redefer_1m": (0.0, False),
        "redefer_1q": (0.0, False),
        "edit": (0.0, False),
    }
    result = resolve_task("TK-01", ticks, "tickler", {}, edited=False)
    assert result["action"] == "done"


def test_resolve_task_none_ticked():
    """No ticks → action is 'none'."""
    ticks = {
        "done": (0.0, False),
        "to_deleg": (0.0, False),
        "defer_1w": (0.0, False),
        "defer_1m": (0.0, False),
        "defer_1q": (0.0, False),
        "edit": (0.0, False),
    }
    result = resolve_task("NA-01", ticks, "next", {}, edited=False)
    assert result["action"] == "none"
    assert result["edited"] is False


def test_resolve_task_edited_flag():
    """edit ticked → edited=True."""
    ticks = {
        "done": (0.0, False),
        "to_deleg": (0.0, False),
        "defer_1w": (0.0, False),
        "defer_1m": (0.0, False),
        "defer_1q": (0.0, False),
        "edit": (0.08, True),
    }
    result = resolve_task("NA-01", ticks, "next", {}, edited=False)
    assert result["edited"] is True


def test_resolve_task_field_texts_edited():
    """Field texts present → edited=True even without edit tick."""
    ticks = {
        "done": (0.0, False),
        "to_deleg": (0.0, False),
        "defer_1w": (0.0, False),
        "defer_1m": (0.0, False),
        "defer_1q": (0.0, False),
        "edit": (0.0, False),
    }
    fields = {"priority": {"text": "7", "ocr_conf": None}}
    result = resolve_task("NA-01", ticks, "next", fields, edited=False)
    assert result["edited"] is True


def test_resolve_task_conflict_warning():
    """Multiple non-defer actions ticked → conflict warning."""
    ticks = {
        "done": (0.15, True),
        "to_deleg": (0.12, True),
        "defer_1w": (0.0, False),
        "defer_1m": (0.0, False),
        "defer_1q": (0.0, False),
        "edit": (0.0, False),
    }
    result = resolve_task("NA-01", ticks, "next", {}, edited=False)
    assert result["action"] == "done"
    assert any("Multiple actions" in w for w in result["warnings"])


def test_build_decisions_structure():
    """build_decisions returns correct schema."""
    result = build_decisions(
        bucket="next",
        task_results=[],
        captures=[],
        rectify_meta={"residual_px": 0.5, "reg_marks_found": {}},
        header_qr="GTD|next|2026-05-30",
        source_image="/tmp/test.png",
        manifest_path="/tmp/test.manifest.json",
        the_date="2026-05-30",
    )
    assert result["schema"] == "gtd.decisions/1"
    assert result["bucket"] == "next"
    assert result["header_qr"] == "GTD|next|2026-05-30"
    assert "tasks" in result
    assert "captures" in result
