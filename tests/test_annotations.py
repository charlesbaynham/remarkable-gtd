"""Reading a .rm layer: erased ink is not unreadable ink; every layer lands on its own page."""

import io
import json
import uuid
import zipfile

import pytest

pytest.importorskip("rmscene")

from rmscene import write_blocks  # noqa: E402
from rmscene.crdt_sequence import CrdtSequenceItem  # noqa: E402
from rmscene.scene_items import Group  # noqa: E402
from rmscene.scene_stream import (  # noqa: E402
    AuthorIdsBlock,
    MigrationInfoBlock,
    PageInfoBlock,
    SceneGroupItemBlock,
    SceneLineItemBlock,
    SceneTreeBlock,
    TreeNodeBlock,
)
from rmscene.tagged_block_common import CrdtId, LwwValue  # noqa: E402

from remarkable_gtd.rm.annotations import (  # noqa: E402
    UnreadableLayer,
    extract_from_rmdoc,
    page_uuid_to_pdf_index,
    parse_annotations,
    read_annotations,
)

ROOT, LAYER = CrdtId(0, 1), CrdtId(0, 11)


def erased_layer(points: int = 17) -> bytes:
    """One layer holding a single stroke of ``points`` points, rubbed out.

    What the tablet actually writes when you erase: the line item survives with
    no value and the point count in ``deleted_length``. Modelled on a real sheet
    from 2026-09-24 that wedged the pipeline (three such layers, 17 points each).
    """
    blocks = [
        AuthorIdsBlock(author_uuids={1: uuid.uuid4()}),
        MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
        PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
        SceneTreeBlock(tree_id=LAYER, node_id=CrdtId(0, 0), is_update=True, parent_id=ROOT),
        TreeNodeBlock(Group(node_id=ROOT)),
        TreeNodeBlock(Group(node_id=LAYER, label=LwwValue(CrdtId(0, 12), "Layer 1"))),
        SceneGroupItemBlock(
            parent_id=ROOT,
            item=CrdtSequenceItem(
                item_id=CrdtId(0, 13), left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
                deleted_length=0, value=LAYER,
            ),
        ),
        SceneLineItemBlock(
            parent_id=LAYER,
            item=CrdtSequenceItem(
                item_id=CrdtId(1, 16), left_id=CrdtId(0, 0), right_id=CrdtId(0, 0),
                deleted_length=points, value=None,
            ),
        ),
    ]
    buf = io.BytesIO()
    write_blocks(buf, blocks)
    return buf.getvalue()


def test_erased_strokes_read_cleanly_as_no_strokes():
    """The whole point: a rubbed-out sheet is readable and simply empty."""
    assert read_annotations(erased_layer()) == []


def test_unparseable_bytes_raise():
    with pytest.raises(UnreadableLayer):
        read_annotations(b"not a v6 stroke file at all")


def test_parse_annotations_stays_lenient(capsys):
    """The render path still wants whatever ink it can get, and no exception."""
    assert parse_annotations(b"not a v6 stroke file at all") == []
    assert "Error reading blocks" in capsys.readouterr().err
    assert parse_annotations(erased_layer()) == []


# --- .content layouts --------------------------------------------------------------

P0, P1, P2 = "fc06a8f9-p0", "a638a7f8-p1", "8d82bad9-p2"


def test_page_map_format_2():
    content = {"formatVersion": 2, "cPages": {"pages": [
        {"id": P0, "redir": {"value": 0}},
        {"id": "inserted"},  # a page added on the tablet: no redir, falls back to its position
        {"id": P1, "redir": {"value": 1}},
    ]}}
    assert page_uuid_to_pdf_index(content) == {P0: 0, "inserted": 1, P1: 1}


def test_page_map_format_1():
    """The layout a sheet really arrived in on 2026-09-29, which used to map to nothing."""
    content = {"formatVersion": 1, "pages": [P0, P1, "blank", P2], "redirectionPageMap": [0, 1, -1, 2]}
    assert page_uuid_to_pdf_index(content) == {P0: 0, P1: 1, P2: 2}


def test_page_map_format_1_without_redirects():
    assert page_uuid_to_pdf_index({"pages": [P0, P1]}) == {P0: 0, P1: 1}


def test_format_1_rmdoc_keeps_every_page(tmp_path):
    """Ink on two pages of a v1 sheet: both layers come back, each on its page.

    Before the fix every layer fell back to page 0 and the last one written
    won, so the Next Actions ink on page 2 silently vanished.
    """
    doc = "318ea23b"
    content = {"formatVersion": 1, "pages": [P0, P1, P2], "redirectionPageMap": [0, 1, 2]}
    path = tmp_path / "sheet.rmdoc"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"{doc}.pdf", b"%PDF-1.4 stub")
        z.writestr(f"{doc}.content", json.dumps(content))
        z.writestr(f"{doc}/{P1}.rm", b"page two")
        z.writestr(f"{doc}/{P0}.rm", b"page one")
        z.writestr(f"{doc}/stray.rm", b"no such page")
    _pdf, rm_by_page = extract_from_rmdoc(path)
    assert rm_by_page == {0: b"page one", 1: b"page two"}
