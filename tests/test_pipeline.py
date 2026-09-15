"""Unit tests for pure helpers in the pipeline module (no images)."""
from __future__ import annotations

from remarkable_gtd.scan.pipeline import row_roi


def test_row_roi_uses_manifest_row_when_present():
    task_rois = {
        "row": {"x": 0.05, "y": 0.10, "w": 0.9, "h": 0.05},
        "act": {"x": 0.1, "y": 0.11, "w": 0.5, "h": 0.03},
        "qr": {"x": 0.02, "y": 0.10, "w": 0.03, "h": 0.03},
    }
    assert row_roi(task_rois) == task_rois["row"]


def test_row_roi_falls_back_to_union_of_all_rois():
    task_rois = {
        "qr": {"x": 0.02, "y": 0.10, "w": 0.03, "h": 0.03},
        "act": {"x": 0.10, "y": 0.11, "w": 0.50, "h": 0.03},
        "slot_due": {"x": 0.70, "y": 0.10, "w": 0.10, "h": 0.04},
    }
    result = row_roi(task_rois, page_w_frac_pad=0.01, page_h_frac_pad=0.003)

    x1 = min(r["x"] for r in task_rois.values())
    y1 = min(r["y"] for r in task_rois.values())
    x2 = max(r["x"] + r["w"] for r in task_rois.values())
    y2 = max(r["y"] + r["h"] for r in task_rois.values())

    assert result["x"] == x1 - 0.01
    assert result["y"] == y1 - 0.003
    assert result["w"] == (x2 + 0.01) - (x1 - 0.01)
    assert result["h"] == (y2 + 0.003) - (y1 - 0.003)


def test_row_roi_clamps_to_page_bounds():
    task_rois = {
        "qr": {"x": 0.0, "y": 0.0, "w": 0.02, "h": 0.02},
        "act": {"x": 0.9, "y": 0.98, "w": 0.1, "h": 0.02},
    }
    result = row_roi(task_rois, page_w_frac_pad=0.05, page_h_frac_pad=0.05)

    assert result["x"] == 0.0
    assert result["y"] == 0.0
    assert result["x"] + result["w"] == 1.0
    assert result["y"] + result["h"] == 1.0
