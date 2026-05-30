"""Resolve tick marks into decisions JSON."""
from __future__ import annotations

BUCKET_ACTIONS = {
    "inbox": ["to_next", "to_deleg", "drop"],
    "next": ["done", "to_deleg"],
    "delegated": ["done", "to_me"],
    "tickler": ["activate", "done"],
}

DEFER_KEYS = ["defer_1w", "defer_1m", "defer_1q"]
REDEFER_KEYS = ["redefer_1w", "redefer_1m", "redefer_1q"]
PRECEDENCE = ["done", "activate", "to_next", "to_me", "to_deleg", "drop", "defer"]


def resolve_task(
    task_id: str,
    ticks: dict[str, tuple[float, bool]],
    bucket: str,
    field_texts: dict,
    edited: bool,
) -> dict:
    """Build the per-task decisions entry."""
    actions = BUCKET_ACTIONS.get(bucket, [])
    defer_keys = REDEFER_KEYS if bucket == "tickler" else DEFER_KEYS

    # Collect all ticked verbs
    ticked = {k: v[0] for k, v in ticks.items() if v[1]}

    # Determine primary action by precedence
    action = "none"
    for verb in PRECEDENCE:
        if verb == "defer":
            for dk in defer_keys:
                if dk in ticked:
                    action = dk
                    break
            if action != "none":
                break
        elif verb in ticked:
            action = verb
            break

    # Determine if edited (edit ticked or any slot has text)
    is_edited = edited or "edit" in ticked or bool(field_texts)

    # Warnings for conflicts
    warnings = []
    ticked_actions = [k for k, v in ticked.items() if v]
    if len(ticked_actions) > 1:
        # Check if multiple non-defer actions
        non_defer = [a for a in ticked_actions if a not in defer_keys and a != "edit"]
        if len(non_defer) > 1:
            warnings.append(f"Multiple actions ticked: {non_defer}")
        # If defer also ticked with a higher precedence action
        if action in ["done", "activate"]:
            deferred = [a for a in ticked_actions if a in defer_keys]
            if deferred:
                warnings.append(f"{action} takes precedence over {deferred}")

    return {
        "id": task_id,
        "qr_verified": True,
        "action": action,
        "edited": is_edited,
        "fields": field_texts,
        "ticks": {k: {"inked": v[1], "fill": round(v[0], 4)} for k, v in ticks.items()},
        "warnings": warnings,
    }


def build_decisions(
    bucket: str,
    task_results: list,
    captures: list,
    rectify_meta: dict,
    header_qr: str,
    source_image: str,
    manifest_path: str,
    the_date: str,
) -> dict:
    """Assemble the full decisions JSON."""
    from remarkable_gtd.common.schema import DECISIONS_SCHEMA

    return {
        "schema": DECISIONS_SCHEMA,
        "source_image": source_image,
        "manifest": manifest_path,
        "bucket": bucket,
        "date": the_date,
        "header_qr": header_qr,
        "rectify": rectify_meta,
        "tasks": task_results,
        "captures": captures,
        "warnings": [],
    }
