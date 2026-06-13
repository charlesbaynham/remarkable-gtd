"""gtd-scan — CLI entry point for the sheet scanner."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    """Scan a written-on GTD sheet image into a decisions JSON."""
    p = argparse.ArgumentParser(
        description="Read handwritten marks from a GTD sheet image "
        "and emit a structured decisions JSON."
    )
    p.add_argument("image", help="Scan image (PNG/JPG, or PDF first page).")
    p.add_argument(
        "--manifest", required=True, metavar="PATH",
        help="Layout manifest JSON written by gtd-gen alongside the PDF.",
    )
    p.add_argument(
        "--page", default=None, metavar="KEY",
        help="Manifest page key, e.g. 'GTD|next|2026-05-30'. Auto-detected "
        "from QR codes if omitted (or if the manifest has one page).",
    )
    p.add_argument(
        "--ocr", default="null", choices=["null", "tesseract"],
        help="OCR engine for handwriting regions (default: null = flag only).",
    )
    p.add_argument(
        "-o", "--out", default=None, metavar="PATH",
        help="Output decisions JSON path (default: stdout).",
    )
    args = p.parse_args(argv)

    from remarkable_gtd.scan.manifest_io import load_manifest
    from remarkable_gtd.scan.pipeline import ScanConfig, run_scan

    manifest = load_manifest(args.manifest)
    manifest["_path"] = str(args.manifest)

    cfg = ScanConfig(ocr_engine=args.ocr)
    decisions = run_scan(Path(args.image), manifest, cfg, page_key=args.page)

    out_json = json.dumps(decisions, indent=2)
    if args.out:
        Path(args.out).write_text(out_json + "\n", encoding="utf-8")
        n = len(decisions["tasks"])
        acted = sum(1 for t in decisions["tasks"] if t["action"] != "none")
        print(
            f"wrote {args.out}  ({decisions['bucket']}, {n} tasks, "
            f"{acted} with actions, {len(decisions['warnings'])} warnings)"
        )
    else:
        sys.stdout.write(out_json + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
