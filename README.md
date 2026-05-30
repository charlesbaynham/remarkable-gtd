# remarkable-gtd

GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet.

## Overview

This package generates tall, auto-height PDF pages (one per GTD bucket:
**Inbox / Next Actions / Delegated / Tickler**) and provides a machine-vision
pipeline that reads handwritten marks from a scanned/photographed sheet.

## Installation

```bash
pip install -e ".[gen,scan,dev]"
playwright install chromium
```

System dependencies (Ubuntu/Debian):
```bash
sudo apt-get install libzbar0 tesseract-ocr poppler-utils
```

## Usage

### Generate a GTD sheet

```bash
gtd-gen tasks.json --out today.pdf
```

Options:
- `--date YYYY-MM-DD` — override sheet date
- `--html debug.html` — also dump per-bucket HTML for debugging
- `--manifest PATH` — custom manifest output path
- `--no-manifest` — suppress manifest sidecar

### Scan a marked sheet

```bash
gtd-scan scan.png --manifest today.manifest.json -o decisions.json
```

Options:
- `--page KEY` — select a specific page from the manifest
- `--ocr null|tesseract` — OCR engine (default: null)

## Project structure

```
remarkable-gtd/
  src/remarkable_gtd/
    gen/          — PDF generation (Playwright/Chromium)
    scan/         — Machine vision pipeline
    common/       — Shared geometry / schema helpers
  tests/          — pytest suite
```
