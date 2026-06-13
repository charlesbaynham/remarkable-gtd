# remarkable-gtd

GTD (Getting Things Done) PDF generator and machine-vision scanner for the reMarkable 2 e-ink tablet.

## Overview

Generates a 4-page GTD sheet (Inbox / Next Actions / Delegated / Tickler), 157.8 mm wide, with auto-height pages. Each task carries a stable ID, QR code fiducial, labelled gutter checkboxes, and metadata slots. The scanner reads a photographed/exported sheet image and produces a structured decisions JSON.

## Installation

### System dependencies

```bash
# Ubuntu/Debian
sudo apt-get install libzbar0 tesseract-ocr poppler-utils

# Install Chromium for the generator
playwright install chromium
```

### Optional: IBM Plex fonts (for offline render)

Without IBM Plex fonts installed, the template falls back to Google Fonts (requires internet at render time).

### Python package

```bash
# Generator only
pip install "remarkable-gtd[gen]"
playwright install chromium

# Scanner only
pip install "remarkable-gtd[scan]"

# Everything (including dev/test)
pip install "remarkable-gtd[gen,scan,dev]"
```

## Usage

### Generate a GTD sheet

```bash
gtd-gen tasks.json --out today.pdf
gtd-gen tasks.json --out today.pdf --date 2026-05-30
gtd-gen tasks.json --out today.pdf --html debug.html
gtd-gen tasks.json --out today.pdf --manifest layout.manifest.json
gtd-gen tasks.json --out today.pdf --no-manifest
```

### Scan a filled sheet

```bash
gtd-scan sheet_photo.jpg --manifest today.manifest.json -o decisions.json
gtd-scan sheet_photo.jpg --manifest today.manifest.json --page "GTD|inbox|2026-05-30"
gtd-scan sheet_photo.jpg --manifest today.manifest.json --ocr tesseract -o decisions.json
```

## Task JSON input

```jsonc
{
  "date": "2026-05-30",            // optional; defaults to today
  "inbox":     [ { "act": "…" } ], // ids optional → auto IN-01, IN-02…
  "next":      [ { "id": "NA-01", "pri": 7, "due": "30 May", "proj": "epsrc", "act": "…" } ],
  "delegated": [ { "id": "DG-01", "pri": 7, "due": "1 Jun", "proj": "…", "to": "Oliver", "act": "…" } ],
  "tickler":   { "week": [ { "act": "…" } ], "month": [], "quarter": [] }
}
```

Use **stable ids** if your system tracks tasks — the per-row QR encodes the id, so scan results match back cleanly.

## How the scanner works

The sheet is designed so full agent-vision is unnecessary:

1. **Rectify** — the four printed corner brackets (`reg:*`) are detected and a homography warps the photo into the manifest's normalized frame.
2. **Identify** — the header QR (`GTD|<bucket>|<date>`) selects the manifest page; each row's QR verifies the task id at that position.
3. **Tick detection** — ink fill-ratio is measured inside each known box rectangle (the printed border is excluded by an inset), so a tick in a known box is an unambiguous command.
4. **OCR fallback** — only regions with ink get OCR'd: metadata slots, capture lines, and the action text of rows whose ✎ Edit box is ticked. The engine is pluggable (`--ocr null|tesseract`); a vision-LLM backend can implement the same `OcrEngine` protocol.

### Canonical actions

Scan results use canonical verbs, independent of the printed labels:

| Bucket | Primary actions | Defer group |
|---|---|---|
| inbox | `to_next`, `to_deleg`, `drop` | `defer` + `defer_period` 1w/1m/1q |
| next | `done`, `to_deleg` | `defer` + period |
| delegated | `done`, `to_me` | `defer` + period |
| tickler | `activate`, `done` | `defer` + period (re-defer) |

`edited` is an orthogonal flag (✎ box) meaning "re-read this row". Conflicting ticks are resolved by precedence (`done` first) and surfaced in `warnings`. Raw fill ratios are kept under `ticks` for auditability.

## Output formats

### Layout manifest (`*.manifest.json`)

```json
{
  "schema": "gtd.manifest/1",
  "date": "2026-05-30",
  "page_w_mm": 157.8,
  "pages": {
    "GTD|inbox|2026-05-30": {
      "bucket": "inbox",
      "page_no": 1,
      "render": {"w_px": 600, "h_px": 900},
      "rois": {
        "IN-01:to_next": {"x": 0.7, "y": 0.1, "w": 0.05, "h": 0.04},
        "reg:tl": {"x": 0.0, "y": 0.0, "w": 0.03, "h": 0.03}
      }
    }
  }
}
```

### Decisions (`decisions.json`)

```json
{
  "schema": "gtd.decisions/1",
  "source_image": "sheet.jpg",
  "manifest": "today.manifest.json",
  "bucket": "inbox",
  "date": "2026-05-30",
  "tasks": [
    {
      "id": "IN-01",
      "action": "to_next",
      "edited": false,
      "ticks": {"to_next": {"inked": true, "fill": 0.42}}
    }
  ],
  "captures": [],
  "warnings": []
}
```
