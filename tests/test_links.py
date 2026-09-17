"""Internal PDF navigation: summary <-> project pages (needs Chromium).

The projects summary is an index, so each project block is a GoTo link to
that project's own page and each project page links back. Whether the
reMarkable's own PDF reader follows them is firmware behaviour we cannot
test here; this checks the annotations are in the file and point at the
right page objects.
"""
from __future__ import annotations

from remarkable_gtd.common.schema import make_page_key
from tests.conftest import needs_chromium

pytestmark = needs_chromium


def _links(pdf_path) -> list[tuple[int, int]]:
    """``[(source page index, destination page index), ...]`` for every link."""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    indices = {page.indirect_reference.idnum: i for i, page in enumerate(reader.pages)}
    out: list[tuple[int, int]] = []
    for i, page in enumerate(reader.pages):
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            if obj.get("/Subtype") != "/Link":
                continue
            dest = obj.get("/Dest")
            if dest is None and obj.get("/A") is not None:
                action = obj["/A"].get_object()
                assert action.get("/S") == "/GoTo"
                dest = action.get("/D")
            target = dest[0]
            idnum = getattr(target, "idnum", None)
            out.append((i, indices[idnum] if idnum is not None else int(target)))
    return out


def test_summary_links_to_each_project_and_back(rendered_sheet, manifest):
    pdf_path, _ = rendered_sheet
    pages = list(manifest["pages"])
    summary_idx = pages.index(make_page_key("projects", "2026-05-30"))
    p1_idx = pages.index(make_page_key("project-01", "2026-05-30"))
    p2_idx = pages.index(make_page_key("project-02", "2026-05-30"))
    np_idx = pages.index(make_page_key("new-projects", "2026-05-30"))

    links = _links(pdf_path)
    assert (summary_idx, p1_idx) in links
    assert (summary_idx, p2_idx) in links
    assert (p1_idx, summary_idx) in links
    assert (p2_idx, summary_idx) in links
    # The summary also points at the New Projects page, which points back.
    assert (summary_idx, np_idx) in links
    assert (np_idx, summary_idx) in links
    # No stray links anywhere else.
    assert {src for src, _ in links} == {summary_idx, p1_idx, p2_idx, np_idx}
    assert len(links) == 6


def test_link_rects_match_the_manifest_rois(rendered_sheet, manifest):
    """Each annotation covers its ROI, with PDF y measured from the bottom."""
    from pypdf import PdfReader

    pdf_path, _ = rendered_sheet
    key = make_page_key("projects", "2026-05-30")
    idx = list(manifest["pages"]).index(key)
    roi = manifest["pages"][key]["rois"]["link:P01"]

    page = PdfReader(pdf_path).pages[idx]
    pw, ph = float(page.mediabox.width), float(page.mediabox.height)
    rects = [
        [float(v) for v in a.get_object()["/Rect"]]
        for a in page["/Annots"]
    ]
    expected = [
        roi["x"] * pw,
        ph * (1.0 - (roi["y"] + roi["h"])),
        (roi["x"] + roi["w"]) * pw,
        ph * (1.0 - roi["y"]),
    ]
    assert any(
        all(abs(a - b) < 1.0 for a, b in zip(rect, expected)) for rect in rects
    ), f"no annotation matches {expected}, got {rects}"
