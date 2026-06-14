"""Registration mark detection and perspective rectification.

The printed registration marks (``.reg`` in gtd.css) are **L-shaped corner
brackets**: a 5 mm x 0.6 mm horizontal bar and a 0.6 mm x 5 mm vertical bar
hugging the two page-facing edges of a 5 mm x 5 mm box, one per page corner
(tl/tr/bl/br). They are NOT crosses — detection scores "L-ness" along the
two expected edges of each candidate's bounding box.

The reference point for the homography is the candidate's **bounding-box
centre** (the bars span the full 5 mm extent, so the bbox centre coincides
with the centre of the manifest ``reg:*`` ROI). The contour mass centroid
must not be used: for an L it is biased toward the corner.
"""
from __future__ import annotations

import numpy as np


class RegistrationError(Exception):
    """Raised when fewer than 4 registration marks are found."""


# Which edges of its bounding box each corner's bracket occupies.
_EDGES = {
    "tl": ("top", "left"),
    "tr": ("top", "right"),
    "bl": ("bottom", "left"),
    "br": ("bottom", "right"),
}


def _edge_band_fill(dark: np.ndarray, edge: str) -> float:
    """Max per-row (or per-column) dark fill within the band along ``edge``.

    The bracket bar lies flush against one edge of the bbox and spans its
    full length, so the best row/column in the outer-third band should be
    almost entirely dark.
    """
    h, w = dark.shape
    third_h = max(1, h // 3)
    third_w = max(1, w // 3)
    if edge == "top":
        band = dark[:third_h, :]
        profile = band.mean(axis=1)  # per-row fill
    elif edge == "bottom":
        band = dark[-third_h:, :]
        profile = band.mean(axis=1)
    elif edge == "left":
        band = dark[:, :third_w]
        profile = band.mean(axis=0)  # per-column fill
    else:  # right
        band = dark[:, -third_w:]
        profile = band.mean(axis=0)
    return float(profile.max()) if profile.size else 0.0


def find_reg_marks(binary: np.ndarray) -> dict[str, tuple[float, float]]:
    """Find the 4 L-bracket registration marks in the four corner quadrants.

    Args:
        binary: Uint8 binary image (0 = dark/ink, 255 = light/paper — as
            from Otsu threshold). Shape (H, W).

    Returns:
        Dict with keys ``"tl"``, ``"tr"``, ``"bl"``, ``"br"``, each mapping
        to the ``(cx, cy)`` bounding-box centre in source-image pixels.

    Raises:
        RegistrationError: If any corner has no valid candidate.
    """
    import cv2

    H, W = binary.shape[:2]
    qh = H // 4
    qw = W // 4

    # Plausible bracket size: ~5mm of a ~157.8mm-wide page => ~3.2% of width.
    min_size = max(6, int(W * 0.012))
    max_size = int(W * 0.07)

    quadrants = {
        "tl": (0, 0),
        "tr": (W - qw, 0),
        "bl": (0, H - qh),
        "br": (W - qw, H - qh),
    }
    corners = {
        "tl": (0.0, 0.0),
        "tr": (float(W), 0.0),
        "bl": (0.0, float(H)),
        "br": (float(W), float(H)),
    }
    quad_diag = float(np.hypot(qw, qh))

    marks: dict[str, tuple[float, float]] = {}

    for name, (ox, oy) in quadrants.items():
        region = binary[oy : oy + qh, ox : ox + qw]
        inv = cv2.bitwise_not(region)  # findContours wants bright-on-dark
        contours, _ = cv2.findContours(inv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        edge_a, edge_b = _EDGES[name]
        best_score = -1.0
        best_center: tuple[float, float] | None = None

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if not (min_size <= w <= max_size and min_size <= h <= max_size):
                continue
            aspect = w / h
            if not (0.6 < aspect < 1.7):
                continue

            sub = region[y : y + h, x : x + w]
            dark = sub < 128

            # An L bracket is two thin bars: total fill is low (~0.2),
            # ruling out solid blobs like QR finder patterns.
            total_fill = float(dark.mean())
            if not (0.05 < total_fill < 0.55):
                continue

            fill_a = _edge_band_fill(dark, edge_a)
            fill_b = _edge_band_fill(dark, edge_b)
            # Bars span the full bbox along their edge: near-solid best line.
            if fill_a < 0.7 or fill_b < 0.7:
                continue

            # The opposite edges should NOT contain a solid bar.
            opp = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
            fill_oa = _edge_band_fill(dark, opp[edge_a])
            fill_ob = _edge_band_fill(dark, opp[edge_b])

            # The bracket is by design the mark closest to the page corner;
            # penalise candidates that sit deeper into the page.
            cx = float(ox + x + w / 2)
            cy = float(oy + y + h / 2)
            kx, ky = corners[name]
            corner_dist = float(np.hypot(cx - kx, cy - ky)) / quad_diag

            score = (
                (fill_a + fill_b)
                - 0.5 * (fill_oa + fill_ob)
                - total_fill
                - 1.5 * corner_dist
            )
            if score > best_score:
                best_score = score
                best_center = (cx, cy)

        if best_center is not None:
            marks[name] = best_center

    if len(marks) < 4:
        found = list(marks.keys())
        missing = [k for k in ("tl", "tr", "bl", "br") if k not in marks]
        raise RegistrationError(
            f"Only found reg marks in corners {found}; missing: {missing}"
        )

    return marks


def rectify(
    gray: np.ndarray,
    binary: np.ndarray,
    marks: dict,
    manifest_page: dict,
    canvas_width: int = 1404,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int], float | None]:
    """Warp image so its reg marks align with the manifest reg-mark positions.

    Destination points come from the manifest ``reg:tl/tr/bl/br`` ROI centres
    scaled to a canonical canvas (width ``canvas_width`` px; height derived
    from the manifest render aspect ratio). After warping, the output frame
    matches the manifest's normalized frame: ``roi * canvas`` gives exact
    pixel rectangles.

    Args:
        gray: Grayscale source image (uint8).
        binary: Binarised source image (uint8, same size as gray).
        marks: Output of :func:`find_reg_marks` — ``{"tl": (cx,cy), ...}``.
        manifest_page: Single page entry from the manifest
            (has ``render`` and ``rois`` keys).
        canvas_width: Output canvas width in pixels (reMarkable 2 panel).

    Returns:
        Tuple of:
        - warped_gray (np.ndarray)
        - warped_binary (np.ndarray)
        - (canvas_w, canvas_h) ints
        - residual_px (float | None) — mean distance between reg marks
          *re-detected in the warped image* and their expected positions;
          ``None`` if re-detection failed (quality metric only).
    """
    import cv2

    render = manifest_page["render"]
    rois = manifest_page["rois"]

    canvas_w = canvas_width
    canvas_h = int(round(canvas_w * render["h_px"] / render["w_px"]))

    def dest_pt(key: str) -> list[float]:
        roi = rois[key]
        cx = (roi["x"] + roi["w"] / 2) * canvas_w
        cy = (roi["y"] + roi["h"] / 2) * canvas_h
        return [cx, cy]

    dst_pts = np.array(
        [dest_pt("reg:tl"), dest_pt("reg:tr"), dest_pt("reg:bl"), dest_pt("reg:br")],
        dtype=np.float32,
    )
    src_pts = np.array(
        [marks["tl"], marks["tr"], marks["bl"], marks["br"]],
        dtype=np.float32,
    )

    M = cv2.getPerspectiveTransform(src_pts, dst_pts)

    warped_gray = cv2.warpPerspective(
        gray, M, (canvas_w, canvas_h),
        flags=cv2.INTER_LINEAR, borderValue=255,
    )
    warped_binary = cv2.warpPerspective(
        binary, M, (canvas_w, canvas_h),
        flags=cv2.INTER_NEAREST, borderValue=255,
    )

    # Quality metric: re-detect the marks in the warped image and measure
    # how far they land from their expected manifest positions. (Projecting
    # the original 4 source points would be tautologically ~0.)
    residual_px: float | None
    try:
        remarks = find_reg_marks(warped_binary)
        re_pts = np.array(
            [remarks["tl"], remarks["tr"], remarks["bl"], remarks["br"]],
            dtype=np.float32,
        )
        residual_px = float(np.mean(np.linalg.norm(re_pts - dst_pts, axis=1)))
    except RegistrationError:
        residual_px = None

    return warped_gray, warped_binary, (canvas_w, canvas_h), residual_px
