"""Whole-sheet scanning: a multi-page annotated PDF or an ``.rmdoc``.

Ties the per-page pipeline to the device round-trip: an ``.rmdoc`` downloaded
from the reMarkable is unpacked, its ``.rm`` strokes are drawn onto the
original PDF, the manifest and task ids are read back out of the PDF's
embedded files, and every page is scanned against its manifest entry.
"""
from __future__ import annotations

import json
from pathlib import Path

from remarkable_gtd.common.embedded import read_state
from remarkable_gtd.scan.manifest_io import list_page_keys
from remarkable_gtd.scan.pipeline import ScanConfig, run_scan

RASTER_DPI = 226  # reMarkable 2 native: 157.8 mm page -> 1404 px


def task_texts_from_tasks(tasks: dict | None) -> dict[str, str]:
    """``{task_id: printed action text}`` from a ``gtd.tasks/1`` document."""
    if not tasks:
        return {}
    return {tid: t.get("act", "") for tid, t in tasks.get("tasks", {}).items()}


def scan_pdf(
    pdf_path: Path,
    manifest: dict,
    cfg: ScanConfig | None = None,
    work_dir: Path | None = None,
    task_texts: dict[str, str] | None = None,
    dpi: int = RASTER_DPI,
) -> dict:
    """Scan every page of an annotated PDF against the manifest's pages.

    Pages are matched to manifest entries by position (page 1 = first
    manifest key). Per-page failures are recorded rather than raised.

    Returns:
        ``{"schema": "gtd.decisions/1", "source_pdf": ..., "pages": [...]}``
        where each page entry is a per-page decisions document (see
        :func:`remarkable_gtd.scan.pipeline.run_scan`) plus ``page_key`` and
        ``page_no``, or ``{"page_key", "page_no", "error"}``.
    """
    import pymupdf

    cfg = cfg or ScanConfig()
    pdf_path = Path(pdf_path)
    work_dir = Path(work_dir) if work_dir else pdf_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    keys = list_page_keys(manifest)
    if not keys:
        raise ValueError("manifest has no pages")

    doc = pymupdf.open(str(pdf_path))
    page_results: list[dict] = []
    warnings: list[str] = []
    if len(doc) != len(keys):
        warnings.append(f"PDF has {len(doc)} pages but manifest has {len(keys)} pages")

    for i, page in enumerate(doc):
        if i >= len(keys):
            break
        page_key = keys[i]
        mat = pymupdf.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_path = work_dir / f"{pdf_path.stem}.page{i + 1}.png"
        pix.save(str(img_path))
        try:
            decisions = run_scan(img_path, manifest, cfg, page_key, task_texts=task_texts)
            page_results.append({"page_key": page_key, "page_no": i + 1, **decisions})
        except Exception as exc:  # keep going: one bad page must not lose the rest
            page_results.append({"page_key": page_key, "page_no": i + 1, "error": str(exc)})
    doc.close()

    return {
        "schema": "gtd.decisions/1",
        "source_pdf": str(pdf_path),
        "manifest": manifest.get("_path", "<embedded>"),
        "warnings": warnings,
        "pages": page_results,
    }


def scan_rmdoc(
    rmdoc_path: Path,
    work_dir: Path,
    cfg: ScanConfig | None = None,
    manifest: dict | None = None,
    tasks: dict | None = None,
) -> tuple[dict, dict, dict | None, Path]:
    """Render an ``.rmdoc``'s strokes onto its PDF and scan every page.

    The manifest and tasks default to the documents embedded in the PDF by
    the generator; pass them explicitly for a sheet that predates embedding.

    Returns:
        ``(decisions, manifest, tasks, annotated_pdf_path)``.

    Raises:
        ValueError: If no manifest is available.
    """
    from remarkable_gtd.rm.annotations import extract_from_rmdoc, render_rmdoc

    rmdoc_path = Path(rmdoc_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if manifest is None or tasks is None:
        pdf_bytes, _ = extract_from_rmdoc(rmdoc_path)
        if not pdf_bytes:
            raise ValueError(f"no PDF inside {rmdoc_path}")
        emb_manifest, emb_tasks = read_state(pdf_bytes)
        manifest = manifest or emb_manifest
        tasks = tasks or emb_tasks
    if manifest is None:
        raise ValueError(
            f"{rmdoc_path.name} carries no embedded manifest and none was given; "
            "it was probably generated before manifests were embedded"
        )

    annotated = work_dir / f"{rmdoc_path.stem}.annotated.pdf"
    render_rmdoc(rmdoc_path, annotated)
    decisions = scan_pdf(
        annotated, manifest, cfg, work_dir, task_texts=task_texts_from_tasks(tasks)
    )
    decisions["source_rmdoc"] = str(rmdoc_path)
    (work_dir / f"{rmdoc_path.stem}.decisions.json").write_text(
        json.dumps(decisions, indent=2), encoding="utf-8"
    )
    return decisions, manifest, tasks, annotated


def summarize(decisions: dict) -> dict:
    """Counts for a log line: pages, tasks, actions, edits, captures, warnings."""
    pages = decisions.get("pages", [])
    tasks = [t for p in pages for t in p.get("tasks", [])]
    return {
        "pages": len(pages),
        "errors": sum(1 for p in pages if "error" in p),
        "tasks": len(tasks),
        "actions": sum(1 for t in tasks if t.get("action") != "none"),
        "edits": sum(1 for t in tasks if t.get("edited") or t.get("fields")),
        "captures": sum(
            1 for p in pages for c in p.get("captures", []) if c.get("inked")
        ),
        "warnings": sum(len(p.get("warnings", [])) for p in pages)
        + len(decisions.get("warnings", [])),
    }
