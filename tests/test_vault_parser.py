"""Tests for vault parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from remarkable_gtd.vault.parser import (
    build_tasks_json,
    parse_delegated,
    parse_inbox,
    parse_next_actions,
    parse_tickler,
)


@pytest.fixture
def sample_vault(tmp_path: Path) -> Path:
    """Create a synthetic GTD vault."""
    gtd = tmp_path / "gtd"
    gtd.mkdir()

    # Inbox.md
    (gtd / "Inbox.md").write_text(
        "# Inbox\n\n"
        "- Reply to reviewer about variance estimator\n"
        "- Book flights for NeurIPS\n"
        "Idea: extend continuum model to multi-agent case\n",
        encoding="utf-8",
    )

    # Next actions.md
    (gtd / "Next actions.md").write_text(
        "| Action | Project | Deadline | Priority |\n"
        "| --- | --- | --- | --- |\n"
        "| Give Oliver final copy of EPSRC proposal | [[EPSRC]] | 30 May | 7 |\n"
        "| Submit placeholder EOIs | | 2 Jun | 6 |\n"
        "| Draft impact statement | epsrc | 3 Jun | |\n"
        "| Buy coffee | #shopping | | 3 |\n",
        encoding="utf-8",
    )

    # Delegated.md
    (gtd / "Delegated.md").write_text(
        "| Thing | Person | Chase by | |\n"
        "| --- | --- | --- | --- |\n"
        "| Run full sweep on cluster | Oliver | 1 Jun | |\n"
        "| Clean benchmark dataset | Priya | 7 Jun | |\n",
        encoding="utf-8",
    )

    # Tickler files
    tickler = gtd / "Tickler"
    tickler.mkdir()
    (tickler / "Next week.md").write_text(
        "Check camera-ready instructions\n" "Follow up with Tom about consent forms\n",
        encoding="utf-8",
    )
    (tickler / "Next month.md").write_text(
        "| Action | Project | Deadline | Priority |\n"
        "| --- | --- | --- | --- |\n"
        "| Plan summer intern logistics | | | |\n"
        "\nStart thinking about GPU allocation review\n",
        encoding="utf-8",
    )
    (tickler / "Next quarter.md").write_text(
        "Reassess survey paper scope\n" "Plan next round of PhD recruitment\n",
        encoding="utf-8",
    )

    return gtd


def test_parse_inbox(sample_vault: Path) -> None:
    items = parse_inbox(sample_vault / "Inbox.md")
    assert len(items) == 3
    assert items[0]["act"] == "Reply to reviewer about variance estimator"
    assert items[1]["act"] == "Book flights for NeurIPS"
    assert items[2]["act"] == "Idea: extend continuum model to multi-agent case"


def test_parse_next_actions(sample_vault: Path) -> None:
    actions = parse_next_actions(sample_vault / "Next actions.md")
    assert len(actions) == 4

    # First action
    assert actions[0]["id"] == "NA-01"
    assert actions[0]["act"] == "Give Oliver final copy of EPSRC proposal"
    assert actions[0]["proj"] == "EPSRC"
    assert actions[0]["due"] == "30 May"
    assert actions[0]["pri"] == 7

    # Action with empty priority
    assert actions[2]["pri"] == 0

    # Action with hashtag
    assert actions[3]["act"] == "Buy coffee"
    assert actions[3]["proj"] == "shopping"


def test_parse_delegated(sample_vault: Path) -> None:
    actions = parse_delegated(sample_vault / "Delegated.md")
    assert len(actions) == 2
    assert actions[0]["id"] == "DG-01"
    assert actions[0]["act"] == "Run full sweep on cluster"
    assert actions[0]["to"] == "Oliver"
    assert actions[0]["due"] == "1 Jun"


def test_parse_tickler(sample_vault: Path) -> None:
    tick = parse_tickler(sample_vault / "Tickler")
    assert len(tick["week"]) == 2
    assert len(tick["month"]) == 2  # table row + list item
    assert len(tick["quarter"]) == 2

    assert tick["week"][0]["act"] == "Check camera-ready instructions"
    assert tick["quarter"][1]["act"] == "Plan next round of PhD recruitment"


def test_build_tasks_json(sample_vault: Path) -> None:
    tasks = build_tasks_json(sample_vault, date(2026, 5, 30))
    assert tasks["date"] == "2026-05-30"
    assert len(tasks["inbox"]) == 3
    assert len(tasks["next"]) == 4
    assert len(tasks["delegated"]) == 2
    assert len(tasks["tickler"]["week"]) == 2
    assert len(tasks["tickler"]["month"]) == 2
    assert len(tasks["tickler"]["quarter"]) == 2


def test_parse_inbox_missing_file(tmp_path: Path) -> None:
    assert parse_inbox(tmp_path / "Inbox.md") == []


def test_parse_next_actions_empty_priority() -> None:
    """Empty priority cell should result in pri=0, not crash."""
    text = "| Action | Project | Deadline | Priority |\n| --- | --- | --- | --- |\n| Do thing | | | |\n"
    path = Path("/tmp/test_empty_pri.md")
    path.write_text(text, encoding="utf-8")
    actions = parse_next_actions(path)
    assert len(actions) == 1
    assert actions[0]["pri"] == 0
