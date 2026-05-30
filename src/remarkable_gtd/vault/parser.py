"""Parse Obsidian GTD vault into tasks.json format for gtd-gen."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def _clean_action_text(text: str) -> str:
    """Remove wiki-links, image tags, hashtags, normalize whitespace."""
    # Remove image tags entirely: ![[...]]
    text = re.sub(r"!\[\[(.*?)\]\]", "", text)
    # Unwrap wiki-links: [[...]] -> inner text
    text = re.sub(r"\[\[(.*?)\]\]", r"\1", text)
    # Remove hashtags (keep text)
    text = re.sub(r"#(\w+)", r"\1", text)
    # Normalize whitespace, collapse multiple spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_table_rows(lines: list[str], header_cols: list[str]) -> list[dict]:
    """Parse markdown table lines into dict rows.

    Handles tables where header defines columns. Stops at first non-table line.
    Returns list of dicts with keys matching header column names (lowercased).
    """
    rows = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Skip separator line
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue
        # Skip header line (contains column names)
        if any(col.lower() in stripped.lower() for col in header_cols):
            # Check if this is the header by seeing if ALL header cols are present
            all_present = all(col.lower() in stripped.lower() for col in header_cols)
            if all_present:
                continue

        cells = [c.strip() for c in stripped.split("|")]
        # Drop at most one empty edge cell from each side (markdown table artifacts)
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if len(cells) < len(header_cols):
            continue

        row = {}
        for i, col in enumerate(header_cols):
            row[col.lower()] = cells[i] if i < len(cells) else ""
        rows.append(row)
    return rows


def parse_inbox(path: Path) -> list[dict]:
    """Parse Inbox.md into list of {act: ...} dicts.

    Inbox is free-form text. We extract non-empty lines as individual items.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    items = []
    # Split by lines, treating each non-empty line as an item
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip markdown headers
        if line.startswith("#"):
            continue
        cleaned = _clean_action_text(line)
        # Remove leading bullet markers
        cleaned = re.sub(r"^[-*]\s+", "", cleaned)
        if cleaned:
            items.append({"act": cleaned})
    return items


def parse_next_actions(path: Path) -> list[dict]:
    """Parse Next actions.md table into list of {id, pri, due, proj, act} dicts.

    Table columns: Action | Project | Deadline | Priority
    """
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    header_cols = ["Action", "Project", "Deadline", "Priority"]
    rows = _parse_table_rows(lines, header_cols)

    actions = []
    for i, row in enumerate(rows, start=1):
        action_text = _clean_action_text(row.get("action", ""))
        if not action_text:
            continue
        proj = _clean_action_text(row.get("project", ""))
        due = row.get("deadline", "").strip()
        pri_str = row.get("priority", "").strip()
        try:
            pri = int(pri_str) if pri_str else 0
        except ValueError:
            pri = 0

        actions.append(
            {
                "id": f"NA-{i:02d}",
                "pri": pri,
                "due": due,
                "proj": proj,
                "act": action_text,
            }
        )
    return actions


def parse_delegated(path: Path) -> list[dict]:
    """Parse Delegated.md table into list of {id, pri, due, proj, to, act} dicts.

    Table columns: Thing | Person | Chase by | (empty)
    """
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    header_cols = ["Thing", "Person", "Chase by"]
    rows = _parse_table_rows(lines, header_cols)

    actions = []
    for i, row in enumerate(rows, start=1):
        action_text = _clean_action_text(row.get("thing", ""))
        if not action_text:
            continue
        to = row.get("person", "").strip()
        due = row.get("chase by", "").strip()

        actions.append(
            {
                "id": f"DG-{i:02d}",
                "pri": 0,
                "due": due,
                "proj": "",
                "to": to,
                "act": action_text,
            }
        )
    return actions


def parse_tickler(tickler_dir: Path) -> dict[str, list[dict]]:
    """Parse Tickler/*.md files into {week, month, quarter} lists.

    Looks for:
    - Next week.md -> week
    - Next two weeks.md -> week (combined)
    - Next month.md -> month
    - Next quarter.md -> quarter
    """
    result = {"week": [], "month": [], "quarter": []}
    if not tickler_dir.exists():
        return result

    # Map filenames to period
    week_files = ["Next week.md", "Next two weeks.md"]
    month_files = ["Next month.md"]
    quarter_files = ["Next quarter.md"]

    def _extract_items(path: Path) -> list[dict]:
        """Extract action items from a tickler file."""
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        items = []
        # Try to find table rows first
        lines = text.splitlines()
        header_cols = ["Action", "Project", "Deadline", "Priority"]
        table_rows = _parse_table_rows(lines, header_cols)
        for row in table_rows:
            act = _clean_action_text(row.get("action", ""))
            if act:
                items.append({"act": act})

        # Also extract non-table lines as items
        for line in lines:
            line = line.strip()
            if not line or line.startswith("|") or line.startswith("#"):
                continue
            # Skip checkbox items (they go in the table)
            cleaned = _clean_action_text(line)
            cleaned = re.sub(r"^-\s+\[.\]\s*", "", cleaned)
            cleaned = re.sub(r"^[-*]\s+", "", cleaned)
            if cleaned and cleaned not in [i["act"] for i in items]:
                items.append({"act": cleaned})
        return items

    for fname in week_files:
        result["week"].extend(_extract_items(tickler_dir / fname))
    for fname in month_files:
        result["month"].extend(_extract_items(tickler_dir / fname))
    for fname in quarter_files:
        result["quarter"].extend(_extract_items(tickler_dir / fname))

    return result


def build_tasks_json(gtd_dir: Path, the_date: date | None = None) -> dict:
    """Parse entire GTD vault into tasks.json format.

    Returns dict matching the schema expected by gtd-gen.
    """
    if the_date is None:
        the_date = date.today()

    inbox = parse_inbox(gtd_dir / "Inbox.md")
    nxt = parse_next_actions(gtd_dir / "Next actions.md")
    deleg = parse_delegated(gtd_dir / "Delegated.md")
    tick = parse_tickler(gtd_dir / "Tickler")

    return {
        "date": the_date.isoformat(),
        "inbox": inbox,
        "next": nxt,
        "delegated": deleg,
        "tickler": tick,
    }
