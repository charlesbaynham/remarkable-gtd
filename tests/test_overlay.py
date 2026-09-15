"""gtd-overlay: every ROI drawn onto the page, from a PDF, an .rmdoc or an image."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from remarkable_gtd.cli.overlay import main
from remarkable_gtd.scan.overlay import classify, draw_rois

FIX = Path(__file__).parent / "fixtures" / "annotated_scan"


def test_classify():
    assert classify("reg:tl") == "reg"
    assert classify("page:qr") == "qr" and classify("NA-01:qr") == "qr"
    assert classify("NA-01:act") == "act"
    assert classify("NA-01:slot_due") == "slot" and classify("capture:N1:line") == "slot"
    assert classify("NA-01:done") == "tick" and classify("capture:N1:box") == "tick"


def test_draw_rois_marks_pixels():
    gray = np.full((100, 200), 255, dtype=np.uint8)
    img = draw_rois(gray, {"NA-01:done": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.4}}, (200, 100))
    assert img.shape == (100, 200, 3)
    assert not np.array_equal(img[:, :, 0], img[:, :, 2])  # something coloured was drawn


def test_cli_rmdoc_and_image(tmp_path):
    out = tmp_path / "ov"
    rc = main([str(FIX / "gtd_sheet.rmdoc"), "--manifest", str(FIX / "sheet.manifest.json"), "-o", str(out)])
    assert rc == 0
    pngs = sorted(p.name for p in out.glob("*.overlay.png"))
    assert pngs == [f"gtd_sheet.annotated.page{i}.overlay.png" for i in range(1, 5)]

    rc = main([str(FIX / "page_01.png"), "--manifest", str(FIX / "sheet.manifest.json"),
               "--page", "GTD|next|2026-06-02", "--raw", "--labels", "-o", str(out)])
    assert rc == 0 and (out / "page_01.overlay.png").exists()
