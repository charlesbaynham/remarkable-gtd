"""
GTD reMarkable Sheet — PDF generator
=====================================
Builds a 4-page PDF (Inbox / Next Actions / Delegated / Tickler) for the
reMarkable 2. Each bucket is ONE page, 157.8 mm wide (the device panel
width) and exactly as TALL as its content needs — no truncation, no blank
tails. Every task carries a stable ID + QR fiducial, and a fixed labelled
gutter, so the nightly vision agent reads your handwritten marks reliably.

Assets (template + CSS) are loaded via importlib.resources so they work
correctly after pip install.
"""
from __future__ import annotations

import base64
import importlib.resources
import io
import json
from datetime import date, datetime
from pathlib import Path

PX_PER_MM = 96.0 / 25.4          # CSS px per mm at 96 dpi (Chromium print unit)
PAGE_W_MM = 157.8                # reMarkable 2 panel width
HEIGHT_PAD_MM = 0.6              # guard against rounding overflow to a 2nd page


# --------------------------------------------------------------------------
# QR fiducials  ->  PNG data-URI
# --------------------------------------------------------------------------
def qr_datauri(text: str) -> str:
    import qrcode

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
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
        {"key": "inbox", "tag": "0", "title": "Inbox",
         "sub": "Unprocessed capture — route every item out today",
         "count_label": f"{len(inbox)} to process",
         "kind": "flat", "items": inbox, "capture": 6},
        {"key": "next", "tag": "1", "title": "Next Actions",
         "sub": "On your plate — do, delegate, or defer",
         "count_label": f"{len(nxt)} actions",
         "kind": "flat", "items": nxt, "capture": 0},
        {"key": "delegated", "tag": "2", "title": "Delegated",
         "sub": "Waiting on others — follow up or reclaim",
         "count_label": f"{len(deleg)} waiting",
         "kind": "flat", "items": deleg, "capture": 0},
        {"key": "tickler", "tag": "3", "title": "Tickler",
         "sub": "Deferred — resurface when the time comes",
         "count_label": f"{tick_total} parked",
         "kind": "sectioned", "capture": 0,
         "sections": [
             {"title": "Next week", "sub": "resurfaces in ~7 days", "items": week},
             {"title": "Next month", "sub": "resurfaces in ~30 days", "items": month},
             {"title": "Next quarter", "sub": "resurfaces in ~90 days", "items": quarter},
         ]},
    ]
    for i, b in enumerate(buckets, start=1):
        b["page_no"] = i
    return buckets


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def _read_asset(filename: str) -> str:
    """Read a text asset from the remarkable_gtd.gen.assets package."""
    pkg = importlib.resources.files("remarkable_gtd.gen.assets")
    return (pkg / filename).read_text(encoding="utf-8")


def _env():
    from jinja2 import Environment, BaseLoader, select_autoescape

    class AssetLoader(BaseLoader):
        def get_source(self, environment, template):
            try:
                source = _read_asset(template)
            except (FileNotFoundError, TypeError) as exc:
                from jinja2 import TemplateNotFound
                raise TemplateNotFound(template) from exc
            return source, None, lambda: True

    env = Environment(
        loader=AssetLoader(),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["qr"] = qr_datauri
    return env


def render_bucket_html(tmpl, bucket, total, the_date) -> str:
    """Full HTML doc containing a single bucket page, with gtd.css inlined
    so it loads under page.set_content (no file server needed)."""
    css = _read_asset("gtd.css")
    html = tmpl.render(
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
    manifest_path: Path | None = ...,  # type: ignore[assignment]
) -> None:
    """Render the GTD sheet to PDF and optionally write a layout manifest.

    Args:
        data: Parsed tasks JSON dict.
        the_date: Date to stamp on the sheet.
        out_path: Output PDF path.
        debug_html: If given, also write per-bucket HTML files for debugging.
        manifest_path: Path for the manifest JSON sidecar. Defaults to
            ``out_path.with_suffix('.manifest.json')``. Pass ``None`` to
            suppress manifest writing.
    """
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    from remarkable_gtd.gen.manifest import collect_rois, write_manifest
    from remarkable_gtd.common.schema import make_page_key

    # Resolve sentinel default
    if manifest_path is ...:  # type: ignore[comparison-overlap]
        manifest_path = out_path.with_suffix(".manifest.json")

    buckets = build_buckets(data)
    total = len(buckets)
    tmpl = _env().get_template("template.html.j2")
    writer = PdfWriter()
    buckets_rois: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 640, "height": 900})
        for b in buckets:
            html = render_bucket_html(tmpl, b, total, the_date)
            if debug_html:
                Path(f"{debug_html.stem}-{b['key']}{debug_html.suffix}").write_text(
                    html, encoding="utf-8"
                )

            page.set_content(html, wait_until="networkidle")
            page.emulate_media(media="print")
            page.evaluate("document.fonts && document.fonts.ready")  # await web fonts

            # Guard: if any content is wider than the page (e.g. fallback
            # fonts because IBM Plex isn't installed and Google Fonts is
            # unreachable), Chromium will silently shrink-to-fit the PDF and
            # every manifest coordinate would be wrong. Fail loudly instead.
            # (html/body stretch to the viewport, so measure real elements.)
            page_w, max_right = page.evaluate(
                """() => {
                    const pw = document.querySelector('.page')
                        .getBoundingClientRect().width;
                    let right = 0;
                    document.querySelectorAll('body *').forEach(el => {
                        right = Math.max(right, el.getBoundingClientRect().right);
                    });
                    return [pw, right];
                }"""
            )
            if max_right > page_w + 2:
                raise RuntimeError(
                    f"Bucket '{b['key']}' layout overflows the page "
                    f"(content extends to {max_right:.0f}px > page "
                    f"{page_w:.0f}px). Chromium would shrink-to-fit and break "
                    "the layout manifest. This usually means the IBM Plex "
                    "fonts are unavailable — install them locally (e.g. "
                    "apt install fonts-ibm-plex) or allow access to "
                    "fonts.googleapis.com."
                )

            height_px = page.evaluate(
                "Math.ceil(document.querySelector('.page').getBoundingClientRect().height)"
            )
            height_mm = height_px / PX_PER_MM + HEIGHT_PAD_MM

            # Collect ROIs while the page is still live
            if manifest_path is not None:
                rois = collect_rois(page)
                render_w = page.evaluate(
                    "Math.round(document.querySelector('.page').getBoundingClientRect().width)"
                )
                page_key = make_page_key(b["key"], the_date.strftime("%Y-%m-%d"))
                buckets_rois.append({
                    "key": page_key,
                    "bucket": b["key"],
                    "page_no": b["page_no"],
                    "render": {"w_px": render_w, "h_px": height_px},
                    "rois": rois,
                })

            pdf_bytes = page.pdf(
                width=f"{PAGE_W_MM}mm",
                height=f"{height_mm:.2f}mm",
                print_background=True,
                prefer_css_page_size=False,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
            writer.add_page(PdfReader(io.BytesIO(pdf_bytes)).pages[0])

        browser.close()

    with open(out_path, "wb") as fh:
        writer.write(fh)

    if manifest_path is not None and buckets_rois:
        write_manifest(buckets_rois, the_date, PAGE_W_MM, Path(manifest_path))
