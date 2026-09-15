"""Calibration: target document, stroke/target pairing and the per-axis fit."""
from __future__ import annotations

import json

import pymupdf

from remarkable_gtd.cli import calibrate as cal


def _traced_cross(x_rm: float, y_rm: float, arm: float = 40) -> list[cal.Stroke]:
    return [
        cal.Stroke([x_rm - arm, x_rm, x_rm + arm], [y_rm + 1, y_rm, y_rm - 1]),
        cal.Stroke([x_rm + 1, x_rm, x_rm - 1], [y_rm - arm, y_rm, y_rm + arm]),
    ]


def _rm_from_pdf(x_pt, y_pt, sx, bx, sy, by):
    return (x_pt - bx) / sx, (y_pt - by) / sy


def test_make_embeds_targets(tmp_path):
    out = tmp_path / "cal.pdf"
    spec = cal.make_document(out, heights_mm=(150, 450))
    doc = pymupdf.open(out)
    assert len(doc) == 2
    assert abs(doc[1].rect.height / cal.MM - 450) < 0.01
    embedded = json.loads(doc.embfile_get(cal.ATTACH_TARGETS).decode("utf-8"))
    assert embedded == spec
    assert len(spec["pages"][1]["targets"]) == 8 * 3  # (450 - 25 - 15) // 55 + 1 rows
    assert spec["pages"][0]["targets"][0]["id"] == "A1"


def test_fit_recovers_a_known_transform():
    targets = cal.target_grid(450)
    sx, bx = cal.NOMINAL_SCALE * 1.004, cal.RM_W / 2 * cal.NOMINAL_SCALE + 1.5
    sy, by = cal.NOMINAL_SCALE * 0.996, -2.0
    strokes: list[cal.Stroke] = []
    for t in targets:
        strokes += _traced_cross(*_rm_from_pdf(t["x_pt"], t["y_pt"], sx, bx, sy, by))
    strokes.append(cal.Stroke([0, 300, 600], [0, 900, 1800]))  # the ruled diagonal, ignored
    pairs = cal.pair_targets(strokes, targets)
    assert len(pairs) == len(targets)
    fit = cal.fit_page(pairs, cal.PAGE_W_MM * cal.MM)
    assert abs(fit["x"]["scale"] - sx) < 1e-6 and abs(fit["x"]["offset"] - bx) < 1e-3
    assert abs(fit["y"]["scale"] - sy) < 1e-6 and abs(fit["y"]["offset"] - by) < 1e-3
    assert fit["x"]["rms_pt"] < 1e-6 and fit["y"]["rms_pt"] < 1e-6


def test_pooled_fit_reports_device_width():
    targets = cal.target_grid(620)
    s, cx = 447.31 / 1410, 705.9
    strokes: list[cal.Stroke] = []
    for t in targets:
        strokes += _traced_cross(t["x_pt"] / s - cx, t["y_pt"] / s)
    pooled = cal.fit_pooled(cal.pair_targets(strokes, targets), 447.31)
    assert abs(pooled["fit_width_px"] - 1410) < 0.01
    assert abs(pooled["x_centre_px"] - cx) < 0.01 and abs(pooled["y_offset_px"]) < 0.01


def test_untraced_targets_are_dropped():
    targets = cal.target_grid(150)
    strokes = _traced_cross(*_rm_from_pdf(targets[0]["x_pt"], targets[0]["y_pt"],
                                          cal.NOMINAL_SCALE, cal.RM_W / 2 * cal.NOMINAL_SCALE,
                                          cal.NOMINAL_SCALE, 0))
    pairs = cal.pair_targets(strokes, targets)
    assert [p["id"] for p in pairs] == ["A1"]
