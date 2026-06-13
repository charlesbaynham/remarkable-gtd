# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`remarkable-gtd` is a GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet. It has two halves:

1. **Generation (`gtd-gen`)** — Renders a 4-page PDF (one tall auto-height page per bucket: Inbox / Next Actions / Delegated / Tickler) with task rows, checkboxes, and QR fiducials. Uses Playwright/Chromium to measure content height and emit exact-size pages.
2. **Scanning (`gtd-scan`)** — Machine-vision pipeline that reads handwritten ticks from a scanned/photographed sheet and produces a structured decisions JSON.

The two halves are bridged by a **layout manifest** (JSON sidecar): the generator exports normalized bounding rectangles of every interactive element via `data-roi` attributes, and the scanner rectifies the photo against 4 corner registration marks then samples ink at those exact rects.

## Development commands

### Setup

```bash
# Editable install with all extras
pip install -e ".[gen,scan,dev]"

# Install Chromium for Playwright (required for gen tests)
playwright install chromium
```

System dependencies (Ubuntu/Debian):
```bash
sudo apt-get install libzbar0 tesseract-ocr poppler-utils
```

A Nix flake is also provided (`flake.nix`) with a dev shell including all system deps and pre-configured Playwright browser paths.

### Running tests

```bash
# Full test suite (requires Chromium)
pytest --tb=short

# Scan-only tests (no browser needed)
pytest -k "not (end_to_end or manifest)" --tb=short

# Run a single test file
pytest tests/test_ink.py -v

# Run a specific test
pytest tests/test_decisions.py::test_resolve_task_done_wins_over_defer -v
```

### CLI usage

```bash
# Generate a GTD sheet
gtd-gen tasks.json --out today.pdf

# Generate with custom date and debug HTML
gtd-gen tasks.json --out today.pdf --date 2026-06-01 --html debug.html

# Scan a marked sheet
gtd-scan scan.png --manifest today.manifest.json -o decisions.json

# Scan with OCR
gtd-scan scan.png --manifest today.manifest.json --ocr tesseract -o decisions.json
```

## Architecture

### PDF generation pipeline (`src/remarkable_gtd/gen/`)

- `generate.py` — Core library. `render_pdf()` launches Chromium, renders each bucket's HTML via Jinja2, measures pixel height with `page.evaluate()`, and emits a PDF page cut to fit. Assets (CSS, template) are loaded via `importlib.resources.files("remarkable_gtd.gen.assets")` — **not** `Path(__file__).parent`.
- `assets/template.html.j2` — Jinja2 template with `data-roi` attributes on every interactive element (gutter boxes, slots, QR regions, capture lines, reg marks). The `data-roi` values use canonical verbs (e.g. `NA-01:done`, `NA-01:defer_1w`, `capture:N1:line`).
- `assets/gtd.css` — Print stylesheet. Fixed width (`157.8mm`, reMarkable 2 panel width); height grows to content. **Do not change CSS classes or layout** when adding `data-roi` — attributes must be purely additive.
- `manifest.py` — `collect_rois(page)` runs JS via `page.evaluate()` that walks `[data-roi]` elements and returns rects normalized to the `.page` bounding box (fractions 0..1). `write_manifest()` assembles the sidecar JSON.

### Machine-vision pipeline (`src/remarkable_gtd/scan/`)

Orchestrated by `pipeline.run_scan(image_path, manifest, cfg) -> dict`:

1. **Load** — `cv2.imread`; EXIF transpose via PIL; grayscale + Otsu binary.
2. **Reg marks** (`rectify.find_reg_marks`) — Searches 15% corner quadrants for cross-shaped components. Scores by "plus-ness" (high fill in center bands, low in corners) + proximity to corner. Raises `RegistrationError` if < 4 found.
3. **Rectify** (`rectify.rectify`) — `cv2.getPerspectiveTransform` from detected marks to manifest `reg:*` centers, warped to a canonical canvas (width 1404 px, height from manifest aspect ratio). Residual (mean reprojection error) is stored for QA.
4. **QR decode** (`qr.py`) — Pluggable `QRBackend` Protocol. Default `OpenCVBackend` (`cv2.QRCodeDetector`), fallback `PyzbarBackend`. `decode_header` reads `page:qr` to select the manifest page; `decode_task_qrs` verifies per-row identity.
5. **Ink detection** (`ink.py`) — `detect_box()` crops the ROI from the rectified binary, insets by `inner_inset_frac` (default 0.22) to exclude the printed border, and measures dark-pixel fill ratio. `inked = fill > threshold` (default 0.06). Defer trios use `select_one` (argmax above threshold).
6. **OCR** (`ocr.py`) — Pluggable `OcrEngine` Protocol. `NullEngine` (returns "", default for deterministic core tests) and `TesseractEngine` (pytesseract with PSM tuned per hint: 7 for slots/single-line, 6 for paragraphs). Invoked only where ink is present.
7. **Decisions** (`decisions.py`) — `resolve_task()` maps ticked verbs to a single `action` per task using bucket-specific precedence (`done > activate > to_next > to_me > to_deleg > drop > defer`). `edited` flag is orthogonal (set if `edit` ticked or any slot has OCR text). Conflict warnings emitted when multiple non-defer actions are ticked.

### Key design decisions

- **Manifest as the bridge**: Rather than fragile contour detection, the scanner knows exactly where every box is because the generator measured it in Chromium. This is the central architectural invariant.
- **Normalized rects**: All manifest ROIs are fractions of the page box (0..1), not pixels. The scanner scales them to the rectified canvas size.
- **Canonical canvas width**: 1404 px (reMarkable 2 panel width). Height is derived from manifest `render.h_px / render.w_px`.
- **Deterministic tests**: Core CV tests use `NullEngine` for OCR to avoid Tesseract nondeterminism. E2E tests in `test_end_to_end.py` render a PDF → rasterize → paint synthetic ink into ROIs → run the full pipeline.
- **Bucket action vocabularies**: `BUCKET_ACTIONS` in `decisions.py` defines which verbs are valid per bucket. The defer trio keys differ between normal buckets (`defer_1w/1m/1q`) and tickler (`redefer_1w/1m/1q`).

### Schemas

- **Manifest** (`gtd.manifest/1`): `{schema, date, page_w_mm, pages: {"GTD|<bucket>|<date>": {bucket, page_no, render: {w_px, h_px}, rois: {"<key>": {x, y, w, h}}}}}`
- **Decisions** (`gtd.decisions/1`): `{schema, source_image, manifest, bucket, date, header_qr, rectify: {residual_px, reg_marks_found}, tasks: [{id, qr_verified, action, edited, fields, ticks, warnings}], captures: [{line, text, inked, ocr_conf}], warnings: [...]}`

## CI

`.github/workflows/ci.yml` has two jobs:
- **scan-unit** — Installs `[scan,dev]` only (no browser), runs `pytest -k "not (end_to_end or manifest)"`
- **full** — Installs `[gen,scan,dev]` + Playwright Chromium, runs full `pytest --tb=short`

Both install system deps via `apt-get`: `libzbar0`, `tesseract-ocr`, `poppler-utils`.
