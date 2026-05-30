"""End-to-end integration test: render → paint ink → scan → verify decisions."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import pytest

pytestmark = pytest.mark.usefixtures("_playwright_available")


def test_e2e_done_tick(tmp_path, tasks_min_path):
    """Render tasks.min.json, paint a 'done' tick on NA-01, run_scan, assert action=='done'."""
    from remarkable_gtd.gen.generate import render_pdf
    from remarkable_gtd.scan.pipeline import run_scan, ScanConfig
    from tests.conftest import rasterize_page, paint_ink

    data = json.loads(tasks_min_path.read_text(encoding="utf-8"))
    pdf_path = tmp_path / "sheet.pdf"
    manifest_path = tmp_path / "sheet.manifest.json"
    render_pdf(data, date(2026, 5, 30), pdf_path, manifest_path=manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Find the next-actions page
    next_key = None
    for key, page in manifest["pages"].items():
        if page["bucket"] == "next":
            next_key = key
            break
    assert next_key is not None

    manifest_page = manifest["pages"][next_key]
    page_idx = manifest_page["page_no"] - 1  # zero-based

    # Rasterize the next-actions page
    img = rasterize_page(pdf_path, page_index=page_idx, dpi=226)

    # Paint a tick in NA-01's done box
    img_inked = paint_ink(img, manifest_page, "NA-01:done", style="tick")

    # Save as PNG for scanning
    image_path = tmp_path / "inked.png"
    cv2.imwrite(str(image_path), cv2.cvtColor(img_inked, cv2.COLOR_RGB2BGR))

    # Run scan with NullEngine
    cfg = ScanConfig(ocr_engine="null", ink_fill_threshold=0.06)
    decisions = run_scan(image_path, manifest, cfg, page_key=next_key)

    # Find NA-01 task result
    na01 = next((t for t in decisions["tasks"] if t["id"] == "NA-01"), None)
    assert na01 is not None, f"NA-01 not found in decisions. Tasks: {[t['id'] for t in decisions['tasks']]}"
    assert na01["action"] == "done", f"Expected action='done', got {na01['action']}"


def test_e2e_no_ticks_all_none(tmp_path, tasks_min_path):
    """Render with no ticks → all actions are 'none'."""
    from remarkable_gtd.gen.generate import render_pdf
    from remarkable_gtd.scan.pipeline import run_scan, ScanConfig
    from tests.conftest import rasterize_page

    data = json.loads(tasks_min_path.read_text(encoding="utf-8"))
    pdf_path = tmp_path / "sheet.pdf"
    manifest_path = tmp_path / "sheet.manifest.json"
    render_pdf(data, date(2026, 5, 30), pdf_path, manifest_path=manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Test on the next-actions page without any ink
    next_key = None
    for key, page in manifest["pages"].items():
        if page["bucket"] == "next":
            next_key = key
            break
    assert next_key is not None

    manifest_page = manifest["pages"][next_key]
    page_idx = manifest_page["page_no"] - 1

    img = rasterize_page(pdf_path, page_index=page_idx, dpi=226)
    image_path = tmp_path / "no_ink.png"
    cv2.imwrite(str(image_path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

    cfg = ScanConfig(ocr_engine="null", ink_fill_threshold=0.06)
    decisions = run_scan(image_path, manifest, cfg, page_key=next_key)

    for task in decisions["tasks"]:
        assert task["action"] == "none", f"Task {task['id']} action={task['action']} (expected none)"
