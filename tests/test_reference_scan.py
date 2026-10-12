"""Scanning a reference sheet: strokes drawn into a synthetic .rmdoc, read back as decisions."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

pytest.importorskip("rmscene")

from rmscene import write_blocks  # noqa: E402
from rmscene.crdt_sequence import CrdtSequenceItem  # noqa: E402
from rmscene.scene_items import Group, Line, Pen, PenColor, Point  # noqa: E402
from rmscene.scene_stream import (  # noqa: E402
    AuthorIdsBlock, MigrationInfoBlock, PageInfoBlock, SceneGroupItemBlock,
    SceneLineItemBlock, SceneTreeBlock, TreeNodeBlock,
)
from rmscene.tagged_block_common import CrdtId, LwwValue  # noqa: E402

from remarkable_gtd.rm.annotations import RM_FIT_WIDTH_PX, RM_X_CENTRE_PX, render_strokes_only
from remarkable_gtd.scan.pipeline import ScanConfig
from remarkable_gtd.scan.sheet import scan_pdf, scan_rmdoc, summarize
from tests.conftest import needs_chromium

pytestmark = needs_chromium

ROOT, LAYER = CrdtId(0, 1), CrdtId(0, 11)
PEN_WIDTH = 20


def stroke_layer(polylines: list[list[tuple[float, float]]]) -> bytes:
    """A v6 layer of black ballpoint strokes; points are in device stroke space."""
    blocks = [
        AuthorIdsBlock(author_uuids={1: __import__("uuid").uuid4()}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(tree_id=LAYER, node_id=CrdtId(0, 0), is_update=True, parent_id=ROOT),
        TreeNodeBlock(Group(node_id=ROOT)),
        TreeNodeBlock(Group(node_id=LAYER, label=LwwValue(CrdtId(0, 12), "Layer 1"))),
        SceneGroupItemBlock(parent_id=ROOT, item=CrdtSequenceItem(
            item_id=CrdtId(0, 13), left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
            deleted_length=0, value=LAYER)),
    ]
    for n, points in enumerate(polylines):
        line = Line(
            color=PenColor.BLACK, tool=Pen.BALLPOINT_1, thickness_scale=2.0, starting_length=0,
            points=[Point(x=x, y=y, speed=0, direction=0, width=PEN_WIDTH, pressure=100) for x, y in points],
        )
        blocks.append(SceneLineItemBlock(parent_id=LAYER, item=CrdtSequenceItem(
            item_id=CrdtId(1, 20 + n), left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
            deleted_length=0, value=line)))
    buf = io.BytesIO()
    write_blocks(buf, blocks)
    return buf.getvalue()


class Ink:
    """Strokes placed by manifest ROI, in device stroke space for a page of known size."""

    def __init__(self, pdf_path):
        import pymupdf

        with pymupdf.open(str(pdf_path)) as doc:
            self.w, self.h = doc[0].rect.width, doc[0].rect.height

    def point(self, fx: float, fy: float) -> tuple[float, float]:
        return fx * RM_FIT_WIDTH_PX - RM_X_CENTRE_PX, fy * self.h / self.w * RM_FIT_WIDTH_PX

    def cross(self, roi: dict) -> list[list[tuple[float, float]]]:
        x0, y0, x1, y1 = roi["x"], roi["y"], roi["x"] + roi["w"], roi["y"] + roi["h"]
        return [[self.point(x0, y0), self.point(x1, y1)], [self.point(x0, y1), self.point(x1, y0)]]

    def scribble(self, roi: dict, rows: int = 4) -> list[list[tuple[float, float]]]:
        x0, x1 = roi["x"] + roi["w"] * 0.1, roi["x"] + roi["w"] * 0.6
        ys = [roi["y"] + roi["h"] * (0.2 + 0.6 * i / (rows - 1)) for i in range(rows)]
        zig = []
        for i, y in enumerate(ys):
            zig += [self.point(x0, y), self.point(x1, y)] if i % 2 == 0 else [self.point(x1, y), self.point(x0, y)]
        return [zig]


def make_rmdoc(path, pdf_path, layers: dict[int, list]) -> None:
    page_ids = [f"page-{i}" for i in range(len(layers) and max(layers) + 1)]
    import pymupdf

    with pymupdf.open(str(pdf_path)) as doc:
        page_ids = [f"page-{i}" for i in range(len(doc))]
    content = {"formatVersion": 2, "cPages": {"pages": [
        {"id": pid, "redir": {"value": i}} for i, pid in enumerate(page_ids)]}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("doc.pdf", pdf_path.read_bytes())
        z.writestr("doc.content", json.dumps(content))
        for index, polylines in layers.items():
            z.writestr(f"doc/{page_ids[index]}.rm", stroke_layer(polylines))


class RefEngine:
    name = "ref-recording"

    def __init__(self, reading=None):
        self.reading = reading
        self.reads: list[tuple[str, tuple[int, int]]] = []
        self.interpretations: list[tuple[tuple[int, int], dict, str]] = []

    def read(self, image, hint=None, context=None):
        self.reads.append((hint, image.shape[:2]))
        return f"<{hint}>"

    def interpret_reference(self, image, note, today=None):
        self.interpretations.append((image.shape[:2], note, today))
        return self.reading


READING = {
    "handwriting": "check with Dave", "understood": True, "confidence": 0.9, "note": "margin note",
    "operations": [{"op": "reference_append", "text": "check with Dave", "old": None, "new": None, "heading": None}],
}


@pytest.fixture()
def sheet(reference_sheet):
    pdf, result, _vault = reference_sheet
    keys = list(result["manifest"]["pages"])
    return pdf, result["manifest"], keys, Ink(pdf)


def page_rois(manifest, keys, n):
    return manifest["pages"][keys[n - 1]]["rois"]


def test_blank_sheet_reads_clean(sheet, tmp_path):
    pdf, manifest, keys, _ink = sheet
    make_rmdoc(tmp_path / "s.rmdoc", pdf, {})
    decisions, *_ = scan_rmdoc(tmp_path / "s.rmdoc", tmp_path / "w", ScanConfig())
    assert [p["page_key"] for p in decisions["pages"]] == keys
    for number, page in enumerate(decisions["pages"], start=1):
        assert "error" not in page, page
        assert page["header_qr"] == keys[number - 1]
        assert page["bucket"] == ("refnotes" if number == len(keys) else "reference")
        assert page["note"]["stem"] == "Flat hot water system"
        assert page["ai"] is False and page["ink_elsewhere"] is False and page["ink_fill"] == 0
        assert "ink_approximate" not in page and page["warnings"] == []
    last = decisions["pages"][-1]
    assert [t["id"] for t in last["tasks"]] == [f"RF-C{i}" for i in range(1, 7)]
    assert all(t["inked"] is False and t["qr_verified"] for t in last["tasks"])
    assert summarize(decisions)["actions"] == 0


def test_ai_tick_ink_elsewhere_and_notes_line(sheet, tmp_path):
    pdf, manifest, keys, ink = sheet
    last = len(keys)
    make_rmdoc(tmp_path / "s.rmdoc", pdf, {
        1: ink.cross(page_rois(manifest, keys, 2)["page:ai"]) + ink.scribble(page_rois(manifest, keys, 2)["page:body"]),
        2: ink.scribble(page_rois(manifest, keys, 3)["page:body"]),
        last - 1: ink.scribble(page_rois(manifest, keys, last)["RF-C1:act"], rows=2),
    })
    engine = RefEngine(READING)
    decisions, *_ = scan_rmdoc(tmp_path / "s.rmdoc", tmp_path / "w", ScanConfig(ocr_engine=engine))
    pages = decisions["pages"]

    assert pages[0]["ai"] is False and pages[0]["ink_elsewhere"] is False

    assert pages[1]["ai"] is True and pages[1]["ink_elsewhere"] is True
    assert pages[1]["ai_reading"] == READING
    assert pages[2]["ai"] is False and pages[2]["ink_elsewhere"] is True and "ai_reading" not in pages[2]

    (shape, note, today), = engine.interpretations
    assert shape[1] == 2 * 1404                       # annotated page beside the handwriting alone
    assert note["side_by_side"] is True and note["page_no"] == 2 and note["page_count"] == last
    assert note["stem"] == "Flat hot water system" and "Paragraph" in note["page_text"]
    assert today == "2026-10-12"

    rows = {t["id"]: t for t in pages[-1]["tasks"]}
    assert rows["RF-C1"]["inked"] is True and rows["RF-C1"]["act_text"] == "<capture>"
    assert rows["RF-C2"]["inked"] is False and "act_text" not in rows["RF-C2"]
    assert ("capture", ) == tuple({hint for hint, _ in engine.reads})
    assert pages[-1]["ink_elsewhere"] is False        # the notes lines are not "elsewhere"


def test_ai_with_a_null_engine_warns_instead_of_guessing(sheet, tmp_path):
    pdf, manifest, keys, ink = sheet
    make_rmdoc(tmp_path / "s.rmdoc", pdf, {0: ink.cross(page_rois(manifest, keys, 1)["page:ai"])})
    decisions, *_ = scan_rmdoc(tmp_path / "s.rmdoc", tmp_path / "w", ScanConfig())
    page = decisions["pages"][0]
    assert page["ai"] is True and "ai_reading" not in page
    assert any("no reading" in w for w in page["warnings"])


def test_failed_interpretation_is_recorded_not_raised(sheet, tmp_path):
    pdf, manifest, keys, ink = sheet
    make_rmdoc(tmp_path / "s.rmdoc", pdf, {0: ink.cross(page_rois(manifest, keys, 1)["page:ai"])})

    class Broken(RefEngine):
        def interpret_reference(self, image, note, today=None):
            raise RuntimeError("boom")

    decisions, *_ = scan_rmdoc(tmp_path / "s.rmdoc", tmp_path / "w", ScanConfig(ocr_engine=Broken()))
    page = decisions["pages"][0]
    assert page["ai_reading"]["understood"] is False and "boom" in page["ai_reading"]["note"]
    assert any("boom" in w for w in page["warnings"])


def test_plain_pdf_scan_is_approximate(sheet, tmp_path):
    """Without a strokes layer the AI tick still reads; body ink is flagged approximate."""
    import pymupdf

    pdf, manifest, keys, _ink = sheet
    marked = tmp_path / "marked.pdf"
    with pymupdf.open(str(pdf)) as doc:
        roi = page_rois(manifest, keys, 1)["page:ai"]
        page = doc[0]
        w, h = page.rect.width, page.rect.height
        x0, y0, x1, y1 = roi["x"] * w, roi["y"] * h, (roi["x"] + roi["w"]) * w, (roi["y"] + roi["h"]) * h
        page.draw_line((x0, y0), (x1, y1), width=2)
        page.draw_line((x0, y1), (x1, y0), width=2)
        doc.save(str(marked))
    decisions = scan_pdf(marked, manifest, ScanConfig(), tmp_path / "w")
    first, second = decisions["pages"][0], decisions["pages"][1]
    assert first["ai"] is True and second["ai"] is False
    assert first["ink_approximate"] is True and first["ink_elsewhere"] is False


def test_render_strokes_only_keeps_page_sizes_and_drops_print(sheet, tmp_path):
    import pymupdf

    pdf, manifest, keys, ink = sheet
    make_rmdoc(tmp_path / "s.rmdoc", pdf, {0: ink.cross(page_rois(manifest, keys, 1)["page:ai"])})
    out = tmp_path / "strokes.pdf"
    render_strokes_only(tmp_path / "s.rmdoc", out)
    with pymupdf.open(str(out)) as strokes, pymupdf.open(str(pdf)) as original:
        assert [p.rect for p in strokes] == [p.rect for p in original]
        assert strokes[0].get_text().strip() == ""
        dark = np.frombuffer(strokes[0].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8) < 128
        blank = np.frombuffer(strokes[1].get_pixmap(dpi=50, colorspace=pymupdf.csGRAY).samples, np.uint8) < 128
        assert dark.any() and not blank.any()
