"""Reference-note renderer: markdown -> device-sized PDF with chrome, ROIs and embedded state."""
from __future__ import annotations

import hashlib

import pytest

from remarkable_gtd.common.embedded import read_state
from remarkable_gtd.common.schema import is_reference_key, make_reference_key, parse_page_key
from remarkable_gtd.gen.obsidian_md import render_markdown, strip_front_matter
from remarkable_gtd.gen.reference import Attachments, note_title
from tests.conftest import REFERENCE_NOTE, needs_chromium

STEM = "Flat hot water system"


def test_reference_key_round_trip():
    key = make_reference_key(STEM, 3, "d8429acb6323ffff")
    assert key == f"REF|{STEM}#3|d8429acb6323" and is_reference_key(key)
    assert parse_page_key(key) == {
        "prefix": "REF", "bucket": f"{STEM}#3", "date": "d8429acb6323",
        "stem": STEM, "page": 3, "sha": "d8429acb6323",
    }
    assert not is_reference_key("GTD|inbox|2026-05-30")
    assert parse_page_key("GTD|inbox|2026-05-30")["bucket"] == "inbox"


def test_reference_key_rejects_separator_and_bad_keys():
    with pytest.raises(ValueError):
        make_reference_key("a|b", 1, "abc")
    with pytest.raises(ValueError):
        parse_page_key("REF|nopage|abc")


def test_markdown_obsidian_features():
    html = render_markdown(
        "---\na: 1\n---\nSee [[Foo|bar]] and [[Baz#h]] ![[x.png|200]] <b>x</b> a<br>b\n\n"
        "- [ ] open\n- [x] shut\n",
        lambda target, width: f"<EMBED {target} {width}>",
    )
    assert 'class="wl">bar<' in html and 'class="wl">Baz#h<' in html
    assert "<EMBED x.png 200>" in html
    assert "&lt;b&gt;" in html and "a<br>b" in html
    assert 'class="chk"' in html and 'class="chk done"' in html
    assert "a: 1" not in html


def test_strip_front_matter_and_title():
    assert strip_front_matter("---\nk: v\n---\n# T\n") == "# T\n"
    assert note_title("---\nk: v\n---\n# Real title\nbody", "stem") == "Real title"
    assert note_title("no heading", "stem") == "stem"


def test_attachments_missing_and_traversal(tmp_path):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "secret.png").write_bytes(b"x")
    (tmp_path / "attachments" / "mail.msg").write_bytes(b"x")
    a = Attachments(tmp_path)
    assert "missing: nope.png" in a.embed("nope.png")
    assert "missing: ../secret.png" in a.embed("../secret.png")
    assert "attachment: mail.msg" in a.embed("mail.msg")
    assert "embedded note: Other" in a.embed("Other")


def test_attachment_image_downscaled(tmp_path):
    from PIL import Image
    import base64, io, re

    (tmp_path / "attachments").mkdir()
    Image.new("RGB", (4000, 2000), "white").save(tmp_path / "attachments" / "big.png")
    html = Attachments(tmp_path).embed("big.png")
    uri = re.search(r'src="data:image/jpeg;base64,([^"]+)"', html)[1]
    assert max(Image.open(io.BytesIO(base64.b64decode(uri))).size) <= 1404


@needs_chromium
def test_render_pages_manifest_and_state(reference_sheet):
    import pymupdf

    pdf, result, _vault = reference_sheet
    manifest, tasks = result["manifest"], result["tasks"]
    sha = hashlib.sha256(REFERENCE_NOTE.encode()).hexdigest()

    doc = pymupdf.open(str(pdf))
    assert len(doc) == result["page_count"] >= 4          # 3+ pages of body, then the notes page
    assert abs(doc[0].rect.width - 157.8 / 25.4 * 72) < 0.5
    assert abs(doc[0].rect.height - 210.4 / 25.4 * 72) < 0.5

    keys = list(manifest["pages"])
    assert keys == [make_reference_key(STEM, n, sha) for n in range(1, len(doc) + 1)]
    assert manifest["schema"] == "gtd.manifest/1" and manifest["doc"] == "reference"
    assert manifest["note"] == {
        "stem": STEM, "path": f"Reference/{STEM}.md", "source_sha": sha, "title": STEM,
    }
    for n, key in enumerate(keys, start=1):
        page = manifest["pages"][key]
        last = n == len(keys)
        assert page["bucket"] == ("refnotes" if last else "reference")
        assert page["page_no"] == n and page["scan"] is True
        assert {"reg:tl", "reg:tr", "reg:bl", "reg:br", "page:qr", "page:ai", "page:body"} <= set(page["rois"])
        notes_rois = {k for k in page["rois"] if k.startswith("RF-C")}
        assert (len(notes_rois) == 6 * 3) == last and (not notes_rois or last)
        assert f"page {n}/{len(keys)}" in doc[n - 1].get_text()
    assert "RF-C1:act" in manifest["pages"][keys[-1]]["rois"]

    assert "Ring the plumber" in doc[-2].get_text() + doc[-3].get_text() + doc[-4].get_text()
    assert "missing: gone.png" in "".join(p.get_text() for p in doc)

    emb_manifest, emb_tasks = read_state(pdf.read_bytes())
    assert emb_manifest == manifest and emb_tasks == tasks
    assert emb_tasks["note"]["stem"] == STEM
    assert sorted(emb_tasks["tasks"]) == [f"RF-C{i}" for i in range(1, 7)]
    assert emb_tasks["tasks"]["RF-C1"]["bucket"] == "refnotes"


@needs_chromium
def test_body_area_clear_of_chrome(reference_sheet):
    _pdf, result, _vault = reference_sheet
    for page in result["manifest"]["pages"].values():
        body, qr, ai = page["rois"]["page:body"], page["rois"]["page:qr"], page["rois"]["page:ai"]
        assert body["y"] >= max(qr["y"] + qr["h"], ai["y"] + ai["h"])
        assert body["y"] + body["h"] <= page["rois"]["reg:bl"]["y"]


@needs_chromium
def test_overlay_runs_on_reference_pdf(reference_sheet, tmp_path):
    from remarkable_gtd.cli.overlay import main

    pdf, result, _vault = reference_sheet
    assert main([str(pdf), "-o", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.overlay.png"))) == result["page_count"]


@needs_chromium
def test_cli_writes_pdf_and_manifest(tmp_path, reference_sheet):
    from remarkable_gtd.gen.reference_cli import main

    _pdf, _result, vault = reference_sheet
    out = tmp_path / "o.pdf"
    rc = main([str(vault / "Reference" / f"{STEM}.md"), "--vault", str(vault), "--out", str(out),
               "--date", "2026-10-12", "--manifest", str(tmp_path / "o.manifest.json")])
    assert rc == 0 and out.exists() and (tmp_path / "o.manifest.json").exists()
