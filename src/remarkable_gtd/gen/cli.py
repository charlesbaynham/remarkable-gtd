"""gtd-gen — CLI entry point for the GTD PDF generator."""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path


def main(argv=None) -> int:
    """Generate the GTD reMarkable PDF (one tall page per bucket)."""
    p = argparse.ArgumentParser(
        description="Generate the GTD reMarkable PDF (one tall page per bucket)."
    )
    p.add_argument("tasks", help="Path to tasks JSON (see tasks.example.json).")
    p.add_argument("--out", default="gtd-sheet.pdf", help="Output PDF path.")
    p.add_argument(
        "--date", default=None, help="Override sheet date (YYYY-MM-DD)."
    )
    p.add_argument(
        "--html",
        default=None,
        help="Also dump per-bucket HTML (debug), e.g. debug.html.",
    )
    p.add_argument(
        "--manifest",
        default=None,
        metavar="PATH",
        help="Path for the layout manifest JSON sidecar (default: <out>.manifest.json).",
    )
    p.add_argument(
        "--no-manifest",
        action="store_true",
        default=False,
        help="Suppress manifest sidecar output.",
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

    # Resolve manifest_path
    if args.no_manifest:
        manifest_path = None
    elif args.manifest:
        manifest_path = Path(args.manifest)
    else:
        manifest_path = ...  # use default (sentinel) in render_pdf

    from remarkable_gtd.gen.generate import render_pdf, build_buckets

    render_pdf(
        data,
        the_date,
        out_path,
        Path(args.html) if args.html else None,
        manifest_path=manifest_path,
    )

    n_pages = len(build_buckets(data))
    manifest_note = ""
    if not args.no_manifest:
        mp = Path(args.manifest) if args.manifest else out_path.with_suffix(".manifest.json")
        manifest_note = f"  manifest -> {mp}"
    print(f"wrote {out_path}  ({the_date.isoformat()}, {n_pages} pages){manifest_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
