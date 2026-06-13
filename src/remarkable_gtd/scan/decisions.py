"""Resolve raw tick/ink evidence into per-task decisions JSON.

A task's gutter may have several inked boxes; precedence resolves them to a
single ``action`` and conflicts are surfaced as warnings rather than
silently dropped. ``edit`` is an orthogonal annotation flag ("re-read this
row"), never a primary action. Raw fill ratios are retained under
``ticks`` so a human or downstream agent can audit ambiguous rows.
"""
from __future__ import annotations

from remarkable_gtd.common.schema import DECISIONS_SCHEMA

# Primary (mutually exclusive) gutter verbs per bucket. ``edit`` is excluded:
# it is an annotation flag, not an action.
BUCKET_ACTIONS = {
    "inbox": ["to_next", "to_deleg", "drop"],
    "next": ["done", "to_deleg"],
    "delegated": ["done", "to_me"],
    "tickler": ["activate", "done"],
}

DEFER_KEYS = ["defer_1w", "defer_1m", "defer_1q"]
REDEFER_KEYS = ["redefer_1w", "redefer_1m", "redefer_1q"]

# Higher precedence first; "defer" stands for whichever defer/redefer box won.
PRECEDENCE = ["done", "activate", "to_next", "to_me", "to_deleg", "drop", "defer"]


def _defer_period(key: str) -> str:
    """``"defer_1m"`` / ``"redefer_1m"`` -> ``"1m"``."""
    return key.rsplit("_", 1)[1]


def resolve_task(
    task_id: str,
    ticks: dict[str, tuple[float, bool]],
    bucket: str,
    field_texts: dict[str, dict] | None = None,
    act_text: str | None = None,
) -> tuple[dict, list[str]]:
    """Build the decisions entry for one task from its tick evidence.

    Args:
        task_id: Stable task id (e.g. ``"NA-05"``).
        ticks: Mapping of verb -> ``(fill_ratio, inked)`` for every gutter
            box of this task (including defer trio and edit).
        bucket: Bucket key (``inbox``/``next``/``delegated``/``tickler``).
        field_texts: Optional ``{field: {"text": ..., ...}}`` of OCR'd slots.
        act_text: Optional OCR of the action region (when edit is ticked).

    Returns:
        ``(task_entry, warnings)``.
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

    edited = ticks.get("edit", (0, False))[1]

    entry: dict = {
        "id": task_id,
        "action": action,
        "edited": edited,
        "ticks": {
            verb: {"inked": inked, "fill": round(fill, 4)}
            for verb, (fill, inked) in sorted(ticks.items())
        },
    }
    if action == "defer" and defer_key is not None:
        entry["defer_period"] = _defer_period(defer_key)
    if field_texts:
        entry["fields"] = field_texts
    if act_text is not None:
        entry["act_text"] = act_text
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
    """Assemble the full decisions document (schema ``gtd.decisions/1``)."""
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
