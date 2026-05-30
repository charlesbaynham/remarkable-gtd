"""Manifest I/O helpers."""
from __future__ import annotations

import json
from pathlib import Path


def load_manifest(path: Path | str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get_page(manifest: dict, key: str) -> dict:
    return manifest["pages"][key]


def list_page_keys(manifest: dict) -> list[str]:
    return list(manifest.get("pages", {}).keys())
