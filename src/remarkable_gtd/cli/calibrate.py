"""gtd-calibrate — measure how the reMarkable maps pen strokes onto a PDF page.

``make`` prints a document of cross-hair targets at known positions on pages
of several heights (the sheet pages range from shorter than the screen to
three screens tall). Trace every cross on the device, download the
``.rmdoc`` and run ``fit``: it pairs each traced cross with its printed
position and least-squares fits ``pdf = a * rm + b`` per axis and per page,
which is exactly the transform ``rm/annotations.py`` applies when it draws
strokes back onto the sheet before scanning.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

MM = 72 / 25.4
PAGE_W_MM = 157.8
RM_W, RM_H = 1404, 1872
NOMINAL_SCALE = 72 / 226
ATTACH_TARGETS = "gtd.calibration.json"

PAGE_HEIGHTS_MM = (150, 211, 450, 620)
COLUMNS_MM = (15, 79, 143)
ROW_STEP_MM = 55
MARGIN_MM = 15
TOP_MM = 25  # leaves room for the instructions above row A
ARM_MM, RING_MM = 4.0, 3.0


# --- make ------------------------------------------------------------------------------


def target_grid(height_mm: float) -> list[dict]:
    rows = int((height_mm - TOP_MM - MARGIN_MM) // ROW_STEP_MM) + 1
    out = []
    for r in range(rows):
        y = TOP_MM + r * ROW_STEP_MM
        for c, x in enumerate(COLUMNS_MM):
            out.append({"id": f"{chr(65 + r)}{c + 1}", "x_pt": x * MM, "y_pt": y * MM})
    return out


def make_document(out_path: Path, heights_mm=PAGE_HEIGHTS_MM) -> dict:
    import pymupdf

    doc = pymupdf.open()
    pages: list[dict] = []
    for n, h_mm in enumerate(heights_mm, start=1):
        page = doc.new_page(width=PAGE_W_MM * MM, height=h_mm * MM)
        targets = target_grid(h_mm)
        shape = page.new_shape()
        for t in targets:
            x, y = t["x_pt"], t["y_pt"]
            shape.draw_line((x - ARM_MM * MM, y), (x + ARM_MM * MM, y))
            shape.draw_line((x, y - ARM_MM * MM), (x, y + ARM_MM * MM))
            shape.draw_circle((x, y), RING_MM * MM)
        shape.finish(color=(0, 0, 0), width=0.6)
        shape.commit()
        for t in targets:
            page.insert_text((t["x_pt"] + 3.5 * MM, t["y_pt"] - 3.5 * MM), t["id"], fontsize=6)
        page.insert_text(
            (MARGIN_MM * MM, 6 * MM),
            [f"Calibration {n}/{len(heights_mm)} - page {h_mm} mm tall.",
             "Trace every cross: one horizontal and one vertical stroke through its centre,",
             f"then rule one straight line from {targets[0]['id']} to {targets[-1]['id']}."],
            fontsize=6.5,
        )
        pages.append({"page_no": n, "height_mm": h_mm, "width_pt": page.rect.width,
                      "height_pt": page.rect.height, "targets": targets})
    spec = {"schema": "gtd.calibration/1", "pages": pages}
    doc.embfile_add(ATTACH_TARGETS, json.dumps(spec).encode("utf-8"))
    doc.save(str(out_path))
    doc.close()
    return spec


# --- fit ---------------------------------------------------------------------------------


@dataclass
class Stroke:
    xs: list[float]
    ys: list[float]

    @property
    def centre(self) -> tuple[float, float]:
        return statistics.median(self.xs), statistics.median(self.ys)

    @property
    def extent(self) -> tuple[float, float]:
        return max(self.xs) - min(self.xs), max(self.ys) - min(self.ys)


def nominal_to_pdf(x_rm: float, y_rm: float) -> tuple[float, float]:
    """The transform ``rm/annotations.py`` uses today; only used to pair strokes with targets."""
    return (x_rm + RM_W / 2) * NOMINAL_SCALE, y_rm * NOMINAL_SCALE


def pair_targets(strokes: list[Stroke], targets: list[dict], radius_pt: float = 12 * MM) -> list[dict]:
    """For each target, the rm-space centre of the traced cross: the vertical
    stroke gives x, the horizontal stroke gives y. Targets missing either
    stroke are dropped."""
    out = []
    for t in targets:
        best_h = best_v = None
        for s in strokes:
            cx, cy = s.centre
            px, py = nominal_to_pdf(cx, cy)
            if abs(px - t["x_pt"]) > radius_pt or abs(py - t["y_pt"]) > radius_pt:
                continue
            w, h = s.extent
            if w > 2 * h and (best_h is None or w > best_h.extent[0]):
                best_h = s
            elif h > 2 * w and (best_v is None or h > best_v.extent[1]):
                best_v = s
        if best_h is None or best_v is None:
            continue
        out.append({"id": t["id"], "x_pt": t["x_pt"], "y_pt": t["y_pt"],
                    "x_rm": best_v.centre[0], "y_rm": best_h.centre[1]})
    return out


def fit_axis(rm: list[float], pdf: list[float]) -> tuple[float, float, float]:
    """Least squares ``pdf = a * rm + b`` -> ``(a, b, rms_residual)``."""
    n = len(rm)
    mx, my = sum(rm) / n, sum(pdf) / n
    sxx = sum((x - mx) ** 2 for x in rm)
    sxy = sum((x - mx) * (y - my) for x, y in zip(rm, pdf))
    a = sxy / sxx
    b = my - a * mx
    rms = (sum((a * x + b - y) ** 2 for x, y in zip(rm, pdf)) / n) ** 0.5
    return a, b, rms


def fit_page(pairs: list[dict], width_pt: float) -> dict:
    ax, bx, rx = fit_axis([p["x_rm"] for p in pairs], [p["x_pt"] for p in pairs])
    ay, by, ry = fit_axis([p["y_rm"] for p in pairs], [p["y_pt"] for p in pairs])
    return {
        "n": len(pairs),
        "x": {"scale": ax, "offset": bx, "rms_pt": rx, "scale_over_fit_width": ax * RM_W / width_pt,
              "offset_in_rm_px": bx / ax},
        "y": {"scale": ay, "offset": by, "rms_pt": ry, "scale_over_fit_width": ay * RM_W / width_pt,
              "offset_in_rm_px": by / ay},
        "nominal_scale": NOMINAL_SCALE,
        "fit_width_scale": width_pt / RM_W,
    }


def fit_pooled(pairs: list[dict], width_pt: float) -> dict:
    """One model for every page: ``x = s * (x_rm + cx)``, ``y = s * y_rm + dy``
    with a single scale shared by both axes and all pages (the device fits the
    page width, so the scale is reported as the width in device px it implies)."""
    import numpy as np

    rows, rhs = [], []
    for p in pairs:
        rows += [[p["x_rm"], 1.0, 0.0], [p["y_rm"], 0.0, 1.0]]
        rhs += [p["x_pt"], p["y_pt"]]
    a = np.array(rows)
    (s, bx, dy), *_ = np.linalg.lstsq(a, np.array(rhs), rcond=None)
    resid = a @ np.array([s, bx, dy]) - np.array(rhs)
    return {"n": len(pairs), "scale": float(s), "fit_width_px": float(width_pt / s),
            "x_centre_px": float(bx / s), "y_offset_px": float(dy / s),
            "rms_pt": float(np.sqrt((resid ** 2).mean())), "max_pt": float(abs(resid).max())}


def strokes_from_rm(rm_bytes: bytes) -> list[Stroke]:
    from remarkable_gtd.rm.annotations import parse_annotations

    return [Stroke([p.x for p in ln.points], [p.y for p in ln.points])
            for ln in parse_annotations(rm_bytes) if len(ln.points) >= 2]


def fit_rmdoc(rmdoc: Path) -> list[dict]:
    import pymupdf

    from remarkable_gtd.rm.annotations import extract_from_rmdoc

    pdf_bytes, rm_by_page = extract_from_rmdoc(rmdoc)
    if not pdf_bytes:
        raise ValueError(f"no PDF inside {rmdoc}")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    spec = json.loads(doc.embfile_get(ATTACH_TARGETS).decode("utf-8"))
    results = []
    for page in spec["pages"]:
        idx = page["page_no"] - 1
        strokes = strokes_from_rm(rm_by_page[idx]) if idx in rm_by_page else []
        pairs = pair_targets(strokes, page["targets"])
        entry = {"page_no": page["page_no"], "height_mm": page["height_mm"], "width_pt": page["width_pt"],
                 "strokes": len(strokes), "paired": len(pairs), "targets": len(page["targets"])}
        if len(pairs) >= 3:
            entry["fit"] = fit_page(pairs, page["width_pt"])
            entry["pairs"] = pairs
        results.append(entry)
    pooled = [p for r in results for p in r.get("pairs", [])]
    if len(pooled) >= 3:
        results.append({"pooled": fit_pooled(pooled, spec["pages"][0]["width_pt"])})
    return results


def _report(results: list[dict]) -> str:
    lines = []
    for r in results:
        if "pooled" in r:
            f = r["pooled"]
            lines.append(
                f"all pages ({f['n']} targets): scale {f['scale']:.6f} pt/px = page width / {f['fit_width_px']:.1f} px, "
                f"x centre {f['x_centre_px']:+.1f} px, y offset {f['y_offset_px']:+.1f} px, "
                f"rms {f['rms_pt']:.2f} pt, max {f['max_pt']:.2f} pt"
            )
            lines.append(f"  -> annotations.py: RM_FIT_WIDTH_PX = {f['fit_width_px']:.1f}, RM_X_CENTRE_PX = {f['x_centre_px']:.1f}")
            continue
        head = f"page {r['page_no']} ({r['height_mm']} mm): {r['strokes']} strokes, {r['paired']}/{r['targets']} targets paired"
        if "fit" not in r:
            lines.append(head + " — not enough to fit")
            continue
        f = r["fit"]
        lines.append(head)
        for axis in ("x", "y"):
            a = f[axis]
            lines.append(
                f"  {axis}: scale {a['scale']:.6f} pt/px ({a['scale'] / f['nominal_scale']:.4%} of 72/226, "
                f"{a['scale_over_fit_width']:.4%} of width/1404), offset {a['offset']:+.2f} pt "
                f"= {a['offset_in_rm_px']:+.1f} px, rms {a['rms_pt']:.2f} pt"
            )
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("make", help="write the target document")
    m.add_argument("--out", default="gtd_calibration.pdf")
    f = sub.add_parser("fit", help="fit the stroke->PDF transform from a traced .rmdoc")
    f.add_argument("rmdoc")
    f.add_argument("--json", help="also write the full results here")
    args = p.parse_args(argv)

    if args.command == "make":
        spec = make_document(Path(args.out))
        print(f"wrote {args.out}: {len(spec['pages'])} pages, "
              f"{sum(len(pg['targets']) for pg in spec['pages'])} targets")
        return 0
    results = fit_rmdoc(Path(args.rmdoc))
    print(_report(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if all("fit" in r for r in results if "pooled" not in r) else 1


if __name__ == "__main__":
    sys.exit(main())
