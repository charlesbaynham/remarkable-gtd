"""Manifest I/O helpers for the scan pipeline."""
from __future__ import annotations

import json
from pathlib import Path

from remarkable_gtd.common.schema import MANIFEST_SCHEMA


def load_manifest(path: Path | str) -> dict:
    """Load and minimally validate a manifest JSON file.

    Args:
        path: Path to the ``.manifest.json`` file.

    Returns:
        The parsed manifest dict.

    Raises:
        ValueError: If the schema field is missing or wrong.
    """
    raw = Path(path).read_text(encoding="utf-8")
    manifest = json.loads(raw)
    schema = manifest.get("schema", "")
    if schema != MANIFEST_SCHEMA:
        raise ValueError(
            f"Unsupported manifest schema {schema!r}; expected {MANIFEST_SCHEMA!r}"
        )
    return manifest


def list_page_keys(manifest: dict) -> list[str]:
    """Return all page keys present in the manifest.

    Args:
        manifest: Parsed manifest dict.

    Returns:
        List of page key strings (e.g. ``["GTD|inbox|2026-05-30", ...]``).
    """
    return list(manifest.get("pages", {}).keys())


def get_page(manifest: dict, key: str) -> dict:
    """Retrieve a single page entry from the manifest.

    Args:
        manifest: Parsed manifest dict.
        key: Page key string (e.g. ``"GTD|inbox|2026-05-30"``).

    Returns:
        The per-page dict with ``bucket``, ``page_no``, ``render``, ``rois``.

    Raises:
        KeyError: If the key is not found in the manifest.
    """
    pages = manifest.get("pages", {})
    if key not in pages:
        available = list(pages.keys())
        raise KeyError(
            f"Page key {key!r} not in manifest. Available keys: {available}"
        )
    return pages[key]
