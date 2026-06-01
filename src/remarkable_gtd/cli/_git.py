"""Shared git helpers for the GTD CLI commands."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def ensure_vault(gtd_url_or_dir: str, cache_base: Path) -> Path:
    """Return a local Path to the GTD vault, cloning/updating as needed.

    If ``gtd_url_or_dir`` looks like a URL (contains ``://`` or starts with
    ``git@``), the repo is cloned into ``cache_base/vault``.  The local copy
    is treated as fully disposable: we always ``fetch`` then ``reset --hard``
    to match the remote exactly.  Any local edits are wiped.

    If it looks like a plain path, it is returned as-is (no git operations).
    """
    is_url = "://" in gtd_url_or_dir or gtd_url_or_dir.startswith("git@")
    if not is_url:
        return Path(gtd_url_or_dir)

    branch = os.environ.get("GTD_GIT_BRANCH", "main")
    vault_dir = cache_base / "vault"

    if vault_dir.exists():
        print(f"  → Fetching vault from {gtd_url_or_dir} ...")
        r = _run_git(["fetch", "--prune", "origin"], vault_dir)
        if r.returncode != 0:
            print(f"Warning: git fetch failed: {r.stderr.strip()}", file=sys.stderr)
        # Hard-reset to remote — local copy is disposable
        r = _run_git(["reset", "--hard", f"origin/{branch}"], vault_dir)
        if r.returncode != 0:
            print(f"Warning: git reset failed: {r.stderr.strip()}", file=sys.stderr)
    else:
        print(f"  → Cloning vault from {gtd_url_or_dir} ...")
        vault_dir.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", "clone", "--branch", branch, gtd_url_or_dir, str(vault_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            print(f"Error: git clone failed:\n{r.stderr}", file=sys.stderr)
            raise RuntimeError(f"Could not clone {gtd_url_or_dir}")

    return vault_dir


def commit_and_push(gtd_dir: Path) -> bool:
    """Stage all changes, commit, and push to origin.

    Returns True if successful (or if there was nothing to commit).
    """
    branch = os.environ.get("GTD_GIT_BRANCH", "main")

    status = _run_git(["status", "--porcelain"], gtd_dir)
    if status.returncode != 0:
        print(f"Warning: git status failed: {status.stderr.strip()}", file=sys.stderr)
        return False

    if not status.stdout.strip():
        print("  (no changes to commit)")
        return True

    for cmd in (
        ["add", "."],
        ["commit", "-m", "GTD update from reMarkable"],
        ["push", "origin", branch],
    ):
        r = _run_git(cmd, gtd_dir)
        if r.returncode != 0:
            print(f"Warning: git {cmd[0]} failed: {r.stderr.strip()}", file=sys.stderr)
            return False

    print("  ✓ committed and pushed changes")
    return True
