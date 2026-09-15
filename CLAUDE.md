# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`remarkable-gtd` is a GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet. It has two halves:

1. **Generation (`gtd-gen`)** — Renders a 4-page PDF (one tall auto-height page per bucket: Inbox / Next Actions / Delegated / Tickler) with task rows, checkboxes, and QR fiducials. Uses Playwright/Chromium to measure content height and emit exact-size pages.
2. **Scanning (`gtd-scan-pdf`, `gtd-scan`)** — Machine-vision pipeline that reads handwritten ticks from the annotated sheet and produces a structured decisions JSON. Handwriting is transcribed only where ink is found, by sending that crop to a vision LLM via OpenRouter.

The two halves are bridged by a **layout manifest**: the generator exports normalized bounding rectangles of every interactive element via `data-roi` attributes, and the scanner rectifies the page against 4 corner registration marks then samples ink at those exact rects. The manifest and the task list are **embedded in the PDF** as attachments, so the sheet that comes back from the device is self-describing.

This package is vault-agnostic: tasks JSON in, decisions JSON out. The Obsidian-vault adapter and the nightly GitLab CI job live in the `gtd` repository under `.gtd/remarkable/`.

## Development commands

```bash
pip install -e ".[dev]"
playwright install --with-deps chromium          # required for gen tests

pytest --tb=short                                # full suite (needs Chromium)
pytest --ignore=tests/test_end_to_end.py --ignore=tests/test_manifest.py   # no browser
pytest tests/test_annotated_fixture.py -v        # real hand-ticked sheet
```

### CLI usage

```bash
gtd-gen tasks.json --out today.pdf --date 2026-06-01 [--html debug.html]
gtd-scan-pdf sheet.rmdoc --ocr openrouter -o decisions.json      # manifest read from the PDF
gtd-scan-pdf annotated.pdf --manifest today.manifest.json -o decisions.json
gtd-scan page.png --manifest today.manifest.json -o decisions.json
gtd-render-annotations sheet.rmdoc annotated.pdf
```

## Architecture

### PDF generation pipeline (`src/remarkable_gtd/gen/`)

- `generate.py` — Core library. `render_pdf()` launches Chromium, renders each bucket's HTML via Jinja2, measures pixel height with `page.evaluate()`, and emits a PDF page cut to fit. It then attaches `gtd.manifest.json` and `gtd.tasks.json` to the PDF (`common/embedded.py`) and returns both documents. Assets (CSS, template) are loaded via `importlib.resources.files("remarkable_gtd.gen.assets")` — **not** `Path(__file__).parent`.
- `assets/template.html.j2` — Jinja2 template with `data-roi` attributes on every interactive element. The `data-roi` values use canonical verbs (e.g. `NA-01:done`, `NA-01:defer_1w`, `capture:N1:line`).
- `assets/gtd.css` — Print stylesheet. Fixed width (`157.8mm`, reMarkable 2 panel width = 1404 px at 226 dpi, so the device shows the page 1:1); height grows to content. **Do not change CSS classes or layout** — the sheet design is final; `data-roi` attributes must stay purely additive.
- `manifest.py` — `collect_rois(page)` runs JS that walks `[data-roi]` elements and returns rects normalized to the `.page` bounding box (fractions 0..1). `build_manifest()` assembles the document.

### Machine-vision pipeline (`src/remarkable_gtd/scan/`)

`sheet.scan_rmdoc()` / `sheet.scan_pdf()` drive the per-page pipeline `pipeline.run_scan(image_path, manifest, cfg, page_key, task_texts)`:

1. **Load** — `cv2.imread`; EXIF transpose via PIL; grayscale + Otsu binary.
2. **Reg marks** (`rectify.find_reg_marks`) — Searches 15% corner quadrants for cross-shaped components. Raises `RegistrationError` if < 4 found.
3. **Rectify** (`rectify.rectify`) — `cv2.getPerspectiveTransform` from detected marks to manifest `reg:*` centers, warped to a canonical canvas (width 1404 px). Residual is stored for QA.
4. **QR decode** (`qr.py`) — `cv2.QRCodeDetector` (pyzbar as optional second decoder). `decode_region` widens the crop progressively — the header QR needs a bigger quiet zone than the row QRs. `decode_header` checks `page:qr`; `decode_task_qrs` verifies per-row identity.
5. **Ink detection** (`ink.py`) — `detect_box()` crops the ROI from the rectified binary, insets by `inner_inset_frac` (0.22 for tick boxes, 0.15 for slots) to exclude the printed border, and measures dark-pixel fill ratio. Tick threshold 0.06, slot/capture threshold 0.03.
6. **Handwriting** (`ocr.py`) — `OcrEngine` Protocol `read(image, hint, context)`. `OpenRouterEngine` (default in production; `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`), `TesseractEngine`, `NullEngine` (tests). Invoked only where ink is present, on a tight crop, with a hint naming the region (`priority`/`due`/`project`/`to`/`capture`/`act`) and, for an amended action, the printed text as context.
7. **Decisions** (`decisions.py`) — `resolve_task()` maps ticked verbs to a single `action` per task using bucket-specific precedence (`done > activate > to_next > to_me > to_deleg > drop > defer`). `edited` flag is orthogonal. Conflict warnings are emitted when multiple boxes are ticked.

### reMarkable I/O (`src/remarkable_gtd/rm/`)

- `api.py` — thin wrapper over the `rmapi` binary (ddvk fork): `list_sheets`, `download` (`rmapi get` → `.rmdoc`), `upload`, `move`, `mkdir`; `write_config_from_env()` builds rmapi's config from `RMAPI_DEVICE_TOKEN` for headless runs.
- `annotations.py` — unpacks an `.rmdoc`, parses v6 strokes with `rmscene`, draws them onto the original PDF with PyMuPDF. Coordinate transform: x is centred on 0 (shift by +702), y is 0-based, uniform scale 72/226.

### Key design decisions

- **Manifest as the bridge**: the scanner knows exactly where every box is because the generator measured it in Chromium. This is the central architectural invariant.
- **State travels in the PDF**: manifest + tasks are PDF attachments; the reMarkable keeps the uploaded PDF byte-for-byte, so nothing has to be stored between the upload run and the processing run.
- **Normalized rects**: all manifest ROIs are fractions of the page box (0..1); the scanner scales them to the rectified canvas.
- **Deterministic tests**: core CV tests use `NullEngine`/a recording engine. `tests/fixtures/annotated_scan/` is a sheet that was really ticked on a device (tasks.example.json for 2026-06-02) with its matching manifest — regenerate the manifest with `gtd-gen tests/fixtures/tasks.example.json --date 2026-06-02` if the template ever changes.
- **Bucket action vocabularies**: `BUCKET_ACTIONS` in `decisions.py`. The defer trio keys differ between normal buckets (`defer_1w/1m/1q`) and tickler (`redefer_1w/1m/1q`); both surface as action `defer` + `defer_period`.

### Schemas

- **Manifest** (`gtd.manifest/1`): `{schema, date, page_w_mm, pages: {"GTD|<bucket>|<date>": {bucket, page_no, render: {w_px, h_px}, rois: {"<key>": {x, y, w, h}}}}}`
- **Tasks** (`gtd.tasks/1`, embedded): `{schema, date, tasks: {"<id>": {act, bucket, period?, pri?, due?, proj?, to?, ...caller extras}}}`
- **Decisions** (`gtd.decisions/1`): per page `{page_key, page_no, bucket, date, header_qr, rectify: {residual_px, reg_marks_found}, tasks: [{id, qr_verified, action, defer_period?, edited, act_text?, fields: {<f>: {text, fill}}, ticks, warnings}], captures: [{line, text, inked}], warnings}`; `gtd-scan-pdf` wraps pages in `{schema, source_pdf, pages: [...]}`.

## CI

`.github/workflows/ci.yml`: **scan-unit** (no browser) and **full** (Playwright Chromium) jobs.
