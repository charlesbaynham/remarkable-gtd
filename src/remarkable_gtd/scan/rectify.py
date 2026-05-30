"""Registration mark detection and perspective rectification."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    pass


class RegistrationError(Exception):
    pass


def find_reg_marks(binary: np.ndarray) -> dict[str, tuple[float, float]]:
    """Finds the 4 cross-shaped reg marks in the four corner quadrants.
    Returns {"tl": (cx, cy), "tr": ..., "bl": ..., "br": ...} in pixel coords.
    Raises RegistrationError if fewer than 4 found."""
    h, w = binary.shape[:2]
    # Search a 15%-of-dimensions window from each corner
    cw = int(w * 0.15)
    ch = int(h * 0.15)
    regions = {
        "tl": (0, ch, 0, cw),
        "tr": (0, ch, w - cw, w),
        "bl": (h - ch, h, 0, cw),
        "br": (h - ch, h, w - cw, w),
    }
    marks = {}
    for name, (y0, y1, x0, x1) in regions.items():
        region = binary[y0:y1, x0:x1]
        if region.size == 0:
            continue
        # Invert so black marks become white foreground
        inv = 255 - region
        contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_score = -1.0
        best_center = None
        for cnt in contours:
            bx, by, bw, bh = cv2.boundingRect(cnt)
            if bw == 0 or bh == 0:
                continue
            aspect = bw / bh
            if not (0.6 <= aspect <= 1.4):
                continue
            area = bw * bh
            if area < 100 or area > 8000:
                continue
            # Hard proximity: bbox must intersect the 80 px corner region
            CORNER_R = 80
            if name == "tl" and (bx >= CORNER_R or by >= CORNER_R):
                continue
            if name == "tr" and (
                bx + bw <= region.shape[1] - CORNER_R or by >= CORNER_R
            ):
                continue
            if name == "bl" and (
                bx >= CORNER_R or by + bh <= region.shape[0] - CORNER_R
            ):
                continue
            if name == "br" and (
                bx + bw <= region.shape[1] - CORNER_R
                or by + bh <= region.shape[0] - CORNER_R
            ):
                continue
            # Check plus-ness: high fill in center bands, low in corners
            cx = bx + bw // 2
            cy = by + bh // 2
            h_band = inv[cy - max(1, bh // 6) : cy + max(1, bh // 6), bx : bx + bw]
            v_band = inv[by : by + bh, cx - max(1, bw // 6) : cx + max(1, bw // 6)]
            if h_band.size == 0 or v_band.size == 0:
                continue
            h_fill = np.mean(h_band > 128) if h_band.size else 0
            v_fill = np.mean(v_band > 128) if v_band.size else 0
            corners = [
                inv[by : by + bh // 3, bx : bx + bw // 3],
                inv[by : by + bh // 3, bx + 2 * bw // 3 : bx + bw],
                inv[by + 2 * bh // 3 : by + bh, bx : bx + bw // 3],
                inv[by + 2 * bh // 3 : by + bh, bx + 2 * bw // 3 : bx + bw],
            ]
            corner_fill = (
                np.mean([np.mean(c > 128) for c in corners if c.size > 0])
                if any(c.size > 0 for c in corners)
                else 1.0
            )
            # Proximity to corner: prefer marks near the corner of the search region
            if name == "tl":
                corner_dist = np.sqrt(bx**2 + by**2)
            elif name == "tr":
                corner_dist = np.sqrt((region.shape[1] - bx) ** 2 + by**2)
            elif name == "bl":
                corner_dist = np.sqrt(bx**2 + (region.shape[0] - by) ** 2)
            else:  # br
                corner_dist = np.sqrt(
                    (region.shape[1] - bx) ** 2 + (region.shape[0] - by) ** 2
                )
            max_dist = np.sqrt(region.shape[1] ** 2 + region.shape[0] ** 2)
            proximity = 1.0 - corner_dist / max_dist
            score = (h_fill + v_fill) * 0.4 - corner_fill * 0.2 + proximity * 0.4
            if score > best_score:
                best_score = score
                M = cv2.moments(cnt)
                if M["m00"] > 0:
                    qcx = M["m10"] / M["m00"]
                    qcy = M["m01"] / M["m00"]
                else:
                    qcx = bx + bw / 2
                    qcy = by + bh / 2
                best_center = (qcx + x0, qcy + y0)
        if best_center is not None:
            marks[name] = best_center
    if len(marks) < 4:
        raise RegistrationError(f"Only {len(marks)}/4 registration marks found")
    return marks


def rectify(
    gray: np.ndarray, binary: np.ndarray, marks: dict, manifest_page: dict
) -> tuple:
    """Warp image so page corners align with manifest reg-mark positions.
    Returns (warped_gray, warped_binary, (canvas_w, canvas_h), residual_px)."""
    rois = manifest_page.get("rois", {})
    reg_keys = ["reg:tl", "reg:tr", "reg:bl", "reg:br"]
    for k in reg_keys:
        if k not in rois:
            raise RegistrationError(f"Missing {k} in manifest")

    render = manifest_page["render"]
    canvas_w = 1404
    canvas_h = int(round(canvas_w * render["h_px"] / render["w_px"]))

    src_pts = np.array([marks[k] for k in ["tl", "tr", "bl", "br"]], dtype=np.float32)
    dst_pts = np.array(
        [
            (
                (rois[f"reg:{k}"]["x"] + rois[f"reg:{k}"]["w"] / 2) * canvas_w,
                (rois[f"reg:{k}"]["y"] + rois[f"reg:{k}"]["h"] / 2) * canvas_h,
            )
            for k in ["tl", "tr", "bl", "br"]
        ],
        dtype=np.float32,
    )

    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped_gray = cv2.warpPerspective(
        gray, M, (canvas_w, canvas_h), flags=cv2.INTER_LINEAR
    )
    warped_binary = cv2.warpPerspective(
        binary, M, (canvas_w, canvas_h), flags=cv2.INTER_NEAREST
    )

    # Compute residual
    warped_marks = cv2.perspectiveTransform(src_pts.reshape(1, -1, 2), M).reshape(-1, 2)
    residual = float(np.mean(np.linalg.norm(warped_marks - dst_pts, axis=1)))

    return warped_gray, warped_binary, (canvas_w, canvas_h), residual
