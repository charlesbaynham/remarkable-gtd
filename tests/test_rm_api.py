"""rmapi wrapper: output parsing and the subprocess contract (via a fake binary)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from remarkable_gtd.rm import api


def test_parse_ls_json_and_text():
    nodes = [
        {"name": "Archive", "type": "CollectionType", "id": "a"},
        {"name": "20260601Z0330_gtd_sheet", "type": "DocumentType", "id": "b", "modifiedClient": "t"},
    ]
    entries = api.parse_ls_json(json.dumps(nodes))
    assert [(e.name, e.is_dir) for e in entries] == [("Archive", True), ("20260601Z0330_gtd_sheet", False)]
    text = "[d] Archive\n[f] 20260601Z0330_gtd_sheet\n\n"
    assert [(e.name, e.is_dir) for e in api.parse_ls_text(text)] == [("Archive", True), ("20260601Z0330_gtd_sheet", False)]


@pytest.fixture
def fake_rmapi(tmp_path, monkeypatch):
    """A shell stand-in for rmapi that logs its argv and answers `ls --json`."""
    log = tmp_path / "calls.log"
    script = tmp_path / "rmapi"
    listing = json.dumps([
        {"name": "Archive", "type": "CollectionType"},
        {"name": "20260602Z0330_gtd_sheet", "type": "DocumentType"},
        {"name": "20260601Z0330_gtd_sheet", "type": "DocumentType"},
        {"name": "notes", "type": "DocumentType"},
    ])
    script.write_text(
        "#!/bin/sh\n"
        f"echo \"$@\" >> '{log}'\n"
        "case \"$2\" in\n"
        f"  ls) echo '{listing}' ;;\n"
        "  get) name=$(basename \"$3\"); printf zip > \"$name.rmdoc\" ;;\n"
        "esac\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("RMAPI_BIN", str(script))
    return log


def test_list_sheets_sorted_and_filtered(fake_rmapi):
    assert api.list_sheets("GTD Daily") == ["20260601Z0330_gtd_sheet", "20260602Z0330_gtd_sheet"]


def test_download_and_move_use_rmapi(fake_rmapi, tmp_path):
    got = api.download("GTD Daily/20260601Z0330_gtd_sheet", tmp_path / "dl")
    assert got.name == "20260601Z0330_gtd_sheet.rmdoc" and got.read_bytes() == b"zip"
    api.move("GTD Daily/20260601Z0330_gtd_sheet", "GTD Daily/Archive")
    calls = fake_rmapi.read_text().splitlines()
    assert calls[0].startswith("-ni get GTD Daily/20260601Z0330_gtd_sheet")
    assert any(c.startswith("-ni mv GTD Daily/20260601Z0330_gtd_sheet GTD Daily/Archive") for c in calls)
    # mkdir only when the folder is missing: Archive exists, so no mkdir call.
    assert not any(" mkdir " in c for c in calls)


def test_write_config_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("RMAPI_DEVICE_TOKEN", raising=False)
    assert api.write_config_from_env(tmp_path / "c.conf") is None
    monkeypatch.setenv("RMAPI_DEVICE_TOKEN", "dev123")
    path = api.write_config_from_env(tmp_path / "c.conf")
    assert json.loads(path.read_text()) == {"devicetoken": "dev123", "usertoken": ""}
    assert os.environ["RMAPI_CONFIG"] == str(path)
