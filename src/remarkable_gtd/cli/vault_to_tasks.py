#!/usr/bin/env python3
"""gtd-vault-to-tasks CLI — parse Obsidian GTD vault into tasks.json."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from remarkable_gtd.vault.parser import build_tasks_json


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Parse GTD vault into tasks.json for gtd-gen."
    )
    p.add_argument(
        "--gtd-dir", default=str(Path.home() / "gtd"), help="Path to GTD vault."
    )
    p.add_argument("--date", default=None, help="Override date (YYYY-MM-DD).")
    p.add_argument(
        "-o", "--output", default="tasks.json", help="Output tasks JSON path."
    )
    args = p.parse_args(argv)

    gtd_dir = Path(args.gtd_dir)
    if not gtd_dir.exists():
        print(
            f"Error: GTD directory not found: {gtd_dir}", file=__import__("sys").stderr
        )
        return 1

    if args.date:
        from datetime import datetime

        the_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        the_date = date.today()

    tasks = build_tasks_json(gtd_dir, the_date)

    out_path = Path(args.output)
    out_path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")

    counts = {
        "inbox": len(tasks.get("inbox", [])),
        "next": len(tasks.get("next", [])),
        "delegated": len(tasks.get("delegated", [])),
        "tickler": sum(len(v) for v in tasks.get("tickler", {}).values()),
    }
    print(f"✓ wrote {out_path}  ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
