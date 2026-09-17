"""
GTD reMarkable Sheet — PDF generator
=====================================
Builds the sheet for the reMarkable 2: Inbox / Next Actions / Delegated /
Tickler, a read-only Projects index, one page per project, and a New
Projects page of blank rows. Each bucket is ONE page, 157.8 mm wide (the device panel
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


CAPTURE_LINES = 6        # blank capture rows at the foot of the Inbox page
PROJECT_ADD_LINES = 4    # blank "add an action" rows at the foot of a project page
NEW_PROJECT_LINES = 6    # blank rows on the New Projects page

# Which view a project's current next action is surfaced in -> printed badge.
SURFACED_BADGES = {
    "next": "NA",
    "delegated": "DG",
    "scheduled": "SC",
    "tickler": "TK",
}


def _capture_items(
    prefix: str, count: int, bare: bool = False, numbered: bool = False, **extra
) -> list[dict]:
    """Blank write-in rows: no printed text, just an inked-or-not ``act`` box.

    ``numbered`` ids read ``CP-01``/``NP-01`` (a page whose rows are all
    write-in lines); otherwise ``P01-C1`` — an add-a-line at the foot of a
    page whose other rows are printed items.
    """
    out = []
    for i in range(1, count + 1):
        item = {"id": f"{prefix}-{i:02d}" if numbered else f"{prefix}-C{i}",
                "act": "", "capture": True}
        if bare:
            item["bare"] = True
        item.update(extra)
        out.append(item)
    return out


NEW_PROJECTS_KEY = "new-projects"


def build_new_projects_page() -> dict:
    """The New Projects page: blank rows for projects that do not exist yet.

    The Inbox has blank capture lines; this is the same idea one level up.
    A row's write-in line is the project's first action and its PROJECT box
    is the project's name, so a project is born already carrying the thing
    that made you want it — nothing is invented on your behalf.

    The rows carry the Inbox routing gutter (and ✦ AI), because a thing you
    wrote down as a project often turns out to be one delegable action, or
    something to defer, or nothing at all. A routing tick means exactly
    that: do not create a project, file this like any Inbox item.

    It is the sheet's LAST page, so every existing page keeps its position
    (``scan_pdf`` matches PDF pages to manifest keys by position).
    """
    return {
        "key": NEW_PROJECTS_KEY,
        "bucket": "newproj",
        "tag": "P+",
        "title": "New Projects",
        "sub": "Projects that do not exist yet — write one per line",
        "count_label": f"{NEW_PROJECT_LINES} blank",
        "kind": "newproj",
        "scan": True,
        "items": _capture_items("NP", NEW_PROJECT_LINES, numbered=True),
    }


def build_project_pages(projects: list[dict]) -> tuple[dict, list[dict]]:
    """Build the projects summary page and one page per project.

    Each project gets ``ref`` ``P01``, ``P02``… Its unchecked items become
    rows ``P01-03`` (numbered by their position in the project's item list,
    so the printed id survives re-ordering of the *open* items), and four
    blank add-lines ``P01-C1``…``P01-C4`` close the page.
    """
    summary_entries: list[dict] = []
    pages: list[dict] = []
    for idx, proj in enumerate(projects, start=1):
        ref = f"P{idx:02d}"
        name = proj.get("name", f"Project {idx}")
        items = proj.get("items") or []
        open_items: list[dict] = []
        done_items: list[str] = []
        for pos, it in enumerate(items, start=1):
            if it.get("done"):
                done_items.append(it.get("text", ""))
                continue
            entry = {
                "id": f"{ref}-{pos:02d}",
                "act": it.get("text", ""),
                "proj": name,
            }
            if it.get("handle") is not None:
                entry["handle"] = it["handle"]
            surfaced = it.get("surfaced")
            if surfaced:
                entry["surfaced"] = surfaced
                entry["badge"] = SURFACED_BADGES.get(surfaced, surfaced)
            open_items.append(entry)

        first = open_items[0] if open_items else None
        if proj.get("stalled") or first is None:
            badge = "STALLED"
        else:
            badge = first.get("badge") or "STALLED"

        page_no_placeholder = 0
        pages.append({
            "key": f"project-{idx:02d}",
            "bucket": "project",
            "tag": ref,
            "title": name,
            "sub": "Project — tick actions off, amend them, or add new ones",
            "goal": proj.get("goal", ""),
            "status": proj.get("status") or [],
            "count_label": f"{len(open_items)} open",
            "kind": "project",
            "scan": True,
            "project": {"index": idx, "name": name},
            "items": open_items,
            "done_items": done_items,
            "capture_items": _capture_items(ref, PROJECT_ADD_LINES, bare=True, proj=name),
        })
        summary_entries.append({
            "ref": ref,
            "name": name,
            "goal": proj.get("goal", ""),
            "open_count": len(open_items),
            "next_action": first["act"] if first else "",
            "badge": badge,
            "page_no": page_no_placeholder,
        })

    summary = {
        "key": "projects", "bucket": "projects", "tag": "P", "title": "Projects",
        "sub": "Outcomes in flight — one page each, overleaf",
        "count_label": f"{len(projects)} projects",
        "kind": "summary", "scan": False,
        "projects": summary_entries,
    }
    return summary, pages


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
         "kind": "flat", "items": inbox,
         "capture_items": _capture_items("CP", CAPTURE_LINES, numbered=True)},
        {"key": "next", "tag": "1", "title": "Next Actions",
         "sub": "On your plate — do, delegate, or defer",
         "count_label": f"{len(nxt)} actions",
         "kind": "flat", "items": nxt},
        {"key": "delegated", "tag": "2", "title": "Delegated",
         "sub": "Waiting on others — follow up or reclaim",
         "count_label": f"{len(deleg)} waiting",
         "kind": "flat", "items": deleg},
        {"key": "tickler", "tag": "3", "title": "Tickler",
         "sub": "Deferred — resurface when the time comes",
         "count_label": f"{tick_total} parked",
         "kind": "sectioned",
         "sections": [
             {"title": "Next week", "sub": "resurfaces in ~7 days", "items": week},
             {"title": "Next month", "sub": "resurfaces in ~30 days", "items": month},
             {"title": "Next quarter", "sub": "resurfaces in ~90 days", "items": quarter},
         ]},
    ]

    summary, project_pages = build_project_pages(data.get("projects") or [])
    buckets.append(summary)
    buckets.extend(project_pages)
    new_projects = build_new_projects_page()
    buckets.append(new_projects)

    for i, b in enumerate(buckets, start=1):
        b["page_no"] = i
        b.setdefault("bucket", b["key"])
    # The summary prints each project's page number, known only now.
    by_ref = {p["project"]["index"]: p["page_no"] for p in project_pages}
    for entry in summary["projects"]:
        entry["page_no"] = by_ref[int(entry["ref"][1:])]
    summary["new_page_ref"] = NEW_PROJECTS_KEY
    summary["new_page_no"] = new_projects["page_no"]
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
    embed_state: bool = True,
) -> dict:
    """Render the GTD sheet to PDF and optionally write a layout manifest.

    Args:
        data: Parsed tasks JSON dict.
        the_date: Date to stamp on the sheet.
        out_path: Output PDF path.
        debug_html: If given, also write per-bucket HTML files for debugging.
        manifest_path: Path for the manifest JSON sidecar. Defaults to
            ``out_path.with_suffix('.manifest.json')``. Pass ``None`` to
            suppress writing the sidecar (the manifest is still built).
        embed_state: Attach the manifest and the tasks document (keyed by
            the ids printed on the sheet) to the PDF as embedded files, so a
            sheet that comes back from the device carries its own state.

    Returns:
        ``{"manifest": ..., "tasks": ...}`` — the documents that were
        embedded (``tasks`` is the ``gtd.tasks/1`` document, see
        :func:`remarkable_gtd.common.embedded.tasks_document`).
    """
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    from remarkable_gtd.common.embedded import attach_state, tasks_document
    from remarkable_gtd.common.schema import make_page_key
    from remarkable_gtd.gen.manifest import build_manifest, collect_rois

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
            rois = collect_rois(page)
            render_w = page.evaluate(
                "Math.round(document.querySelector('.page').getBoundingClientRect().width)"
            )
            page_key = make_page_key(b["key"], the_date.strftime("%Y-%m-%d"))
            buckets_rois.append({
                "key": page_key,
                "bucket": b.get("bucket", b["key"]),
                "page_no": b["page_no"],
                "render": {"w_px": render_w, "h_px": height_px},
                "scan": b.get("scan", True),
                "project": b.get("project"),
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

    manifest = build_manifest(buckets_rois, the_date, PAGE_W_MM)
    add_internal_links(writer, buckets, buckets_rois)
    tasks = tasks_document(buckets, the_date.strftime("%Y-%m-%d"), data.get("context"))
    if embed_state:
        attach_state(writer, manifest, tasks)

    with open(out_path, "wb") as fh:
        writer.write(fh)

    if manifest_path is not None:
        Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return {"manifest": manifest, "tasks": tasks}


# --------------------------------------------------------------------------
# Internal navigation: summary <-> project pages
# --------------------------------------------------------------------------
def add_internal_links(writer, buckets: list[dict], buckets_rois: list[dict]) -> int:
    """Turn every ``link:<target>`` ROI into a PDF GoTo link annotation.

    ``link:P01`` on the projects summary jumps to that project's page,
    ``link:new-projects`` to the New Projects page, and ``link:projects``
    on either jumps back. The ROI rectangle is in
    page fractions with y measured from the top, so it is flipped into PDF
    user space against the page's media box.

    Returns the number of annotations added.
    """
    from pypdf.annotations import Link

    # ref ("P01" / "projects" / "new-projects") -> 0-based PDF page index
    targets: dict[str, int] = {}
    for b in buckets:
        if b.get("kind") == "summary":
            targets["projects"] = b["page_no"] - 1
        elif b.get("kind") == "newproj":
            targets[NEW_PROJECTS_KEY] = b["page_no"] - 1
        elif b.get("kind") == "project":
            targets[f"P{b['project']['index']:02d}"] = b["page_no"] - 1

    added = 0
    for entry in buckets_rois:
        page_index = entry["page_no"] - 1
        box = writer.pages[page_index].mediabox
        pw, ph = float(box.width), float(box.height)
        for key, roi in entry["rois"].items():
            if not key.startswith("link:"):
                continue
            target = targets.get(key[len("link:"):])
            if target is None:
                continue
            rect = (
                roi["x"] * pw,
                ph * (1.0 - (roi["y"] + roi["h"])),
                (roi["x"] + roi["w"]) * pw,
                ph * (1.0 - roi["y"]),
            )
            writer.add_annotation(
                page_number=page_index,
                annotation=Link(rect=rect, target_page_index=target),
            )
            added += 1
    return added
