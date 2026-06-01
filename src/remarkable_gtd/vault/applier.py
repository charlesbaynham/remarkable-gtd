"""Apply scanned decisions back to the Obsidian GTD vault."""

from __future__ import annotations

import json
import re
from pathlib import Path

# Action → (source_bucket, target_bucket) mapping
ACTION_TARGETS = {
    "done": ("remove", None),
    "to_deleg": ("next", "delegated"),
    "to_next": ("inbox", "next"),
    "drop": ("remove", None),
    "defer_1w": ("any", "tickler_week"),
    "defer_1m": ("any", "tickler_month"),
    "defer_1q": ("any", "tickler_quarter"),
    "to_me": ("delegated", "next"),
    "activate": ("tickler", "next"),
    "redefer_1w": ("tickler", "tickler_week"),
    "redefer_1m": ("tickler", "tickler_month"),
    "redefer_1q": ("tickler", "tickler_quarter"),
}


def _escape_regex(text: str) -> str:
    return re.escape(text)


def _remove_from_table(file_path: Path, action_text: str) -> bool:
    """Remove a row from a markdown table by matching action text.

    Returns True if a row was removed.
    """
    if not file_path.exists():
        return False
    lines = file_path.read_text(encoding="utf-8").splitlines()
    anchor = _escape_regex(action_text)
    pattern = re.compile(r"^\|[^|]*" + anchor + r"[^|]*\|.*$")

    new_lines = []
    removed = False
    for line in lines:
        if not removed and pattern.match(line):
            removed = True
            continue
        new_lines.append(line)

    if removed:
        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed


def _remove_from_list_file(file_path: Path, action_text: str) -> bool:
    """Remove a line from a simple list file by matching action text."""
    if not file_path.exists():
        return False
    lines = file_path.read_text(encoding="utf-8").splitlines()
    anchor = _escape_regex(action_text)
    pattern = re.compile(anchor)

    new_lines = []
    removed = False
    for line in lines:
        if not removed and pattern.search(line):
            removed = True
            continue
        new_lines.append(line)

    if removed:
        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed


def _append_to_table(file_path: Path, action_text: str, **extra_cols) -> None:
    """Append a row to a markdown table. Creates file with header if needed."""
    if file_path.exists():
        text = file_path.read_text(encoding="utf-8")
    else:
        text = ""

    # Determine columns from existing header or use defaults
    if file_path.name == "Next actions.md":
        header = "| Action | Project | Deadline | Priority |\n| --- | --- | --- | --- |"
        row = f"| {action_text} | {extra_cols.get('proj', '')} | {extra_cols.get('due', '')} | {extra_cols.get('pri', '')} |"
    elif file_path.name == "Delegated.md":
        header = "| Thing | Person | Chase by | |\n| --- | --- | --- | --- |"
        row = f"| {action_text} | {extra_cols.get('to', '')} | {extra_cols.get('due', '')} | |"
    else:
        # Generic two-column
        header = "| Action | |\n| --- | --- |"
        row = f"| {action_text} | |"

    if not text.strip():
        file_path.write_text(header + "\n" + row + "\n", encoding="utf-8")
    else:
        file_path.write_text(text.rstrip() + "\n" + row + "\n", encoding="utf-8")


def _append_to_list(file_path: Path, action_text: str) -> None:
    """Append a line to a simple list file."""
    if file_path.exists():
        text = file_path.read_text(encoding="utf-8")
    else:
        text = ""
    file_path.write_text(text.rstrip() + "\n" + action_text + "\n", encoding="utf-8")


def _update_table_row(file_path: Path, action_text: str, fields: dict) -> bool:
    """Update fields in a table row. Returns True if updated."""
    if not file_path.exists():
        return False
    lines = file_path.read_text(encoding="utf-8").splitlines()
    anchor = _escape_regex(action_text)
    pattern = re.compile(r"^(\|[^|]*" + anchor + r"[^|]*\|.*)$")

    new_lines = []
    updated = False
    for line in lines:
        m = pattern.match(line)
        if m and not updated:
            # Parse the row, update fields, rebuild
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c or c == ""]
            # cells[0] = Action, cells[1] = Project, cells[2] = Deadline, cells[3] = Priority
            if len(cells) >= 4:
                if "priority" in fields:
                    cells[3] = fields["priority"]
                if "due" in fields:
                    cells[2] = fields["due"]
                if "project" in fields:
                    cells[1] = fields["project"]
                if "act" in fields:
                    cells[0] = fields["act"]
                line = "| " + " | ".join(cells) + " |"
                updated = True
        new_lines.append(line)

    if updated:
        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


def _lookup_task(tasks_json: dict, task_id: str) -> dict | None:
    """Find a task by ID in the tasks.json data."""
    bucket_map = {
        "IN": "inbox",
        "NA": "next",
        "DG": "delegated",
        "TK": "tickler",
    }
    prefix = task_id.split("-")[0] if "-" in task_id else task_id[:2]
    bucket = bucket_map.get(prefix)
    if not bucket:
        return None

    if bucket == "tickler":
        # Tickler tasks don't have IDs in the same way; search all periods
        for period in ["week", "month", "quarter"]:
            for t in tasks_json.get("tickler", {}).get(period, []):
                if t.get("id") == task_id:
                    return t
        return None

    for t in tasks_json.get(bucket, []):
        if t.get("id") == task_id:
            return t
    return None


def apply_task_decision(
    task_id: str,
    action: str,
    fields: dict,
    tasks_json: dict,
    gtd_dir: Path,
) -> str | None:
    """Apply a single task decision to the vault.

    Returns a human-readable description of what was done, or None if skipped.
    """
    # Normalize fields: pipeline stores {"text": str, "ocr_conf": ...} dicts; extract text, drop empties
    fields = {
        k: (v["text"].strip() if isinstance(v, dict) else str(v).strip())
        for k, v in fields.items()
        if (v["text"].strip() if isinstance(v, dict) else str(v).strip())
    }

    if action == "none" and not fields:
        return None

    task = _lookup_task(tasks_json, task_id)
    if not task:
        return f"⚠ Could not find task {task_id} in tasks.json"

    action_text = task.get("act", "")
    if not action_text:
        return f"⚠ Task {task_id} has no action text"

    # Determine source and target files
    prefix = task_id.split("-")[0] if "-" in task_id else task_id[:2]
    source_map = {
        "IN": gtd_dir / "Inbox.md",
        "NA": gtd_dir / "Next actions.md",
        "DG": gtd_dir / "Delegated.md",
    }
    source_file = source_map.get(prefix)  # None for TK — resolved by search below

    target_map = {
        "next": gtd_dir / "Next actions.md",
        "delegated": gtd_dir / "Delegated.md",
        "tickler_week": gtd_dir / "Tickler" / "Next week.md",
        "tickler_month": gtd_dir / "Tickler" / "Next month.md",
        "tickler_quarter": gtd_dir / "Tickler" / "Next quarter.md",
    }

    # Handle edit first
    if action == "none" and fields:
        # Just an edit, no action
        if source_file:
            ok = _update_table_row(source_file, action_text, fields)
            return f"✎ Updated {task_id} in {source_file.name}"
        return f"⚠ Cannot edit {task_id}: unknown source file"

    if action not in ACTION_TARGETS:
        return f"⚠ Unknown action '{action}' for {task_id}"

    src_type, target_key = ACTION_TARGETS[action]

    _tickler_files = [
        gtd_dir / "Tickler" / "Next week.md",
        gtd_dir / "Tickler" / "Next two weeks.md",
        gtd_dir / "Tickler" / "Next month.md",
        gtd_dir / "Tickler" / "Next quarter.md",
    ]

    def _remove_from_source() -> tuple[bool, str]:
        """Try to remove action_text from the appropriate source file(s).

        Returns (removed, source_name).
        """
        if source_file:
            if _remove_from_table(source_file, action_text):
                return True, source_file.name
            if _remove_from_list_file(source_file, action_text):
                return True, source_file.name
            return False, source_file.name
        # TK: search all tickler files
        for tf in _tickler_files:
            if _remove_from_list_file(tf, action_text):
                return True, tf.name
            if _remove_from_table(tf, action_text):
                return True, tf.name
        return False, "tickler"

    if src_type == "remove":
        removed, src_name = _remove_from_source()
        if removed:
            return f"✓ Removed {task_id} ({action}) from {src_name}"
        return f"⚠ Could not remove {task_id}"

    # Move operation
    target_file = target_map.get(target_key)
    if not target_file:
        return f"⚠ Unknown target for action '{action}'"

    # Remove from source
    removed, src_name = _remove_from_source()

    # Append to target
    extra = {}
    if target_key == "next":
        extra = {
            "proj": task.get("proj", ""),
            "due": task.get("due", ""),
            "pri": str(task.get("pri", "")),
        }
    elif target_key == "delegated":
        extra = {
            "to": task.get("to", ""),
            "due": task.get("due", ""),
        }

    if target_key.startswith("tickler_"):
        _append_to_list(target_file, action_text)
    else:
        _append_to_table(target_file, action_text, **extra)

    return f"✓ Moved {task_id} ({action}) from {src_name} to {target_file.name}"


def apply_captures(captures: list[dict], gtd_dir: Path) -> list[str]:
    """Append capture lines to Inbox.md.

    Returns list of human-readable descriptions.
    """
    inbox_file = gtd_dir / "Inbox.md"
    results = []
    for cap in captures:
        text = cap.get("text", "").strip()
        if text:
            _append_to_list(inbox_file, text)
            results.append(f"✓ Captured: {text[:60]}")
    return results


def apply_decisions(
    decisions_path: Path, tasks_json_path: Path, gtd_dir: Path
) -> list[str]:
    """Apply all decisions from a decisions JSON file to the GTD vault.

    Returns list of human-readable operation descriptions.
    """
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    tasks_json = json.loads(tasks_json_path.read_text(encoding="utf-8"))

    results = []

    # Flatten tasks from either single-page {"tasks": [...]} or multi-page {"pages": [...]} format
    if "pages" in decisions:
        all_tasks = [task for page in decisions["pages"] for task in page.get("tasks", [])]
    else:
        all_tasks = decisions.get("tasks", [])

    for task in all_tasks:
        task_id = task.get("id", "")
        action = task.get("action", "none")
        fields = task.get("fields", {})

        if action == "none" and not fields:
            continue  # No action needed

        desc = apply_task_decision(task_id, action, fields, tasks_json, gtd_dir)
        if desc:
            results.append(desc)

    # Handle captures from either format
    if "captures" in decisions:
        results.extend(apply_captures(decisions["captures"], gtd_dir))
    elif "pages" in decisions:
        for page in decisions["pages"]:
            results.extend(apply_captures(page.get("captures", []), gtd_dir))

    return results
