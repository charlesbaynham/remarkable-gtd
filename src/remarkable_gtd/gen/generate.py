#!/usr/bin/env python3
"""
GTD reMarkable Sheet — PDF generator
====================================
Builds a 4-page PDF (Inbox / Next Actions / Delegated / Tickler) for the
reMarkable 2. Each bucket is ONE page, 157.8 mm wide (the device panel
width) and exactly as TALL as its content needs — no truncation, no blank
tails. Every task carries a stable ID + QR fiducial, and a fixed labelled
gutter, so the nightly vision agent reads your handwritten marks reliably.

    python -m remarkable_gtd.gen.generate tasks.example.json --out today.pdf

WHY CHROMIUM (Playwright) AND NOT WEASYPRINT?
A PDF page cannot auto-fit its height to content in WeasyPrint — its page
box is fixed. We need each section's page to be exactly as long as the list,
so we render the *approved* HTML/CSS in headless Chromium, measure each
section's pixel height, and emit a page cut to fit. Bonus: the PDF is a
pixel-for-pixel match of ../GTD Sheet.html (same gtd.css).
"""

from __future__ import annotations

import argparse
import base64
import importlib.resources
import io
import json
from datetime import date, datetime
from pathlib import Path

PX_PER_MM = 96.0 / 25.4  # CSS px per mm at 96 dpi (Chromium print unit)
PAGE_W_MM = 157.8  # reMarkable 2 panel width
HEIGHT_PAD_MM = 0.6  # guard against rounding overflow to a 2nd page


def _asset_text(name: str) -> str:
    """Load asset text from the package using importlib.resources."""
    files = importlib.resources.files("remarkable_gtd.gen.assets")
    return files.joinpath(name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# QR fiducials  ->  PNG data-URI
# --------------------------------------------------------------------------
def qr_datauri(text: str) -> str:
    import qrcode

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=2,
        border=0,
    )
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("1")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------------
# Data shaping
# --------------------------------------------------------------------------
def _with_ids(items, prefix, start=1):
    out = []
    for i, t in enumerate(items, start):
        t = dict(t)
        t.setdefault("id", f"{prefix}-{i:02d}")
        out.append(t)
    return out, start + len(items)


def build_buckets(data: dict) -> list[dict]:
    inbox, _ = _with_ids(data.get("inbox", []), "IN")
    nxt, _ = _with_ids(data.get("next", []), "NA")
    deleg, _ = _with_ids(data.get("delegated", []), "DG")

    tick = data.get("tickler", {}) or {}
    week, n = _with_ids(tick.get("week", []), "TK", 1)
    month, n = _with_ids(tick.get("month", []), "TK", n)
    quarter, n = _with_ids(tick.get("quarter", []), "TK", n)
    tick_total = len(week) + len(month) + len(quarter)

    buckets = [
        {
            "key": "inbox",
            "tag": "0",
            "title": "Inbox",
            "sub": "Unprocessed capture — route every item out today",
            "count_label": f"{len(inbox)} to process",
            "kind": "flat",
            "items": inbox,
            "capture": 6,
        },
        {
            "key": "next",
            "tag": "1",
            "title": "Next Actions",
            "sub": "On your plate — do, delegate, or defer",
            "count_label": f"{len(nxt)} actions",
            "kind": "flat",
            "items": nxt,
            "capture": 0,
        },
        {
            "key": "delegated",
            "tag": "2",
            "title": "Delegated",
            "sub": "Waiting on others — follow up or reclaim",
            "count_label": f"{len(deleg)} waiting",
            "kind": "flat",
            "items": deleg,
            "capture": 0,
        },
        {
            "key": "tickler",
            "tag": "3",
            "title": "Tickler",
            "sub": "Deferred — resurface when the time comes",
            "count_label": f"{tick_total} parked",
            "kind": "sectioned",
            "capture": 0,
            "sections": [
                {"title": "Next week", "sub": "resurfaces in ~7 days", "items": week},
                {
                    "title": "Next month",
                    "sub": "resurfaces in ~30 days",
                    "items": month,
                },
                {
                    "title": "Next quarter",
                    "sub": "resurfaces in ~90 days",
                    "items": quarter,
                },
            ],
        },
    ]
    for i, b in enumerate(buckets, start=1):
        b["page_no"] = i
    return buckets


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def _env():
    from jinja2 import BaseLoader, Environment, select_autoescape

    template_text = _asset_text("template.html.j2")
    env = Environment(
        loader=BaseLoader(),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["qr"] = qr_datauri
    return env, template_text


def render_bucket_html(env, tmpl_text, bucket, total, the_date) -> str:
    """Full HTML doc containing a single bucket page, with gtd.css inlined
    so it loads under page.set_content (no file server needed)."""
    css = _asset_text("gtd.css")
    html = env.from_string(tmpl_text).render(
        buckets=[bucket],
        total_pages=total,
        date_long=the_date.strftime("%A %-d %B %Y"),
        date_stamp=the_date.strftime("%Y-%m-%d"),
    )
    return html.replace(
        '<link rel="stylesheet" href="gtd.css" />',
        f"<style>\n{css}\n</style>",
    )


def render_pdf(
    data: dict,
    the_date: date,
    out_path: Path,
    debug_html: Path | None = None,
    manifest_path: Path | None = None,
):
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    from .manifest import collect_rois, write_manifest

    buckets = build_buckets(data)
    total = len(buckets)
    env, tmpl_text = _env()
    writer = PdfWriter()
    pages_info = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 640, "height": 900})
        for b in buckets:
            html = render_bucket_html(env, tmpl_text, b, total, the_date)
            if debug_html:
                Path(f"{debug_html.stem}-{b['key']}{debug_html.suffix}").write_text(
                    html, encoding="utf-8"
                )

            page.set_content(html, wait_until="networkidle")
            page.emulate_media(media="print")
            page.evaluate("document.fonts && document.fonts.ready")  # await web fonts

            page_rect = page.evaluate(
                "() => { const r = document.querySelector('.page').getBoundingClientRect(); return {w: r.width, h: r.height}; }"
            )
            height_px = int(page_rect["h"])
            width_px = int(page_rect["w"])
            height_mm = height_px / PX_PER_MM + HEIGHT_PAD_MM

            rois = collect_rois(page) if manifest_path else {}

            pdf_bytes = page.pdf(
                width=f"{PAGE_W_MM}mm",
                height=f"{height_mm:.2f}mm",
                print_background=True,
                prefer_css_page_size=False,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            writer.add_page(PdfReader(io.BytesIO(pdf_bytes)).pages[0])

            if manifest_path:
                pages_info.append(
                    {
                        "bucket": b["key"],
                        "page_no": b["page_no"],
                        "w_px": width_px,
                        "h_px": height_px,
                        "rois": rois,
                    }
                )

        browser.close()

    with open(out_path, "wb") as fh:
        writer.write(fh)

    if manifest_path and pages_info:
        write_manifest(pages_info, the_date, PAGE_W_MM, manifest_path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Generate the GTD reMarkable PDF (one tall page per bucket)."
    )
    p.add_argument("tasks", help="Path to tasks JSON (see tasks.example.json).")
    p.add_argument("--out", default="gtd-sheet.pdf", help="Output PDF path.")
    p.add_argument("--date", default=None, help="Override sheet date (YYYY-MM-DD).")
    p.add_argument(
        "--html",
        default=None,
        help="Also dump per-bucket HTML (debug), e.g. debug.html.",
    )
    args = p.parse_args(argv)

    data = json.loads(Path(args.tasks).read_text(encoding="utf-8"))

    if args.date:
        the_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    elif data.get("date"):
        the_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    else:
        the_date = date.today()

    render_pdf(data, the_date, Path(args.out), Path(args.html) if args.html else None)
    print(
        f"✓ wrote {args.out}  ({the_date.isoformat()}, {len(build_buckets(data))} pages)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
