"""Thin wrapper around the rmapi binary for reMarkable cloud operations."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def _rmapi_cmd() -> list[str]:
    """Return the rmapi command with -ni (non-interactive) flag."""
    rmapi = os.environ.get("RMAPI_BIN", "rmapi")
    return [rmapi, "-ni"]


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run rmapi with given args."""
    cmd = _rmapi_cmd() + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def list_files(folder: str = "/") -> list[dict]:
    """List files and folders in a reMarkable directory.

    Returns list of dicts with keys: name, type (file|dir), id.
    """
    result = _run(["ls", folder], check=False)
    if result.returncode != 0:
        return []

    items = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: [d] dirname  or  [f] filename
        m = re.match(r"^\[(\w+)\]\s+(.+)$", line)
        if m:
            items.append(
                {
                    "type": "dir" if m.group(1) == "d" else "file",
                    "name": m.group(2).strip(),
                }
            )
    return items


def upload(local_path: Path, remote_folder: str) -> bool:
    """Upload a file to reMarkable.

    Args:
        local_path: Path to local file.
        remote_folder: Remote folder path (e.g. "GTD Daily").

    Returns:
        True if successful.
    """
    result = _run(["put", str(local_path), remote_folder], check=False)
    return result.returncode == 0


def download(remote_path: str, local_dir: Path) -> Path | None:
    """Download a file from reMarkable.

    Downloads the annotated PDF (reMarkable renders annotations server-side).
    Uses 'rmapi geta --a' which requires the --all-pages flag to produce
    a valid PDF with reMarkable software 3.x+.

    Args:
        remote_path: Remote file path (e.g. "GTD Daily/sheet.pdf").
        local_dir: Local directory to save to.

    Returns:
        Path to downloaded annotated PDF, or None on failure.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    base_name = Path(remote_path).name
    # rmapi geta --a outputs: {base_name}-annotations.pdf
    downloaded = local_dir / f"{base_name}-annotations.pdf"
    # Rename to the conventional _annotated.pdf suffix expected by callers
    renamed = local_dir / f"{base_name}_annotated.pdf"

    # Clean up any stale downloads
    for p in (downloaded, renamed):
        if p.exists():
            p.unlink()

    result = subprocess.run(
        _rmapi_cmd() + ["geta", "--a", remote_path],
        cwd=str(local_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    if downloaded.exists():
        downloaded.rename(renamed)
        return renamed

    # Fallback: search for *-annotations.pdf in directory
    candidates = sorted(
        local_dir.glob("*-annotations.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        candidates[0].rename(renamed)
        return renamed

    return None


def find_latest_gtd(
    remote_folder: str = "GTD Daily", prefix: str = "gtd"
) -> str | None:
    """Find the latest GTD file in a remote folder.

    Returns the remote path (e.g. "GTD Daily/20260530Z1200_gtd_sheet.pdf"),
    or None if not found.
    """
    items = list_files(remote_folder)
    candidates = [
        item["name"]
        for item in items
        if item["type"] == "file" and prefix in item["name"].lower()
    ]
    if not candidates:
        return None

    # Sort and pick latest (timestamp prefix means lexicographic == chronological)
    latest = sorted(candidates)[-1]
    return f"{remote_folder}/{latest}"


def mkdir(remote_path: str) -> bool:
    """Create a folder on reMarkable."""
    result = _run(["mkdir", remote_path], check=False)
    return result.returncode == 0
