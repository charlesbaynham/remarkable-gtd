"""Render reMarkable v6 annotations onto a PDF using rmscene + PyMuPDF."""

from __future__ import annotations

import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None  # type: ignore[assignment]

try:
    from rmscene import SceneLineItemBlock, read_blocks
    from rmscene.scene_items import PenColor
except ImportError:
    read_blocks = None  # type: ignore[assignment]
    SceneLineItemBlock = None  # type: ignore[assignment,misc]
    PenColor = None  # type: ignore[assignment,misc]


def _check_deps() -> None:
    if fitz is None:
        raise ImportError(
            "PyMuPDF (fitz) is required. Install with: pip install PyMuPDF"
        )
    if read_blocks is None:
        raise ImportError("rmscene is required. Install with: pip install rmscene")


def extract_from_rmdoc(
    rmdoc_path: Path,
) -> tuple[bytes | None, dict[int, bytes]]:
    """Extract PDF and all .rm files from an rmdoc zip.

    Returns:
        (pdf_bytes, {page_index: rm_bytes})
    """
    with zipfile.ZipFile(rmdoc_path, "r") as z:
        pdf_name = None
        rm_files = {}
        content_name = None
        for name in z.namelist():
            if name.endswith(".pdf"):
                pdf_name = name
            elif name.endswith(".content"):
                content_name = name
            elif name.endswith(".rm"):
                rm_files[name] = z.read(name)

        pdf_bytes = z.read(pdf_name) if pdf_name else None

        if not content_name or not rm_files:
            # Fallback: return first rm file as page 0
            first_rm = next(iter(rm_files.values())) if rm_files else None
            return pdf_bytes, {0: first_rm} if first_rm else {}

        content = json.loads(z.read(content_name))
        pages = content.get("pages", [])
        # Build UUID -> page_index mapping
        uuid_to_index = {uuid: i for i, uuid in enumerate(pages)}

        # Map rm files to page indices
        rm_by_page = {}
        for rm_name, rm_bytes in rm_files.items():
            # rm_name format: {doc_uuid}/{page_uuid}.rm
            page_uuid = Path(rm_name).stem
            page_index = uuid_to_index.get(page_uuid, 0)
            rm_by_page[page_index] = rm_bytes

        return pdf_bytes, rm_by_page


def parse_annotations(rm_bytes: bytes) -> list:
    """Parse v6 annotations from .rm file bytes."""
    _check_deps()
    lines = []
    with BytesIO(rm_bytes) as f:
        try:
            blocks = read_blocks(f)
            for block in blocks:
                if isinstance(block, SceneLineItemBlock):
                    item = block.item
                    if item and item.value:
                        lines.append(item.value)
        except Exception as e:
            print(f"Warning: Error reading blocks: {e}", file=sys.stderr)
    return lines


def _color_for_pen(color_enum) -> tuple[float, float, float]:
    """Map PenColor to RGB tuple (0-1 range)."""
    mapping = {
        PenColor.BLACK: (0, 0, 0),
        PenColor.GRAY: (0.5, 0.5, 0.5),
        PenColor.WHITE: (1, 1, 1),
    }
    return mapping.get(color_enum, (0, 0, 0))


def render_annotations(
    doc: fitz.Document,
    page_index: int,
    lines: list,
) -> None:
    """Render annotation lines onto a specific PDF page."""
    _check_deps()
    page = doc[page_index]

    # reMarkable 2 dimensions in pixels
    rm_width = 1404
    rm_height = 1872

    pdf_rect = page.rect
    pdf_width = pdf_rect.width
    pdf_height = pdf_rect.height

    scale_x = pdf_width / rm_width
    scale_y = pdf_height / rm_height

    for line in lines:
        color = _color_for_pen(line.color)
        points = line.points
        if len(points) < 2:
            continue

        fitz_points = []
        for p in points:
            x = p.x * scale_x
            y = p.y * scale_y
            fitz_points.append((x, y))

        shape = page.new_shape()
        shape.draw_polyline(fitz_points)
        avg_width = sum(p.width for p in points) / len(points) * scale_x * 0.3
        shape.finish(color=color, width=max(avg_width, 0.5))
        shape.commit()


def pdf_to_images(
    pdf_path: Path,
    output_dir: Path,
    dpi: int = 226,
    scale: int = 2,
) -> list[Path]:
    """Convert PDF pages to PNG images.

    Args:
        pdf_path: Path to PDF file.
        output_dir: Directory to save images.
        dpi: Resolution for rendering.
        scale: Additional scale factor (2x for 2x resolution).

    Returns:
        List of paths to generated PNG files.
    """
    _check_deps()
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    paths = []

    for i, page in enumerate(doc):
        mat = fitz.Matrix(dpi / 72 * scale, dpi / 72 * scale)
        pix = page.get_pixmap(matrix=mat)
        img_path = output_dir / f"page_{i + 1}.png"
        pix.save(str(img_path))
        paths.append(img_path)

    doc.close()
    return paths


def render_rmdoc(rmdoc_path: Path, output_pdf: Path) -> None:
    """Render annotations from an rmdoc file onto a flat PDF.

    If no annotations are present, the clean PDF is copied.
    """
    pdf_bytes, rm_by_page = extract_from_rmdoc(rmdoc_path)
    if not pdf_bytes:
        raise ValueError(f"No PDF found in rmdoc: {rmdoc_path}")

    if not rm_by_page:
        # No annotations — just copy the PDF
        output_pdf.write_bytes(pdf_bytes)
        return

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page_index, rm_bytes in sorted(rm_by_page.items()):
        if page_index >= len(doc):
            print(
                f"Warning: page index {page_index} out of range ({len(doc)} pages)",
                file=sys.stderr,
            )
            continue
        lines = parse_annotations(rm_bytes)
        if lines:
            render_annotations(doc, page_index, lines)

    doc.save(str(output_pdf))
    doc.close()
