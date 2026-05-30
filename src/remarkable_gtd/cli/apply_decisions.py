#!/usr/bin/env python3
"""gtd-apply-decisions CLI entry point."""
from __future__ import annotations

import argparse
from pathlib import Path

from remarkable_gtd.vault.applier import apply_decisions


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Apply scanned decisions back to the GTD vault.")
    p.add_argument("decisions", help="Path to decisions JSON file.")
    p.add_argument("--tasks-json", required=True, help="Path to tasks JSON file.")
    p.add_argument("--gtd-dir", required=True, help="Path to GTD vault directory.")
    args = p.parse_args(argv)

    try:
        results = apply_decisions(
            Path(args.decisions),
            Path(args.tasks_json),
            Path(args.gtd_dir),
        )
    except Exception as e:
        print(f"Error: {e}", file=__import__("sys").stderr)
        return 1

    for result in results:
        print(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
