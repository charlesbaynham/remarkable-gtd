"""Tests for vault applier."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from remarkable_gtd.vault.applier import (
    apply_decisions,
    apply_task_decision,
)


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """Create a synthetic GTD vault."""
    gtd = tmp_path / "gtd"
    gtd.mkdir()

    (gtd / "Next actions.md").write_text(
        "| Action | Project | Deadline | Priority |\n"
        "| --- | --- | --- | --- |\n"
        "| Give Oliver final copy | EPSRC | 30 May | 7 |\n"
        "| Submit EOIs | | 2 Jun | 6 |\n"
        "| Draft impact statement | epsrc | 3 Jun | 5 |\n",
        encoding="utf-8",
    )

    (gtd / "Delegated.md").write_text(
        "| Thing | Person | Chase by | |\n"
        "| --- | --- | --- | --- |\n"
        "| Run full sweep | Oliver | 1 Jun | |\n",
        encoding="utf-8",
    )

    (gtd / "Inbox.md").write_text(
        "Reply to reviewer\n" "Book flights\n",
        encoding="utf-8",
    )

    tickler = gtd / "Tickler"
    tickler.mkdir()
    (tickler / "Next week.md").write_text(
        "Check camera-ready instructions\n", encoding="utf-8"
    )

    return gtd


@pytest.fixture
def tasks_json(sample_vault: Path) -> Path:
    """Create a tasks.json matching the vault."""
    tasks = {
        "date": "2026-05-30",
        "inbox": [
            {"act": "Reply to reviewer"},
            {"act": "Book flights"},
        ],
        "next": [
            {"id": "NA-01", "pri": 7, "due": "30 May", "proj": "EPSRC", "act": "Give Oliver final copy"},
            {"id": "NA-02", "pri": 6, "due": "2 Jun", "proj": "", "act": "Submit EOIs"},
            {"id": "NA-03", "pri": 5, "due": "3 Jun", "proj": "epsrc", "act": "Draft impact statement"},
        ],
        "delegated": [
            {"id": "DG-01", "pri": 0, "due": "1 Jun", "proj": "", "to": "Oliver", "act": "Run full sweep"},
        ],
        "tickler": {
            "week": [{"act": "Check camera-ready instructions"}],
            "month": [],
            "quarter": [],
        },
    }
    path = sample_vault / "tasks.json"
    path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    return path


def test_apply_done_removes_from_next(sample_vault: Path, tasks_json: Path) -> None:
    decisions = {
        "schema": "gtd.decisions/1",
        "tasks": [
            {"id": "NA-01", "action": "done", "edited": False, "fields": {}, "ticks": {}, "warnings": []},
        ],
        "captures": [],
        "warnings": [],
    }
    decisions_path = sample_vault / "decisions.json"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    results = apply_decisions(decisions_path, tasks_json, sample_vault)

    # Should have removed NA-01
    assert any("Removed" in r for r in results)
    next_text = (sample_vault / "Next actions.md").read_text(encoding="utf-8")
    assert "Give Oliver final copy" not in next_text
    assert "Submit EOIs" in next_text  # NA-02 should still be there


def test_apply_to_deleg_moves_to_delegated(sample_vault: Path, tasks_json: Path) -> None:
    decisions = {
        "schema": "gtd.decisions/1",
        "tasks": [
            {"id": "NA-02", "action": "to_deleg", "edited": False, "fields": {}, "ticks": {}, "warnings": []},
        ],
        "captures": [],
        "warnings": [],
    }
    decisions_path = sample_vault / "decisions.json"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    results = apply_decisions(decisions_path, tasks_json, sample_vault)

    next_text = (sample_vault / "Next actions.md").read_text(encoding="utf-8")
    assert "Submit EOIs" not in next_text

    deleg_text = (sample_vault / "Delegated.md").read_text(encoding="utf-8")
    assert "Submit EOIs" in deleg_text


def test_apply_captures_to_inbox(sample_vault: Path, tasks_json: Path) -> None:
    decisions = {
        "schema": "gtd.decisions/1",
        "tasks": [],
        "captures": [
            {"line": "capture:N1:line", "text": "New idea: extend model", "inked": True, "ocr_conf": None},
        ],
        "warnings": [],
    }
    decisions_path = sample_vault / "decisions.json"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    results = apply_decisions(decisions_path, tasks_json, sample_vault)

    inbox_text = (sample_vault / "Inbox.md").read_text(encoding="utf-8")
    assert "New idea: extend model" in inbox_text


def test_apply_no_action_noop(sample_vault: Path, tasks_json: Path) -> None:
    """A decision with action='none' and no fields should not modify anything."""
    original = (sample_vault / "Next actions.md").read_text(encoding="utf-8")

    decisions = {
        "schema": "gtd.decisions/1",
        "tasks": [
            {"id": "NA-01", "action": "none", "edited": False, "fields": {}, "ticks": {}, "warnings": []},
        ],
        "captures": [],
        "warnings": [],
    }
    decisions_path = sample_vault / "decisions.json"
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

    apply_decisions(decisions_path, tasks_json, sample_vault)

    assert (sample_vault / "Next actions.md").read_text(encoding="utf-8") == original
