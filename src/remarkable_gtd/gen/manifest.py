"""Layout manifest: collect data-roi bounding rects from Chromium page and write JSON sidecar."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from remarkable_gtd.common.schema import MANIFEST_SCHEMA


_COLLECT_JS = """
() => {
    const page = document.querySelector('.page');
    if (!page) return {};
    const pr = page.getBoundingClientRect();
    const result = {};
    document.querySelectorAll('[data-roi]').forEach(el => {
        const key = el.getAttribute('data-roi');
        const r = el.getBoundingClientRect();
        result[key] = {
            x: (r.left - pr.left) / pr.width,
            y: (r.top  - pr.top)  / pr.height,
            w: r.width  / pr.width,
            h: r.height / pr.height,
        };
    });
    return result;
}
"""


def collect_rois(page) -> dict:
    """Evaluate JS in a live Playwright page to gather normalized ROI rects.

    Each rect is expressed as fractions relative to the ``.page`` element
    (values in 0..1 for elements fully inside the page).

    Args:
        page: A Playwright ``Page`` object with the bucket HTML loaded.

    Returns:
        dict mapping ``data-roi`` key strings to ``{x, y, w, h}`` dicts.
    """
    return page.evaluate(_COLLECT_JS)


def write_manifest(
    buckets_rois: list[dict],
    the_date: date,
    page_w_mm: float,
    out_path: Path,
) -> None:
    """Write the manifest JSON sidecar file.

    Args:
        buckets_rois: List of per-bucket dicts with keys:
            ``key``, ``bucket``, ``page_no``, ``render`` (w_px/h_px), ``rois``.
        the_date: The sheet date (used as the top-level ``date`` field).
        page_w_mm: Physical page width in mm (157.8 for reMarkable 2).
        out_path: Destination path for the JSON file.
    """
    pages: dict = {}
    for entry in buckets_rois:
        pages[entry["key"]] = {
            "bucket": entry["bucket"],
            "page_no": entry["page_no"],
            "render": entry["render"],
            "rois": entry["rois"],
        }

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "date": the_date.strftime("%Y-%m-%d"),
        "page_w_mm": page_w_mm,
        "pages": pages,
    }

    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
