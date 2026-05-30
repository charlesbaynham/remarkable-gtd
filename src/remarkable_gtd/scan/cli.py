#!/usr/bin/env python3
"""gtd-scan CLI entry point."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest_io import list_page_keys, load_manifest
from .pipeline import ScanConfig, run_scan


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Scan a marked GTD sheet and produce decisions JSON."
    )
    p.add_argument("image", help="Path to scanned/photographed sheet image.")
    p.add_argument("--manifest", required=True, help="Path to manifest JSON.")
    p.add_argument(
        "--page", default=None, help="Page key from manifest (auto if only one page)."
    )
    p.add_argument(
        "--ocr", default="null", choices=["null", "tesseract"], help="OCR engine."
    )
    p.add_argument(
        "-o", "--output", default="decisions.json", help="Output decisions JSON path."
    )
    args = p.parse_args(argv)

    manifest = load_manifest(args.manifest)

    page_key = args.page
    if page_key is None:
        keys = list_page_keys(manifest)
        if len(keys) == 1:
            page_key = keys[0]
        else:
            p.error("Manifest has multiple pages; specify --page")

    cfg = ScanConfig(ocr_engine=args.ocr)
    decisions = run_scan(Path(args.image), manifest, cfg, page_key)

    out_path = Path(args.output)
    out_path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    print(f"✓ wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
