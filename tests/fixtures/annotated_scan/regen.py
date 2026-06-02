#!/usr/bin/env python3
"""Regenerate the annotated_scan PNG fixtures from the stored rmdoc.

Usage:
    uv run python tests/fixtures/annotated_scan/regen.py

Reads:
  tests/fixtures/annotated_scan/gtd_sheet.rmdoc  — annotated reMarkable doc
  tests/fixtures/annotated_scan/sheet.manifest.json — ROI manifest

Writes:
  tests/fixtures/annotated_scan/page_00.png ... page_03.png

The reMarkable v6 stroke format stores x-coordinates centred at x=702
(half the 1404px screen width). This script shifts x by +702 when drawing
onto the 1404px-wide PDF raster so strokes land on the correct checkboxes.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "src"))

import io
import json
import zipfile

import fitz  # PyMuPDF
from rmscene import SceneLineItemBlock, read_blocks
from rmscene.scene_items import PenColor

HERE = Path(__file__).parent
RMDOC = HERE / "gtd_sheet.rmdoc"
MANIFEST = HERE / "sheet.manifest.json"

# reMarkable 2: 1404px wide screen; x is centred at 702.
RM_HALF_W = 702.0
# PDF points per pixel at 226 dpi (reMarkable native).
PT_PER_PX = 72.0 / 226.0


def _page_uuid_to_pdf_index(content: dict) -> dict[str, int]:
    pages = content.get("cPages", {}).get("pages", [])
    return {p["id"]: p.get("redir", {}).get("value", i) for i, p in enumerate(pages)}


def render_onto_pdf(rmdoc: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(rmdoc) as z:
        names = z.namelist()
        content_name = next(n for n in names if n.endswith(".content"))
        pdf_name = next(n for n in names if n.endswith(".pdf"))
        content = json.loads(z.read(content_name))
        uuid_to_pdf = _page_uuid_to_pdf_index(content)

        pdf_bytes = z.read(pdf_name)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        rm_files = [n for n in names if n.endswith(".rm")]
        for rm_path in rm_files:
            uuid = Path(rm_path).stem
            page_idx = uuid_to_pdf.get(uuid)
            if page_idx is None or page_idx >= len(doc):
                continue

            page = doc[page_idx]
            shape = page.new_shape()
            blocks = list(read_blocks(io.BytesIO(z.read(rm_path))))

            stroke_count = 0
            for block in blocks:
                if not isinstance(block, SceneLineItemBlock):
                    continue
                line = block.item.value
                if line is None or len(line.points) < 2:
                    continue

                pts = [
                    ((p.x + RM_HALF_W) * PT_PER_PX, p.y * PT_PER_PX)
                    for p in line.points
                ]
                color = (1, 1, 1) if line.color == PenColor.WHITE else (0, 0, 0)
                avg_w = sum(p.width for p in line.points) / len(line.points)
                width = max(avg_w * PT_PER_PX * 0.3, 0.5)

                shape.draw_polyline(pts)
                shape.finish(color=color, width=width)
                stroke_count += 1

            shape.commit()
            print(f"  page {page_idx}: {stroke_count} strokes")

        # Rasterise each page to PNG at 226dpi (1404px wide)
        mat = fitz.Matrix(226 / 72, 226 / 72)
        for i in range(len(doc)):
            pix = doc[i].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            out_path = output_dir / f"page_{i:02d}.png"
            pix.save(str(out_path))
            print(f"  saved {out_path} ({out_path.stat().st_size // 1024}KB)")

        doc.close()


if __name__ == "__main__":
    print(f"Rendering {RMDOC.name} → {HERE}/page_*.png")
    render_onto_pdf(RMDOC, HERE)
    print("Done.")
