#!/usr/bin/env python3
"""gtd-daily CLI — morning workflow: parse vault, generate PDF, upload to reMarkable."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

load_dotenv()
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from remarkable_gtd.gen.generate import render_pdf
from remarkable_gtd.rm.api import mkdir, upload
from remarkable_gtd.vault.parser import build_tasks_json


def _timestamp() -> str:
    """Return UTC timestamp in YYYYMMDDZHHMM format."""
    return datetime.now(timezone.utc).strftime("%Y%m%dZ%H%M")


def _git_pull(gtd_dir: Path) -> bool:
    """Run git pull in the GTD directory."""
    branch = os.environ.get("GTD_GIT_BRANCH", "master")
    result = subprocess.run(
        ["git", "pull", "origin", branch],
        cwd=str(gtd_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"Warning: git pull failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def run_daily(
    gtd_dir: Path,
    remote_folder: str,
    output_dir: Path,
    the_date: date | None = None,
) -> int:
    """Execute the daily workflow. Returns 0 on success."""
    if the_date is None:
        the_date = date.today()

    # 1. Git pull
    print(f"→ Pulling latest from GTD vault ({gtd_dir})...")
    _git_pull(gtd_dir)

    # 2. Parse vault
    print("→ Parsing vault...")
    tasks = build_tasks_json(gtd_dir, the_date)

    # 3. Write tasks.json
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = _timestamp()
    tasks_path = output_dir / f"{ts}_tasks.json"
    tasks_path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    print(f"  ✓ wrote {tasks_path}")

    # 4. Generate PDF
    pdf_path = output_dir / f"{ts}_gtd_sheet.pdf"
    manifest_path = output_dir / f"{ts}_gtd_sheet.manifest.json"
    print(f"→ Generating PDF ({pdf_path.name})...")
    render_pdf(tasks, the_date, pdf_path, manifest_path=manifest_path)
    print(f"  ✓ wrote {pdf_path}")
    print(f"  ✓ wrote {manifest_path}")

    # 5. Upload PDF to reMarkable
    print(f"→ Uploading to reMarkable folder '{remote_folder}'...")
    mkdir(remote_folder)
    if not upload(pdf_path, remote_folder):
        print(f"Error: Failed to upload PDF to {remote_folder}", file=sys.stderr)
        return 1
    print(f"  ✓ uploaded {pdf_path.name}")

    # 6. Upload manifest alongside
    if not upload(manifest_path, remote_folder):
        print(f"Warning: Failed to upload manifest to {remote_folder}", file=sys.stderr)
    else:
        print(f"  ✓ uploaded {manifest_path.name}")

    # 7. Summary
    counts = {
        "inbox": len(tasks.get("inbox", [])),
        "next": len(tasks.get("next", [])),
        "delegated": len(tasks.get("delegated", [])),
        "tickler": sum(len(v) for v in tasks.get("tickler", {}).values()),
    }
    print(f"\n✓ Daily GTD ready — {the_date.isoformat()}")
    print(
        f"  Tasks: {counts['inbox']} inbox, {counts['next']} next, "
        f"{counts['delegated']} delegated, {counts['tickler']} tickler"
    )
    print(f"  PDF: {pdf_path}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Morning GTD workflow: parse vault, generate PDF, upload to reMarkable."
    )
    p.add_argument(
        "--gtd-dir",
        default=os.environ.get("GTD_DIR", str(Path.home() / "gtd")),
        help="Path to GTD vault (default: ~/gtd or $GTD_DIR).",
    )
    p.add_argument(
        "--remarkable-folder",
        default="GTD Daily",
        help="reMarkable folder to upload to (default: 'GTD Daily').",
    )
    p.add_argument(
        "--output-dir",
        default=str(Path.home() / ".local" / "share" / "gtd"),
        help="Directory for generated files (default: ~/.local/share/gtd).",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Override date (YYYY-MM-DD).",
    )
    args = p.parse_args(argv)

    gtd_dir = Path(args.gtd_dir)
    if not gtd_dir.exists():
        print(f"Error: GTD directory not found: {gtd_dir}", file=sys.stderr)
        return 1

    if args.date:
        the_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        the_date = date.today()

    return run_daily(
        gtd_dir=gtd_dir,
        remote_folder=args.remarkable_folder,
        output_dir=Path(args.output_dir),
        the_date=the_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())
