"""Thin wrapper around the ``rmapi`` binary (ddvk/rmapi) for reMarkable cloud I/O.

Authentication is rmapi's own: a config file at ``$RMAPI_CONFIG`` (or
``~/.rmapi`` / ``~/.config/rmapi/rmapi.conf``) holding ``devicetoken`` and
``usertoken``. :func:`write_config_from_env` creates it from
``RMAPI_DEVICE_TOKEN`` for headless runs such as CI. ``RMAPI_BIN`` overrides
the binary path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RmapiError(RuntimeError):
    """rmapi exited non-zero."""


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    id: str = ""
    modified: str = ""


def _rmapi_cmd() -> list[str]:
    return [os.environ.get("RMAPI_BIN", "rmapi"), "-ni"]


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = _rmapi_cmd() + args
    result = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RmapiError(
            f"{' '.join(cmd)} failed ({result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:500]}"
        )
    return result


def write_config_from_env(path: Path | None = None) -> Path | None:
    """Create rmapi's config from ``RMAPI_DEVICE_TOKEN`` (+ optional
    ``RMAPI_USER_TOKEN``) if the variable is set. Returns the path written,
    or ``None`` when the variable is absent (rmapi will use its own config).

    rmapi refreshes the short-lived user token from the device token by
    itself, so the device token alone is enough.
    """
    device = os.environ.get("RMAPI_DEVICE_TOKEN", "").strip()
    if not device:
        return None
    if path is None:
        env_path = os.environ.get("RMAPI_CONFIG")
        path = Path(env_path) if env_path else Path.home() / ".config" / "rmapi" / "rmapi.conf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"devicetoken": device, "usertoken": os.environ.get("RMAPI_USER_TOKEN", "")}) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    os.environ["RMAPI_CONFIG"] = str(path)
    return path


def parse_ls_json(text: str) -> list[Entry]:
    """Parse ``rmapi ls --json`` output."""
    nodes = json.loads(text) if text.strip() else []
    out = []
    for n in nodes:
        out.append(
            Entry(
                name=n.get("name", ""),
                is_dir=n.get("type") == "CollectionType",
                id=n.get("id", ""),
                modified=n.get("modifiedClient", ""),
            )
        )
    return out


def parse_ls_text(text: str) -> list[Entry]:
    """Parse plain ``rmapi ls`` output (``[d] name`` / ``[f] name`` lines)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\[(d|f)\]\s+(.+?)\s*$", line.strip())
        if m:
            out.append(Entry(name=m.group(2), is_dir=m.group(1) == "d"))
    return out


def list_dir(folder: str = "/") -> list[Entry]:
    """List a reMarkable folder. Raises :class:`RmapiError` if it does not exist."""
    try:
        return parse_ls_json(_run(["ls", "--json", folder]).stdout)
    except (RmapiError, json.JSONDecodeError):
        return parse_ls_text(_run(["ls", folder]).stdout)


def exists(path: str) -> bool:
    parent, _, name = path.rstrip("/").rpartition("/")
    try:
        entries = list_dir(parent or "/")
    except RmapiError:
        return False
    return any(e.name == name for e in entries)


def mkdir(folder: str) -> None:
    """Create a folder (no error if it already exists)."""
    if exists(folder):
        return
    _run(["mkdir", folder])


def upload(local_path: Path, remote_folder: str, force: bool = False) -> None:
    """Upload a PDF into ``remote_folder`` (created if missing)."""
    mkdir(remote_folder)
    args = ["put"]
    if force:
        args.append("--force")
    _run(args + [str(local_path), remote_folder])


def download(remote_path: str, local_dir: Path) -> Path:
    """Download a document as an ``.rmdoc`` zip (PDF + ``.rm`` strokes + metadata).

    Returns the path of the downloaded archive.
    """
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    name = remote_path.rstrip("/").rpartition("/")[2]
    for cand in (local_dir / f"{name}.rmdoc", local_dir / f"{name}.zip"):
        if cand.exists():
            cand.unlink()
    _run(["get", remote_path], cwd=local_dir)
    for cand in (local_dir / f"{name}.rmdoc", local_dir / f"{name}.zip"):
        if cand.exists():
            return cand
    newest = sorted(
        list(local_dir.glob("*.rmdoc")) + list(local_dir.glob("*.zip")),
        key=lambda p: p.stat().st_mtime,
    )
    if newest:
        return newest[-1]
    raise RmapiError(f"rmapi get {remote_path!r} produced no .rmdoc in {local_dir}")


def move(remote_path: str, dest_folder: str) -> None:
    """Move a document into ``dest_folder`` (created if missing)."""
    mkdir(dest_folder)
    _run(["mv", remote_path, dest_folder])


def remove(remote_path: str) -> None:
    _run(["rm", remote_path])


SHEET_NAME_RE = re.compile(r"^\d{8}Z\d{4}_gtd_sheet$")


def list_sheets(remote_folder: str, pattern: re.Pattern = SHEET_NAME_RE) -> list[str]:
    """Names of generated sheets in a folder, oldest first (names sort by time)."""
    try:
        entries = list_dir(remote_folder)
    except RmapiError:
        return []
    return sorted(e.name for e in entries if not e.is_dir and pattern.match(e.name))
