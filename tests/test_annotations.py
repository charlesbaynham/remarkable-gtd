"""Reading a .rm layer: erased ink is not unreadable ink."""

import io
import uuid

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
