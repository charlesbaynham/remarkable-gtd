"""Resolve raw tick/ink evidence into per-task decisions JSON.

A task's gutter may have several inked boxes; precedence resolves them to a
single ``action`` and conflicts are surfaced as warnings rather than
silently dropped. ``new_project`` is an orthogonal flag ("the project named
next to this row does not exist yet — create it"), never a primary action.
Raw fill ratios are retained under ``ticks`` so a human or downstream agent
can audit ambiguous rows.

The ✦ AI box is different in kind: it is not an action and not an
annotation but an *escape hatch* — "do not process this row
deterministically, hand it to the agent". So a row whose AI box is ticked
comes back with ``ai: true``, ``action: "none"``, ``new_project: false``
and the deterministic resolution demoted into ``suggestion``. The
deterministic reading is still computed (it is free, and it is useful
context for the agent) but it is structurally incapable of driving a vault
write: there is exactly one writer per row, and on an AI row that writer is
the agent.
"""
from __future__ import annotations

from remarkable_gtd.common.schema import DECISIONS_SCHEMA

# Primary (mutually exclusive) gutter verbs per bucket. ``ai`` is excluded:
# it is the escape hatch, not an action.
BUCKET_ACTIONS = {
    "inbox": ["to_next", "to_deleg", "drop"],
    "next": ["done", "to_deleg"],
    "delegated": ["done", "to_me"],
    "tickler": ["activate", "done"],
    # An unchecked item printed on its own project page: tick it off, or
    # hand the row to the agent with ✦ AI.
    "project": ["done"],
}
# A blank capture row on the Inbox page carries the Inbox gutter, so the
# same verbs apply to whatever gets written on it. So does a blank
# new-project row, where a routing tick means "on reflection this is not a
# project, route the text instead".
BUCKET_ACTIONS["capture"] = BUCKET_ACTIONS["inbox"]
BUCKET_ACTIONS["newproj"] = BUCKET_ACTIONS["inbox"]

DEFER_KEYS = ["defer_1w", "defer_1m", "defer_1q"]
REDEFER_KEYS = ["redefer_1w", "redefer_1m", "redefer_1q"]

# Higher precedence first; "defer" stands for whichever defer/redefer box won.
PRECEDENCE = ["done", "activate", "to_next", "to_me", "to_deleg", "drop", "defer"]

# The ✦ AI box's ROI verb. Sheets printed before the box was renamed carry
# ``edit``; it means the same thing and is read as the AI flag.
AI_TICK_KEYS = ("ai", "edit")


def _defer_period(key: str) -> str:
    """``"defer_1m"`` / ``"redefer_1m"`` -> ``"1m"``."""
    return key.rsplit("_", 1)[1]


def ai_requested(ticks: dict[str, tuple[float, bool]]) -> bool:
    """Is the ✦ AI box ticked? (``edit`` is its pre-rename ROI name.)"""
    return any(ticks.get(k, (0, False))[1] for k in AI_TICK_KEYS)


def resolve_action(
    task_id: str,
    ticks: dict[str, tuple[float, bool]],
    bucket: str,
) -> tuple[str, str | None, list[str]]:
    """Resolve a gutter's inked boxes to ``(action, defer_period, warnings)``.

    This is the whole deterministic classifier: no model, no vault, no
    side effects. :func:`resolve_task` uses it for a normal row, and the
    pipeline uses it to compute the *suggestion* handed to the agent on an
    AI row.
    """
    warnings: list[str] = []
    defer_keys = REDEFER_KEYS if bucket == "tickler" else DEFER_KEYS

    # Which primary verbs are inked?
    candidates: list[str] = [
        v for v in BUCKET_ACTIONS.get(bucket, []) if ticks.get(v, (0, False))[1]
    ]

    # Resolve the defer trio to (at most) one period.
    defer_results = {k: ticks[k] for k in defer_keys if k in ticks}
    inked_defers = {k: f for k, (f, ok) in defer_results.items() if ok}
    defer_key: str | None = None
    if inked_defers:
        defer_key = max(inked_defers, key=inked_defers.get)
        if len(inked_defers) > 1:
            warnings.append(
                f"{task_id}: multiple defer boxes inked "
                f"({', '.join(sorted(inked_defers))}) — chose '{defer_key}' by max fill"
            )
        candidates.append("defer")

    # Pick the primary action by precedence.
    action = "none"
    if candidates:
        ordered = sorted(candidates, key=PRECEDENCE.index)
        action = ordered[0]
        if len(candidates) > 1:
            warnings.append(
                f"{task_id}: multiple gutter boxes inked "
                f"({', '.join(sorted(candidates))}) — chose '{action}' by precedence"
            )

    period = _defer_period(defer_key) if action == "defer" and defer_key else None
    return action, period, warnings


def build_suggestion(
    task_id: str,
    ticks: dict[str, tuple[float, bool]],
    bucket: str,
    field_texts: dict[str, dict] | None = None,
    act_text: str | None = None,
) -> dict:
    """The deterministic reading of an AI row, as a *suggestion* only.

    Handed to the agent as context and recorded in the decisions document
    for audit. Nothing downstream may apply it: on an AI row the agent owns
    the single write.
    """
    action, period, _warnings = resolve_action(task_id, ticks, bucket)
    suggestion: dict = {
        "action": action,
        "new_project": ticks.get("new_project", (0, False))[1],
    }
    if period is not None:
        suggestion["defer_period"] = period
    if field_texts:
        suggestion["fields"] = {f: v.get("text", "") for f, v in field_texts.items()}
    if act_text:
        suggestion["text"] = act_text
    return suggestion


def resolve_task(
    task_id: str,
    ticks: dict[str, tuple[float, bool]],
    bucket: str,
    field_texts: dict[str, dict] | None = None,
    act_text: str | None = None,
    ai_reading: dict | None = None,
) -> tuple[dict, list[str]]:
    """Build the decisions entry for one task from its tick evidence.

    Args:
        task_id: Stable task id (e.g. ``"NA-05"``).
        ticks: Mapping of verb -> ``(fill_ratio, inked)`` for every gutter
            box of this task (including the defer trio and ✦ AI).
        bucket: Bucket key (``inbox``/``next``/``delegated``/``tickler``/
            ``project``/``capture``/``newproj``) — the task's own bucket,
            which on the Inbox page is ``capture`` for the blank write-in
            rows.
        field_texts: Optional ``{field: {"text": ..., ...}}`` of OCR'd slots.
        act_text: Optional OCR of the action region.
        ai_reading: Optional structured reading of the whole row by the
            agent (``gtd.ai/3``, see
            :data:`remarkable_gtd.scan.ocr.AI_SCHEMA`).

    Returns:
        ``(task_entry, warnings)``. When the ✦ AI box is ticked the entry's
        ``action`` is ``"none"`` and ``new_project`` is ``False``: the
        deterministic resolution is recorded under ``suggestion`` instead,
        so that no deterministic write can be derived from an AI row.
    """
    ai = ai_requested(ticks)
    action, period, warnings = resolve_action(task_id, ticks, bucket)

    entry: dict = {
        "id": task_id,
        "action": "none" if ai else action,
        "ai": ai,
        "new_project": (
            False if ai else ticks.get("new_project", (0, False))[1]
        ),
        "ticks": {
            verb: {"inked": inked, "fill": round(fill, 4)}
            for verb, (fill, inked) in sorted(ticks.items())
        },
    }
    if not ai and action == "defer" and period is not None:
        entry["defer_period"] = period
    if field_texts:
        entry["fields"] = field_texts
    if act_text is not None:
        entry["act_text"] = act_text
    if ai:
        entry["suggestion"] = build_suggestion(
            task_id, ticks, bucket, field_texts, act_text
        )
    if ai_reading is not None:
        entry["ai_reading"] = ai_reading
    return entry, warnings


def build_decisions(
    bucket: str,
    the_date: str,
    header_qr: str,
    tasks: list[dict],
    captures: list[dict],
    rectify_meta: dict,
    source_image: str,
    manifest_path: str,
    warnings: list[str],
) -> dict:
    """Assemble the full decisions document (schema ``gtd.decisions/2``)."""
    return {
        "schema": DECISIONS_SCHEMA,
        "source_image": source_image,
        "manifest": manifest_path,
        "bucket": bucket,
        "date": the_date,
        "header_qr": header_qr,
        "rectify": rectify_meta,
        "tasks": tasks,
        "captures": captures,
        "warnings": warnings,
    }
