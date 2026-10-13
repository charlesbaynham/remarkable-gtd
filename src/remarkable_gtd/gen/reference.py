"""Reference-note renderer: one markdown note -> a device-sized PDF to annotate.

The note's body is flowed by Chromium into fixed 157.8 x 210.4 mm pages whose
``@page`` margins leave room for the chrome. The chrome (corner marks, title,
page N/M, header QR, the ✦ AI box) is printed as separate fixed-layout pages,
so each page can carry its own QR and number, and stamped over the body. The
last page is a fixed notes page of blank write-in lines. Every ROI is thus
measured on a layout that does not depend on how the body paginated.

The manifest and a tasks document are embedded in the PDF exactly as on the
daily sheet, so the scanner needs nothing but the sheet that comes back.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from datetime import date
from html import escape
from pathlib import Path

from remarkable_gtd.common.embedded import TASKS_SCHEMA, attach_state
from remarkable_gtd.common.schema import REFERENCE_DOC, make_reference_key
from remarkable_gtd.gen.generate import PAGE_W_MM, PX_PER_MM, _env, _read_asset, qr_datauri
from remarkable_gtd.gen.manifest import build_manifest, collect_rois
from remarkable_gtd.gen.obsidian_md import render_markdown, strip_front_matter

PAGE_H_MM = 210.4
NOTES_LINES = 6
NOTES_HINT = (
    "Write here → appended to the note under today’s date. "
    "Tick ✦ AI on any page to have your margin notes interpreted."
)
MAX_IMAGE_PX = 1404
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
BUCKET_BODY = "reference"
BUCKET_NOTES = "refnotes"


def source_sha256(note_path: Path) -> str:
    return hashlib.sha256(Path(note_path).read_bytes()).hexdigest()


def note_title(markdown: str, stem: str) -> str:
    match = re.search(r"^# +(.+?)\s*#*\s*$", strip_front_matter(markdown), re.MULTILINE)
    return match[1] if match else stem


def _data_uri(image_bytes: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


class Attachments:
    """Resolves ``![[name]]`` embeds against ``<vault>/attachments/``."""

    def __init__(self, vault_root: Path):
        self.root = (Path(vault_root) / "attachments").resolve()

    def embed(self, target: str, width: int | None = None) -> str:
        name = target.split("#", 1)[0].strip()
        label = escape(name)
        suffix = Path(name).suffix.lower()
        if not suffix:
            return f'<span class="att">embedded note: {label}</span>'
        path = (self.root / name).resolve()
        if self.root not in path.parents or not path.is_file():
            return f'<span class="missing">missing: {label}</span>'
        if suffix not in IMAGE_SUFFIXES:
            return f'<span class="att">attachment: {label}</span>'
        try:
            uri = self._image_uri(path)
        except OSError:
            return f'<span class="missing">unreadable: {label}</span>'
        style = f' style="width:{width}px"' if width else ""
        return f'<img src="{uri}" alt="{label}"{style} />'

    @staticmethod
    def _image_uri(path: Path) -> str:
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((MAX_IMAGE_PX, MAX_IMAGE_PX))
            buf = io.BytesIO()
            if image.mode in ("RGB", "L"):
                image.save(buf, format="JPEG", quality=85)
                return _data_uri(buf.getvalue(), "image/jpeg")
            image.convert("RGBA").save(buf, format="PNG")
            return _data_uri(buf.getvalue(), "image/png")


def _chrome_html(env, **context) -> str:
    return env.get_template("reference_chrome.html.j2").render(
        gtd_css=_read_asset("gtd.css"), css=_read_asset("reference.css"), qr=qr_datauri, **context
    )


def _body_html(env, title: str, markdown: str, vault_root: Path) -> str:
    body = render_markdown(markdown, Attachments(vault_root).embed)
    return env.get_template("reference_body.html.j2").render(
        css=_read_asset("reference.css"), title=title, body=body
    )


def _measure(page) -> tuple[dict, dict]:
    """ROIs and the ``.page`` size (px) of the chrome page currently loaded."""
    size = page.evaluate(
        """() => { const r = document.querySelector('.page').getBoundingClientRect();
                   return {w_px: Math.round(r.width * 100) / 100, h_px: Math.round(r.height * 100) / 100}; }"""
    )
    return collect_rois(page), size


def _check_body_fits(page) -> None:
    """Fail loudly if body content spills past the print column (Chromium would shrink the page).

    On screen the note is as wide as the viewport, so it is first narrowed to
    the column the @page margins leave in print.
    """
    right_limit, overflow = page.evaluate(
        """() => {
            const note = document.querySelector('.note');
            note.style.width = 'calc(157.8mm - 2 * var(--m-side))';
            const right = note.getBoundingClientRect().right;
            const widest = Math.max(0, ...[...note.querySelectorAll('*')]
                .map(el => el.getBoundingClientRect().right));
            return [right, widest];
        }"""
    )
    if overflow > right_limit + 2:
        raise RuntimeError(
            f"note content extends to {overflow:.0f}px, past the {right_limit:.0f}px print column; "
            "Chromium would shrink-to-fit and the manifest would no longer match the printed page"
        )


def render_reference(
    note_path: Path,
    vault_root: Path,
    out_path: Path,
    source_sha: str | None = None,
    the_date: date | None = None,
    stem: str | None = None,
    manifest_path: Path | None = None,
    debug_html: Path | None = None,
) -> dict:
    """Render ``note_path`` to ``out_path`` and embed its manifest and tasks.

    Args:
        note_path: The markdown note.
        vault_root: Vault root: ``attachments/`` is resolved against it and the
            note's manifest path is relative to it.
        source_sha: sha256 of the note bytes (page keys carry its first 12 hex
            digits). Computed from the file when omitted.
        stem: Note name used in page keys (default: the file stem).
        manifest_path: Also write the manifest JSON here.
        debug_html: Also write the body HTML here (the chrome is fixed).

    Returns:
        ``{"manifest": ..., "tasks": ..., "page_count": N}``.
    """
    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    note_path, vault_root, out_path = Path(note_path), Path(vault_root), Path(out_path)
    markdown = note_path.read_text(encoding="utf-8")
    stem = stem or note_path.stem
    source_sha = source_sha or source_sha256(note_path)
    the_date = the_date or date.today()
    title = note_title(markdown, stem)
    try:
        rel_path = note_path.resolve().relative_to(vault_root.resolve()).as_posix()
    except ValueError:
        rel_path = note_path.name

    env = _env()
    body_html = _body_html(env, title, markdown, vault_root)
    if debug_html:
        Path(debug_html).write_text(body_html, encoding="utf-8")

    pdf_args = dict(
        width=f"{PAGE_W_MM}mm", height=f"{PAGE_H_MM}mm", print_background=True,
        prefer_css_page_size=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": int(PAGE_W_MM * PX_PER_MM), "height": int(PAGE_H_MM * PX_PER_MM)})
        page.set_content(body_html, wait_until="networkidle")
        page.emulate_media(media="print")
        page.evaluate("document.fonts && document.fonts.ready")
        _check_body_fits(page)
        body_pages = PdfReader(io.BytesIO(page.pdf(**pdf_args))).pages

        total = len(body_pages) + 1
        notes = {"hint": NOTES_HINT, "rows": [{"id": f"RF-C{i}"} for i in range(1, NOTES_LINES + 1)]}
        writer = PdfWriter()
        page_entries: list[dict] = []
        for page_no in range(1, total + 1):
            is_notes = page_no == total
            page_key = make_reference_key(stem, page_no, source_sha)
            page.set_content(
                _chrome_html(env, title=title, page_no=page_no, total=total, page_key=page_key,
                             notes=notes if is_notes else None),
                wait_until="networkidle",
            )
            page.emulate_media(media="print")
            page.evaluate("document.fonts && document.fonts.ready")
            rois, render = _measure(page)
            chrome = PdfReader(io.BytesIO(page.pdf(**pdf_args))).pages[0]
            if is_notes:
                out_page = chrome
            else:
                out_page = body_pages[page_no - 1]
                out_page.merge_page(chrome)
            writer.add_page(out_page)
            page_entries.append({
                "key": page_key, "bucket": BUCKET_NOTES if is_notes else BUCKET_BODY,
                "page_no": page_no, "render": render, "scan": True, "rois": rois,
            })
        browser.close()

    note = {"stem": stem, "path": rel_path, "source_sha": source_sha, "title": title}
    manifest = build_manifest(page_entries, the_date, PAGE_W_MM, extra={"doc": REFERENCE_DOC, "note": note})
    tasks = {
        "schema": TASKS_SCHEMA,
        "date": the_date.strftime("%Y-%m-%d"),
        # The source rides along so a scanned sheet can quote it to the AI.
        "note": {**note, "source": markdown},
        "tasks": {r["id"]: {"id": r["id"], "act": "", "capture": True, "bucket": BUCKET_NOTES}
                  for r in notes["rows"]},
    }
    attach_state(writer, manifest, tasks)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
    if manifest_path is not None:
        Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"manifest": manifest, "tasks": tasks, "page_count": total}
