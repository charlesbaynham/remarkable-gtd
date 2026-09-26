"""Starred projects: sorted first, and linked from a hotbar on every page.

A project entry carrying ``starred: true`` is printed before the others (so
it is ``P01``), gets a ★ in its name, and appears in the hotbar under the
header of every page as a GoTo link to its own page. Its project row prints
a ★ box whose tick means "flip the star this project was printed with".
"""
from __future__ import annotations

import copy
from datetime import date

import pytest

from remarkable_gtd.common.schema import make_page_key
from remarkable_gtd.gen.generate import build_buckets
from remarkable_gtd.scan.decisions import resolve_task
from tests.conftest import _chromium_available, needs_chromium
from tests.test_decisions import ticks
from tests.test_links import _links

DAY = "2026-05-30"


@pytest.fixture(scope="module")
def tasks_starred(tasks_min) -> dict:
    """tasks.min.json with its *second* project starred."""
    data = copy.deepcopy(tasks_min)
    data["projects"][1]["starred"] = True
    return data


def test_starred_project_sorts_first(tasks_starred):
    buckets = build_buckets(tasks_starred)
    summary = next(b for b in buckets if b["kind"] == "summary")
    assert [(p["ref"], p["name"], p["starred"]) for p in summary["projects"]] == [
        ("P01", "Benchmark survey", True),
        ("P02", "EPSRC proposal", False),
    ]
    p1 = next(b for b in buckets if b["key"] == "project-01")
    assert p1["title"] == "Benchmark survey" and p1["starred"] is True
    assert p1["head_items"][0]["starred"] is True


def test_hotbar_on_every_page(tasks_starred, tasks_min):
    for b in build_buckets(tasks_starred):
        assert b["hotbar"] == [{"ref": "P01", "name": "Benchmark survey"}], b["key"]
    # nothing starred -> no hotbar, and the order is as given
    buckets = build_buckets(tasks_min)
    assert all(b["hotbar"] == [] for b in buckets)
    summary = next(b for b in buckets if b["kind"] == "summary")
    assert [p["name"] for p in summary["projects"]] == ["EPSRC proposal", "Benchmark survey"]


def test_star_is_a_flag_not_an_action():
    entry, warnings = resolve_task("P01-PJ", ticks(done=False, star=True), "projhead")
    assert entry["star"] is True and entry["action"] == "none"
    assert warnings == []
    entry, _ = resolve_task("P01-PJ", ticks(done=True, star=True), "projhead")
    assert entry["star"] is True and entry["action"] == "done"
    # a row without a ★ box carries no star key at all
    entry, _ = resolve_task("NA-01", ticks(done=True), "next")
    assert "star" not in entry


def test_star_on_an_ai_row_is_only_a_suggestion():
    entry, _ = resolve_task("P01-PJ", ticks(star=True, ai=True), "projhead")
    assert entry["star"] is False
    assert entry["suggestion"]["star"] is True


@pytest.fixture(scope="module")
def starred_sheet(tmp_path_factory, tasks_starred):
    if not _chromium_available():
        pytest.skip("Playwright Chromium not installed")
    from remarkable_gtd.gen.generate import render_pdf

    out = tmp_path_factory.mktemp("starred") / "sheet.pdf"
    docs = render_pdf(tasks_starred, date(2026, 5, 30), out, manifest_path=None)
    return out, docs["manifest"], docs["tasks"]


@needs_chromium
def test_hotbar_links_every_page_to_the_starred_project(starred_sheet):
    pdf_path, manifest, _tasks = starred_sheet
    pages = list(manifest["pages"])
    target = pages.index(make_page_key("project-01", DAY))
    links = _links(pdf_path)
    for i, key in enumerate(pages):
        assert "link:P01@hot" in manifest["pages"][key]["rois"], key
        assert (i, target) in links, key
    # the summary keeps its own block link beside the hotbar's
    summary = manifest["pages"][make_page_key("projects", DAY)]["rois"]
    assert {"link:P01", "link:P02", "link:P01@hot"} <= set(summary)
    assert summary["link:P01@hot"]["y"] < summary["link:P01"]["y"]


@needs_chromium
def test_project_row_prints_a_star_box(starred_sheet, manifest):
    _pdf, starred_manifest, tasks = starred_sheet
    for m in (starred_manifest, manifest):  # starred (Unstar) and not (Star)
        rois = m["pages"][make_page_key("project-01", DAY)]["rois"]
        assert "P01-PJ:star" in rois
        assert not [k for k in rois if k.endswith(":star") and not k.startswith("P01-PJ")]
    assert tasks["tasks"]["P01-PJ"]["starred"] is True
    assert tasks["tasks"]["P02-PJ"]["starred"] is False


@needs_chromium
def test_hotbar_rois_are_not_scanned(starred_sheet):
    from remarkable_gtd.scan.pipeline import task_ids_from_rois

    _pdf, manifest, _tasks = starred_sheet
    rois = manifest["pages"][make_page_key("next", DAY)]["rois"]
    assert all("@" not in t for t in task_ids_from_rois(rois))
