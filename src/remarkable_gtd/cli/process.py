#!/usr/bin/env python3
"""gtd-process CLI — full sync: download annotations, apply, regenerate, upload."""

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

from remarkable_gtd.gen.generate import render_pdf
from remarkable_gtd.rm.api import download, find_latest_gtd, mkdir, upload
from remarkable_gtd.vault.applier import apply_decisions
from remarkable_gtd.vault.parser import build_tasks_json

from ._git import commit_and_push, ensure_vault
from .daily import _timestamp


def _find_manifest_for_ts(output_dir: Path, ts: str) -> Path | None:
    """Find a manifest whose filename starts with the given timestamp prefix."""
    for f in output_dir.iterdir():
        if f.is_file() and f.name.endswith(".manifest.json") and f.name.startswith(ts):
            return f
    return None


def _find_any_manifest(output_dir: Path) -> Path | None:
    """Return the most recently created manifest in output_dir, or None."""
    candidates = sorted(
        (
            f
            for f in output_dir.iterdir()
            if f.is_file() and f.name.endswith(".manifest.json")
        ),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def run_process(
    gtd_vault: str,
    remote_folder: str,
    output_dir: Path,
    the_date: date | None = None,
) -> int:
    """Full sync cycle. Returns 0 on success.

    1. Sync vault (clone/reset if URL).
    2. Try to download the latest annotated PDF from the reMarkable.
       If nothing is there yet, skip straight to step 6.
    3. Scan the annotated PDF against the matching manifest.
    4. Apply decisions to the vault and push.
    5. (fall-through)
    6. Parse the updated vault, generate a new PDF + manifest, upload.
    """
    if the_date is None:
        the_date = date.today()

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Sync vault ──────────────────────────────────────────────────────────
    print(f"→ Syncing GTD vault ({gtd_vault})...")
    gtd_dir = ensure_vault(gtd_vault, output_dir)

    # ── 2. Try to download annotated PDF ──────────────────────────────────────
    annotated_pdf = None
    remote_ts = None  # timestamp prefix extracted from the remote filename

    print(f"→ Looking for annotated PDF in '{remote_folder}'...")
    remote_path = find_latest_gtd(remote_folder)

    if not remote_path:
        print("  (no file found — skipping scan, generating fresh sheet)")
    else:
        print(f"  ✓ found {remote_path}")
        # Extract timestamp from remote filename: "GTD Daily/20260601Z0700_gtd_sheet" → "20260601Z0700"
        m = re.search(r"(\d{8}Z\d{4})_", Path(remote_path).name)
        remote_ts = m.group(1) if m else None

        print("→ Downloading annotated PDF...")
        rmdoc_path = download(remote_path, output_dir)
        if not rmdoc_path:
            print(
                "  ⚠ download failed — skipping scan, generating fresh sheet",
                file=sys.stderr,
            )
        else:
            print(f"  ✓ downloaded {rmdoc_path.name}")
            annotated_pdf = rmdoc_path

    # ── 3. Scan ───────────────────────────────────────────────────────────────
    if annotated_pdf:
        # Find the manifest that was generated alongside this specific PDF
        manifest_path = None
        if remote_ts:
            manifest_path = _find_manifest_for_ts(output_dir, remote_ts)
        if not manifest_path:
            manifest_path = _find_any_manifest(output_dir)
            if manifest_path:
                print(f"  ⚠ exact manifest not found, using {manifest_path.name}")

        if not manifest_path:
            print(
                "  ⚠ no manifest found — cannot scan; run will still regenerate the sheet",
                file=sys.stderr,
            )
            annotated_pdf = None  # skip apply step

    if annotated_pdf and manifest_path:
        decisions_path = output_dir / f"{the_date.isoformat()}_decisions.json"
        print("→ Scanning pages for decisions...")
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
            print(f"  ⚠ scan failed:\n{scan_result.stderr.strip()}", file=sys.stderr)
            annotated_pdf = None  # skip apply step
        else:
            print(f"  ✓ wrote {decisions_path.name}")

            # ── 4. Apply decisions and push vault ─────────────────────────────
            print("→ Applying decisions to vault...")
            # Find tasks.json matching the manifest timestamp
            tasks_json_path = None
            if remote_ts:
                candidate = output_dir / f"{remote_ts}_tasks.json"
                if candidate.exists():
                    tasks_json_path = candidate
            if not tasks_json_path:
                # Re-parse from vault as fallback
                tasks = build_tasks_json(gtd_dir, the_date)
                tasks_json_path = output_dir / f"{the_date.isoformat()}_tasks.json"
                tasks_json_path.write_text(
                    json.dumps(tasks, indent=2), encoding="utf-8"
                )

            try:
                results = apply_decisions(decisions_path, tasks_json_path, gtd_dir)
                for r in results:
                    print(f"    {r}")
                print(f"  ✓ applied {len(results)} changes")
            except Exception as e:
                print(f"  ⚠ apply failed: {e}", file=sys.stderr)

            print("→ Committing changes to vault...")
            commit_and_push(gtd_dir)

    # ── 5. Parse updated vault, generate and upload new PDF ───────────────────
    print("→ Parsing vault...")
    tasks = build_tasks_json(gtd_dir, the_date)

    ts = _timestamp()
    pdf_path = output_dir / f"{ts}_gtd_sheet.pdf"
    manifest_path_new = output_dir / f"{ts}_gtd_sheet.manifest.json"

    print(f"→ Generating PDF ({pdf_path.name})...")
    render_pdf(tasks, the_date, pdf_path, manifest_path=manifest_path_new)
    print(f"  ✓ wrote {pdf_path.name}")

    tasks_path = output_dir / f"{ts}_tasks.json"
    tasks_path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    print(f"  ✓ wrote {tasks_path.name}")

    print(f"→ Uploading to reMarkable folder '{remote_folder}'...")
    mkdir(remote_folder)
    if not upload(pdf_path, remote_folder):
        print(f"Error: Failed to upload PDF to {remote_folder}", file=sys.stderr)
        return 1
    print(f"  ✓ uploaded {pdf_path.name}")

    counts = {
        "inbox": len(tasks.get("inbox", [])),
        "next": len(tasks.get("next", [])),
        "delegated": len(tasks.get("delegated", [])),
        "tickler": sum(len(v) for v in tasks.get("tickler", {}).values()),
    }
    print(f"\n✓ GTD sync complete — {the_date.isoformat()}")
    print(
        f"  Tasks: {counts['inbox']} inbox, {counts['next']} next, "
        f"{counts['delegated']} delegated, {counts['tickler']} tickler"
    )
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Full GTD sync: download annotated PDF (if any), apply decisions, "
            "regenerate sheet, upload. Safe to run when nothing is on the reMarkable yet."
        )
    )
    vault_grp = p.add_mutually_exclusive_group()
    vault_grp.add_argument(
        "--gtd-url",
        default=os.environ.get("GTD_URL"),
        help="Remote git URL for the GTD vault (cloned/reset into output-dir/vault).",
    )
    vault_grp.add_argument(
        "--gtd-dir",
        default=os.environ.get("GTD_DIR"),
        help="Local path to GTD vault (used as-is).",
    )
    p.add_argument(
        "--remarkable-folder",
        default=os.environ.get("REMARKABLE_FOLDER", "GTD Daily"),
        help="reMarkable folder (default: 'GTD Daily').",
    )
    p.add_argument(
        "--output-dir",
        default=os.environ.get(
            "OUTPUT_DIR", str(Path.home() / ".local" / "share" / "gtd")
        ),
        help="Directory for generated files.",
    )
    p.add_argument("--date", default=None, help="Override date (YYYY-MM-DD).")
    args = p.parse_args(argv)

    gtd_vault = args.gtd_url or args.gtd_dir
    if not gtd_vault:
        gtd_vault = str(Path.home() / "gtd")

    if "://" not in gtd_vault and not gtd_vault.startswith("git@"):
        if not Path(gtd_vault).exists():
            print(f"Error: GTD directory not found: {gtd_vault}", file=sys.stderr)
            return 1

    the_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    )

    return run_process(
        gtd_vault=gtd_vault,
        remote_folder=args.remarkable_folder,
        output_dir=Path(args.output_dir),
        the_date=the_date,
    )


if __name__ == "__main__":
    raise SystemExit(main())
