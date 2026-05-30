#!/usr/bin/env python3
"""gtd-scan-pdf CLI — scan a multi-page annotated PDF against a manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from remarkable_gtd.scan.manifest_io import list_page_keys, load_manifest
from remarkable_gtd.scan.pipeline import ScanConfig, run_scan


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Scan a multi-page annotated PDF and produce decisions JSON."
    )
    p.add_argument("pdf", help="Path to annotated PDF.")
    p.add_argument("--manifest", required=True, help="Path to manifest JSON.")
    p.add_argument(
        "--ocr", default="null", choices=["null", "tesseract"], help="OCR engine."
    )
    p.add_argument(
        "-o", "--output", default="decisions.json", help="Output decisions JSON path."
    )
    p.add_argument("--dpi", type=int, default=226, help="DPI for page rasterization.")
    p.add_argument("--scale", type=int, default=2, help="Scale factor for page images.")
    args = p.parse_args(argv)

    manifest = load_manifest(args.manifest)
    keys = list_page_keys(manifest)

    if not keys:
        print("Error: manifest has no pages", file=__import__("sys").stderr)
        return 1

    # Import PyMuPDF here to avoid hard dependency for single-image scan
    try:
        import fitz
    except ImportError:
        print(
            "Error: PyMuPDF required for PDF scanning. pip install PyMuPDF",
            file=__import__("sys").stderr,
        )
        return 1

    pdf_path = Path(args.pdf)
    doc = fitz.open(str(pdf_path))

    if len(doc) != len(keys):
        print(
            f"Warning: PDF has {len(doc)} pages but manifest has {len(keys)} pages.",
            file=__import__("sys").stderr,
        )

    cfg = ScanConfig(ocr_engine=args.ocr)
    page_results = []

    tmp_dir = Path("/tmp/gtd_scan_pdf")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for i, page in enumerate(doc):
        if i >= len(keys):
            break

        page_key = keys[i]
        mat = fitz.Matrix(args.dpi / 72 * args.scale, args.dpi / 72 * args.scale)
        pix = page.get_pixmap(matrix=mat)
        img_path = tmp_dir / f"page_{i + 1}.png"
        pix.save(str(img_path))

        try:
            decisions = run_scan(img_path, manifest, cfg, page_key)
            page_results.append(
                {
                    "page_key": page_key,
                    "page_no": i + 1,
                    **decisions,
                }
            )
            print(
                f"  ✓ page {i + 1}: {page_key} — {len(decisions.get('tasks', []))} tasks"
            )
        except Exception as e:
            print(f"  ✗ page {i + 1}: {page_key} — {e}", file=__import__("sys").stderr)
            page_results.append(
                {
                    "page_key": page_key,
                    "page_no": i + 1,
                    "error": str(e),
                }
            )

    doc.close()

    # Build combined output
    combined = {
        "schema": "gtd.decisions/1",
        "source_pdf": str(pdf_path),
        "manifest": args.manifest,
        "pages": page_results,
    }

    out_path = Path(args.output)
    out_path.write_text(json.dumps(combined, indent=2), encoding="utf-8")
    print(f"✓ wrote {out_path} ({len(page_results)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
