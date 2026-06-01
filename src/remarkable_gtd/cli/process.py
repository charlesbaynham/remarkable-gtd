#!/usr/bin/env python3
"""gtd-process CLI — annotation processing workflow: download, scan, apply, regenerate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from remarkable_gtd.rm.annotations import pdf_to_images
from remarkable_gtd.rm.api import download, find_latest_gtd
from remarkable_gtd.vault.applier import apply_decisions
from remarkable_gtd.vault.parser import build_tasks_json

from ._git import commit_and_push, ensure_vault
from .daily import run_daily


def _find_manifest_for_date(output_dir: Path, the_date: date) -> Path | None:
    """Find a manifest file in output_dir matching the given date."""
    date_prefix = the_date.strftime("%Y%m%d")
    for f in output_dir.iterdir():
        if f.is_file() and f.name.endswith(".manifest.json"):
            if f.name.startswith(date_prefix):
                return f
    return None


def run_process(
    gtd_vault: str,
    remote_folder: str,
    output_dir: Path,
    the_date: date | None = None,
) -> int:
    """Execute the annotation processing workflow. Returns 0 on success.

    ``gtd_vault`` may be a local path or a remote git URL.  When it is a URL
    the repo is cloned/reset inside ``output_dir/vault`` and treated as
    disposable; after decisions are applied the changes are committed and
    pushed back to the remote.
    """
    if the_date is None:
        the_date = date.today()

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve vault (clone/reset if URL, validate if local path)
    print(f"→ Syncing GTD vault ({gtd_vault})...")
    gtd_dir = ensure_vault(gtd_vault, output_dir)

    # 2. Find latest GTD file on reMarkable
    print(f"→ Finding latest GTD file in '{remote_folder}'...")
    remote_path = find_latest_gtd(remote_folder)
    if not remote_path:
        print(f"Error: No GTD file found in {remote_folder}", file=sys.stderr)
        return 1
    print(f"  ✓ found {remote_path}")

    # 3. Download annotated PDF
    print("→ Downloading from reMarkable...")
    rmdoc_path = download(remote_path, output_dir)
    if not rmdoc_path:
        print("Error: Failed to download file from reMarkable", file=sys.stderr)
        return 1
    print(f"  ✓ downloaded to {rmdoc_path}")
    annotated_pdf = rmdoc_path

    # 4. Extract page images
    images_dir = output_dir / "page_images"
    print("→ Extracting page images...")
    try:
        image_paths = pdf_to_images(annotated_pdf, images_dir)
    except Exception as e:
        print(f"Error: Failed to extract images: {e}", file=sys.stderr)
        return 1
    print(f"  ✓ extracted {len(image_paths)} pages")

    # 5. Find matching manifest
    manifest_path = _find_manifest_for_date(output_dir, the_date)
    if not manifest_path:
        manifests = sorted(
            f
            for f in output_dir.iterdir()
            if f.is_file() and f.name.endswith(".manifest.json")
        )
        if manifests:
            manifest_path = manifests[-1]
            print(f"  ⚠ using most recent manifest: {manifest_path.name}")
        else:
            print("Error: No manifest file found. Run gtd-daily first.", file=sys.stderr)
            return 1
    else:
        print(f"  ✓ manifest: {manifest_path.name}")

    # 6. Scan pages for decisions
    decisions_path = output_dir / f"{the_date.isoformat()}_decisions.json"
    print("→ Scanning pages for decisions...")
    scan_result = subprocess.run(
        [
            sys.executable, "-m", "remarkable_gtd.cli.scan_pdf",
            str(annotated_pdf),
            "--manifest", str(manifest_path),
            "--ocr", "tesseract",
            "-o", str(decisions_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if scan_result.returncode != 0:
        print(f"Error: Scan failed:\n{scan_result.stderr}", file=sys.stderr)
        return 1
    print(f"  ✓ wrote {decisions_path}")

    # 7. Resolve tasks.json for this date (from the matching manifest timestamp)
    print("→ Applying decisions to vault...")
    tasks_json_path = None
    if manifest_path:
        m = re.match(r"(\d{8}Z\d{4})_", manifest_path.name)
        if m:
            tasks_json_path = output_dir / f"{m.group(1)}_tasks.json"
    if not tasks_json_path or not tasks_json_path.exists():
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

    # 8. Commit and push vault changes back to remote
    print("→ Committing changes...")
    commit_and_push(gtd_dir)

    # 9. Regenerate PDF and upload
    print("\n→ Regenerating daily sheet...")
    return run_daily(
        gtd_vault=gtd_vault,
        remote_folder=remote_folder,
        output_dir=output_dir,
        the_date=the_date,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Process annotated GTD sheet: download, scan, apply decisions, regenerate."
    )
    vault_grp = p.add_mutually_exclusive_group()
    vault_grp.add_argument(
        "--gtd-url",
        default=os.environ.get("GTD_URL"),
        help="Remote git URL for the GTD vault (cloned fresh into output-dir/vault).",
    )
    vault_grp.add_argument(
        "--gtd-dir",
        default=os.environ.get("GTD_DIR"),
        help="Local path to GTD vault (used as-is, no git operations).",
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

    gtd_vault = args.gtd_url or args.gtd_dir
    if not gtd_vault:
        gtd_vault = str(Path.home() / "gtd")

    if "://" not in gtd_vault and not gtd_vault.startswith("git@"):
        if not Path(gtd_vault).exists():
            print(f"Error: GTD directory not found: {gtd_vault}", file=sys.stderr)
            return 1

    if args.date:
        the_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        the_date = date.today()

    return run_process(
        gtd_vault=gtd_vault,
        remote_folder=args.remarkable_folder,
        output_dir=Path(args.output_dir),
        the_date=the_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())
