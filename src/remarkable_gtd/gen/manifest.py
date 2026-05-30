"""Manifest export — collects data-roi regions and writes the sidecar JSON."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from remarkable_gtd.common.schema import MANIFEST_SCHEMA, PAGE_W_MM


# ---------------------------------------------------------------------------
# JavaScript: walk [data-roi] and return normalised rects relative to .page
# ---------------------------------------------------------------------------
_COLLECT_ROIS_JS = """
(() => {
    const page = document.querySelector('.page');
    if (!page) return {};
    const pageRect = page.getBoundingClientRect();
    const pw = pageRect.width;
    const ph = pageRect.height;
    const out = {};
    for (const el of document.querySelectorAll('[data-roi]')) {
        const key = el.getAttribute('data-roi');
        if (!key) continue;
        const r = el.getBoundingClientRect();
        out[key] = {
            x: (r.left - pageRect.left) / pw,
            y: (r.top - pageRect.top) / ph,
            w: r.width / pw,
            h: r.height / ph,
        };
    }
    return out;
})()
"""


def collect_rois(page) -> dict:
    """Calls page.evaluate(JS) to gather normalized rects for all [data-roi]."""
    return page.evaluate(_COLLECT_ROIS_JS)


def write_manifest(
    pages_info: list[dict],
    the_date: date,
    page_w_mm: float,
    out_path: Path,
) -> None:
    """Writes the manifest JSON (schema gtd.manifest/1).

    *pages_info* is a list of page dicts, one per page, in page order.
    Each dict must have keys:
        - ``bucket`` (str): bucket key, e.g. "inbox", "next", …
        - ``page_no`` (int): 1-based page number
        - ``w_px`` (int): rendered page width in pixels
        - ``h_px`` (int): rendered page height in pixels
        - ``rois`` (dict): maps ``data-roi`` value -> ``{"x": f, "y": f, "w": f, "h": f}``
    """
    pages = {}
    for info in pages_info:
        bucket = info["bucket"]
        page_no = info["page_no"]
        page_key = f"GTD|{bucket}|{the_date.isoformat()}"
        pages[page_key] = {
            "bucket": bucket,
            "page_no": page_no,
            "render": {
                "w_px": info["w_px"],
                "h_px": info["h_px"],
            },
            "rois": info["rois"],
        }

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "date": the_date.isoformat(),
        "page_w_mm": page_w_mm,
        "pages": pages,
    }

    out_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
