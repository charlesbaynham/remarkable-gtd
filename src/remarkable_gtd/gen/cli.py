#!/usr/bin/env python3
"""gtd-gen CLI entry point."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from .generate import build_buckets, render_pdf


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Generate the GTD reMarkable PDF (one tall page per bucket)."
    )
    p.add_argument("tasks", help="Path to tasks JSON (see tasks.example.json).")
    p.add_argument("--out", default="gtd-sheet.pdf", help="Output PDF path.")
    p.add_argument("--date", default=None, help="Override sheet date (YYYY-MM-DD).")
    p.add_argument(
        "--html",
        default=None,
        help="Also dump per-bucket HTML (debug), e.g. debug.html.",
    )
    p.add_argument(
        "--manifest",
        default=None,
        help="Manifest output path (default: <out>.manifest.json).",
    )
    p.add_argument(
        "--no-manifest", action="store_true", help="Suppress manifest sidecar output."
    )
    args = p.parse_args(argv)

    data = json.loads(Path(args.tasks).read_text(encoding="utf-8"))

    if args.date:
        the_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    elif data.get("date"):
        the_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    else:
        the_date = date.today()

    out_path = Path(args.out)

    if args.no_manifest:
        manifest_path = None
    elif args.manifest:
        manifest_path = Path(args.manifest)
    else:
        manifest_path = out_path.with_suffix(".manifest.json")

    render_pdf(
        data,
        the_date,
        out_path,
        debug_html=Path(args.html) if args.html else None,
        manifest_path=manifest_path,
    )
    print(
        f"✓ wrote {args.out}  ({the_date.isoformat()}, {len(build_buckets(data))} pages)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
