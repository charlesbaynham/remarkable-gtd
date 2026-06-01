#!/usr/bin/env python3
"""gtd-process CLI — annotation processing workflow: download, scan, apply, regenerate."""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

load_dotenv()
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from remarkable_gtd.rm.annotations import pdf_to_images
from remarkable_gtd.rm.api import download, find_latest_gtd
from remarkable_gtd.vault.applier import apply_decisions
from remarkable_gtd.vault.parser import build_tasks_json

from .daily import run_daily


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


def _git_commit_push(gtd_dir: Path) -> bool:
    """Stage all changes, commit, and push."""
    branch = os.environ.get("GTD_GIT_BRANCH", "master")

    # Check if there are changes to commit
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(gtd_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        print(f"Warning: git status failed: {status.stderr.strip()}", file=sys.stderr)
        return False

    if not status.stdout.strip():
        print("  (no changes to commit)")
        return True

    # Add all changes
    add_result = subprocess.run(
        ["git", "add", "."],
        cwd=str(gtd_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if add_result.returncode != 0:
        print(f"Warning: git add failed: {add_result.stderr.strip()}", file=sys.stderr)
        return False

    # Commit
    commit_result = subprocess.run(
        ["git", "commit", "-m", "GTD update from reMarkable"],
        cwd=str(gtd_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if commit_result.returncode != 0:
        print(
            f"Warning: git commit failed: {commit_result.stderr.strip()}",
            file=sys.stderr,
        )
        return False

    # Push
    push_result = subprocess.run(
        ["git", "push", "origin", branch],
        cwd=str(gtd_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if push_result.returncode != 0:
        print(
            f"Warning: git push failed: {push_result.stderr.strip()}", file=sys.stderr
        )
        return False

    print("  ✓ committed and pushed changes")
    return True


def _find_manifest_for_date(output_dir: Path, the_date: date) -> Path | None:
    """Find a manifest file in output_dir matching the given date."""
    date_prefix = the_date.strftime("%Y%m%d")
    for f in output_dir.iterdir():
        if f.is_file() and f.name.endswith(".manifest.json"):
            if f.name.startswith(date_prefix):
                return f
    return None


def run_process(
    gtd_dir: Path,
    remote_folder: str,
    output_dir: Path,
    the_date: date | None = None,
) -> int:
    """Execute the annotation processing workflow. Returns 0 on success."""
    if the_date is None:
        the_date = date.today()

    # 1. Git pull
    print(f"→ Pulling latest from GTD vault ({gtd_dir})...")
    _git_pull(gtd_dir)

    # 2. Find latest GTD file on reMarkable
    print(f"→ Finding latest GTD file in '{remote_folder}'...")
    remote_path = find_latest_gtd(remote_folder)
    if not remote_path:
        print(f"Error: No GTD file found in {remote_folder}", file=sys.stderr)
        return 1
    print(f"  ✓ found {remote_path}")

    # 3. Download rmdoc
    print("→ Downloading from reMarkable...")
    rmdoc_path = download(remote_path, output_dir)
    if not rmdoc_path:
        print("Error: Failed to download file from reMarkable", file=sys.stderr)
        return 1
    print(f"  ✓ downloaded to {rmdoc_path}")

    # 4. Annotated PDF already rendered server-side by rmapi geta --a
    annotated_pdf = rmdoc_path
    print(f"  ✓ annotated PDF ready: {annotated_pdf.name}")

    # 5. Extract page images
    images_dir = output_dir / "page_images"
    print("→ Extracting page images...")
    try:
        image_paths = pdf_to_images(annotated_pdf, images_dir)
    except Exception as e:
        print(f"Error: Failed to extract images: {e}", file=sys.stderr)
        return 1
    print(f"  ✓ extracted {len(image_paths)} pages")

    # 6. Find matching manifest
    manifest_path = _find_manifest_for_date(output_dir, the_date)
    if not manifest_path:
        # Try to find any manifest in output_dir
        manifests = sorted(
            f
            for f in output_dir.iterdir()
            if f.is_file() and f.name.endswith(".manifest.json")
        )
        if manifests:
            manifest_path = manifests[-1]
            print(f"  ⚠ using most recent manifest: {manifest_path.name}")
        else:
            print(
                "Error: No manifest file found. Run gtd-daily first.", file=sys.stderr
            )
            return 1
    else:
        print(f"  ✓ manifest: {manifest_path.name}")

    # 7. Scan each page image
    decisions_path = output_dir / f"{the_date.isoformat()}_decisions.json"
    print("→ Scanning pages for decisions...")

    # Use gtd-scan-pdf CLI for multi-page PDF scanning
    scan_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "remarkable_gtd.cli.scan_pdf",
            str(annotated_pdf),
            "--manifest",
            str(manifest_path),
            "--ocr",
            "tesseract",
            "-o",
            str(decisions_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if scan_result.returncode != 0:
        print(f"Error: Scan failed:\n{scan_result.stderr}", file=sys.stderr)
        return 1
    print(f"  ✓ wrote {decisions_path}")

    # 8. Apply decisions
    print("→ Applying decisions to vault...")
    # Derive tasks.json path from manifest: "{ts}_gtd_sheet.manifest.json" -> "{ts}_tasks.json"
    tasks_json_path = None
    manifest_for_tasks = _find_manifest_for_date(output_dir, the_date)
    if manifest_for_tasks:
        import re as _re

        m = _re.match(r"(\d{8}Z\d{4})_", manifest_for_tasks.name)
        if m:
            tasks_json_path = output_dir / f"{m.group(1)}_tasks.json"
    if not tasks_json_path or not tasks_json_path.exists():
        # Re-parse to get current tasks.json
        tasks = build_tasks_json(gtd_dir, the_date)
        tasks_json_path = output_dir / f"{the_date.isoformat()}_tasks.json"
        tasks_json_path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")

    try:
        results = apply_decisions(decisions_path, tasks_json_path, gtd_dir)
    except Exception as e:
        print(f"Error: Failed to apply decisions: {e}", file=sys.stderr)
        return 1

    for result in results:
        print(f"    {result}")
    print(f"  ✓ applied {len(results)} changes")

    # 9. Git commit and push
    print("→ Committing changes...")
    _git_commit_push(gtd_dir)

    # 10. Regenerate PDF and upload (daily workflow)
    print("\n→ Regenerating daily sheet...")
    return run_daily(
        gtd_dir=gtd_dir,
        remote_folder=remote_folder,
        output_dir=output_dir,
        the_date=the_date,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Process annotated GTD sheet: download, scan, apply decisions, regenerate."
    )
    p.add_argument(
        "--gtd-dir",
        default=os.environ.get("GTD_DIR", str(Path.home() / "gtd")),
        help="Path to GTD vault (default: ~/gtd or $GTD_DIR).",
    )
    p.add_argument(
        "--remarkable-folder",
        default="GTD Daily",
        help="reMarkable folder to scan (default: 'GTD Daily').",
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

    return run_process(
        gtd_dir=gtd_dir,
        remote_folder=args.remarkable_folder,
        output_dir=Path(args.output_dir),
        the_date=the_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())
