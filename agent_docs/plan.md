# remarkable-gtd — PDF generation CLI + machine-vision interface

## Context

`remarkable-gtd` is a GTD (Getting Things Done) paper workflow for a reMarkable 2
e-ink tablet. A design handoff bundle (Claude Design) defines a 4-page sheet —
one tall, auto-height page per bucket (**Inbox / Next Actions / Delegated /
Tickler**), 157.8 mm wide. The user writes on it with a stylus: ticking labelled
checkboxes in a right-hand **gutter** (Done / Delegate / Defer 1w·1m·1q / Edit /
Drop …) and writing into blank **slots** (metadata edits) and **capture lines**
(freeform new items). The device exports a flattened image (printed page +
handwritten ink).

The sheet was deliberately designed so **full agent-vision is unnecessary**:
every page has 4 corner **registration marks**, a header **QR** (`GTD|<bucket>|<date>`),
and every task row carries a **QR** of its stable id (e.g. `NA-05`) plus the
gutter of tick boxes. The plan: rectify the photo against the reg marks, decode
QRs for identity, check ink-fill in known boxes, and fall back to handwriting OCR
only for slots/capture/Edit regions.

The git repo is currently **empty** (no commits). The design bundle lives at
`/tmp/gtd_design/gtd-remarkable-interface/`. Eventual integration with a GTD
Obsidian vault is **out of scope** — this work delivers two things:

- **(a) PDF generation** — the generator `generate.py` is already written and
  works (Playwright/Chromium → auto-height PDF per bucket). It needs to be
  brought into the repo and packaged as a proper installable CLI, plus a new
  **layout-manifest export** (see below).
- **(b) Machine-vision interface** — the new build: a scanner that turns a
  written-on sheet image into a structured **decisions JSON**.

### Key architectural decision (confirmed)
The vision side must know **where** each box is to sample ink. Rather than fragile
contour-detection, the generator (which already runs Chromium) also exports a
**layout manifest**: the normalized bounding rectangle of every tick box, slot,
capture line, QR, and reg mark, keyed by `taskid:action`, via
`getBoundingClientRect()`. The scanner rectifies the photo against the 4 reg
marks (homography) into that same normalized frame and samples ink at exact
rectangles. This makes box-finding deterministic. Manifest↔scan pairing is via
the header-QR string as the lookup key.

### User decisions
- **OCR backend:** Tesseract is the default, behind a pluggable `OcrEngine`
  interface; a Claude-vision backend is a documented future extension point.
- **Scope:** full CV pipeline working + tested (rectify / QR / ink / decisions);
  OCR delivered as the interface + a thin `TesseractEngine` and `NullEngine`
  (no deep OCR-accuracy testing; core CV tests use `NullEngine` for determinism).

## Repo layout

```
remarkable-gtd/
  pyproject.toml                # packaging, 2 console scripts, gen/scan extras
  README.md                     # install (incl. system deps) + usage
  src/remarkable_gtd/
    __init__.py                 # __version__
    common/
      geometry.py               # Rect, normalize/denormalize helpers, constants
      schema.py                 # manifest + decisions schema versions/keys
    gen/
      generate.py               # ported generator (library funcs)
      manifest.py               # ROI-collector JS + normalization + sidecar write
      cli.py                    # gtd-gen entry point
      assets/template.html.j2   # moved verbatim + data-roi attrs (no CSS change)
      assets/gtd.css            # moved verbatim (byte-identical visuals)
    scan/
      pipeline.py               # orchestrator run_scan(image, manifest, cfg)
      rectify.py                # reg-mark detect + homography
      qr.py                     # QR decode (header + per-row), pluggable backend
      ink.py                    # per-ROI fill-ratio ink detection
      ocr.py                    # OcrEngine protocol + Tesseract/Null engines
      decisions.py              # resolve verbs -> decisions JSON
      manifest_io.py            # load/validate manifest
      cli.py                    # gtd-scan entry point
  tests/
    conftest.py                 # fixtures: render, rasterize, paint_ink, warp
    fixtures/tasks.min.json
    fixtures/example.pdf        # copied from handoff uploads/ (regression raster)
    test_manifest.py test_ink.py test_rectify.py
    test_qr.py test_decisions.py test_end_to_end.py
  .github/workflows/ci.yml      # apt zbar+tesseract+poppler, playwright, pytest
```

`src/` layout; two console scripts `gtd-gen` and `gtd-scan`. **Porting gotcha:**
`generate.py` reads `gtd.css`/template via `Path(__file__).parent`; after
packaging, load assets via `importlib.resources` from `remarkable_gtd.gen.assets`.

## (a) PDF generation + manifest export

Port `generate.py` largely as-is (reuse `qr_datauri`, `build_buckets`,
`render_bucket_html`, `render_pdf`). Two additions:

1. **`data-roi` attributes in `template.html.j2` only** (CSS untouched — nothing
   selects on `[data-roi]`, so pixels stay byte-identical). Thread the task id
   into the macros and tag inner elements with **canonical decision verbs**
   (independent of display wording):
   - gutter `.box` → `data-roi="<id>:<verb>"` where verb ∈
     `to_next, to_deleg, drop, done, to_me, activate, edit`.
   - defer trio `.box` → `<id>:defer_1w|1m|1q` (tickler: `redefer_1w|1m|1q`).
   - field `.slot` → `<id>:slot_priority|due|project|to`.
   - row `.rail .qr` → `<id>:qr`; `.act` → `<id>:act` (OCR region for Edit).
   - capture → `capture:N<i>:box` and `capture:N<i>:line`.
   - page fiducials → `reg:tl|tr|bl|br`, header QR → `page:qr`.
   Edit macros `cell`, `defer`, `fld_cur/fld_blank/fld_prom`, `fields`, `row`,
   capture loop, and the `.reg`/header-QR markup to thread ids and emit attrs.

2. **`gen/manifest.py`** — inside the existing `sync_playwright` loop in
   `render_pdf`, after height measurement, call `collect_rois(page)` which
   `page.evaluate`s JS that walks `[data-roi]`, computing each rect normalized to
   the `.page` box (fractions 0..1). Assemble a manifest keyed by the header-QR
   string, write `<out>.manifest.json` alongside the PDF.
   `render_pdf(..., manifest_path=None)` defaults to `out.with_suffix('.manifest.json')`.
   `gtd-gen` gains `--manifest PATH` / `--no-manifest`.

**Manifest schema** `gtd.manifest/1` (`common/schema.py`): `{schema, date,
page_w_mm, pages: { "GTD|<bucket>|<date>": { bucket, page_no, render:{w_px,h_px},
rois: { "<key>": {x,y,w,h} } } }}` — all rects fractional.

## (b) Machine-vision pipeline (`scan/`)

Orchestrator `pipeline.run_scan(image_path, manifest, cfg) -> Decisions` with
`ScanConfig(ink_fill_threshold=0.06, inner_inset_frac=0.22, ocr_engine="tesseract", ...)`.

1. **Load (`pipeline`)** — `cv2.imread` (or rasterize first PDF page via PyMuPDF
   at ~226 dpi); EXIF-transpose; produce gray + Otsu/adaptive binary ink mask.
2. **Reg marks (`rectify.find_reg_marks`)** — quadrant-restricted search; pick the
   cross-shaped component per corner via a "plus-ness" score; sub-pixel centroid;
   raise `RegistrationError` if <4. Returns `{tl,tr,bl,br: (x,y)}`.
3. **Rectify (`rectify.rectify`)** — `getPerspectiveTransform` from detected marks
   to manifest `reg:*` centers scaled to a canonical canvas (width 1404 px, height
   from manifest aspect); `warpPerspective`. Output frame == manifest frame, so
   `roi.{x,y,w,h} × canvas` = exact pixel rect. Validate residual; store for QA.
4. **QR decode (`qr`)** — `QRBackend` Protocol; default `cv2.QRCodeDetector`,
   `pyzbar` fallback. `decode_header` (page:qr) selects the manifest page +
   asserts bucket/date; per-row `<id>:qr` verifies the manifest key (belt-and-
   suspenders), scoped to each ROI.
5. **Ink (`ink.detect_box`)** — crop ROI rect from rectified binary; inset by
   `inner_inset_frac` to **exclude the printed border** (≈2.5 px stroke vs ≥22%
   inset); `fill_ratio = dark/total`; `inked = fill > threshold`. Defer trios use
   `select_one` (argmax above threshold). Slots/capture/act use a lower threshold
   only to decide *whether OCR is needed*.
6. **OCR (`ocr`)** — `OcrEngine` Protocol `read(image_rgb, hint) -> str`.
   `NullEngine` (returns "", default for deterministic core tests) and
   `TesseractEngine` (pytesseract, psm tuned per region) implemented;
   `get_engine(name)` factory; Claude-vision documented as a future engine.
   Invoked **only** where ink is present: `edit` ticked → OCR `<id>:act` + inked
   slots; any inked `slot_*` → OCR that slot; inked capture line → OCR
   `capture:N<i>:line`.
7. **Decisions (`decisions`)** — resolve gutter verbs into one `action` per task
   with bucket precedence (e.g. done > defer), `edited` flag orthogonal, conflicts
   → `warnings`; attach OCR field edits + captures. Emit JSON.

**Decisions schema** `gtd.decisions/1`: `{schema, source_image, manifest, bucket,
date, header_qr, rectify:{residual_px,reg_marks_found}, tasks:[{id, qr_verified,
action, defer_period?, edited, fields:{<f>:{text,ocr_conf}}, ticks:{<verb>:{inked,
fill}}}], captures:[{line,text,inked,ocr_conf}], warnings:[...]}`. Raw `ticks`
evidence retained for auditability. `gtd-scan IMAGE --manifest M.json [--page KEY]
[--ocr tesseract|null] -o decisions.json`.

## Dependencies (`pyproject.toml`, PEP 621)

- base: `numpy`, `Pillow`.
- extra `gen`: `playwright>=1.40, pypdf>=4.0, Jinja2>=3.1, qrcode>=7.4`.
- extra `scan`: `opencv-python>=4.8, pyzbar>=0.1.9, pytesseract>=0.3.10, PyMuPDF>=1.23`.
- extra `dev`: `pytest, pdf2image` + `[gen,scan]`.
- **System deps (README + CI):** `playwright install chromium`; IBM Plex fonts
  optional (else Google Fonts at render — offline caveat); `libzbar0`,
  `tesseract-ocr`, `poppler-utils`.

## Verification

- **Unit:** `test_manifest` (every task/ROI present, rects in [0,1], reg marks in
  corners, no box overlap, **visual-stability** vs render-without-data-roi /
  `example.pdf`); `test_ink` (white box → fill≈0, X → inked; sweep inset; defer
  `select_one`); `test_rectify` (apply known rotation+keystone, recover marks
  ≤2 px; occluded corner → `RegistrationError`); `test_qr` (header + row decode);
  `test_decisions` (precedence, edited orthogonality, conflict warnings).
- **End-to-end (`test_end_to_end`)** — the generator is the ground-truth
  simulator: render tiny sheet + manifest → rasterize → `paint_ink` chosen
  ticks/slots/captures into manifest ROIs → optional `warp` → `run_scan` →
  assert decisions match. Use `NullEngine` for deterministic ink-trigger
  assertions; a tesseract variant with fuzzy string match for OCR. Plus a
  no-marks page → all `action: none` (false-positive guard).
- **Manual smoke:** `gtd-gen tests/fixtures/tasks.min.json --out /tmp/t.pdf`
  produces PDF + manifest; rasterize, hand-annotate, `gtd-scan` → inspect JSON.
- **CI:** job A (scan-unit, no browser) runs CV tests against `example.pdf`;
  job B installs `[gen]` + chromium and runs render + e2e tests.

## Build sequence
1. Scaffold packaging; port generator to `gen/` with `importlib.resources`; wire
   `gtd-gen`; confirm PDF output. 2. `data-roi` attrs + `manifest.py` + sidecar +
   `test_manifest` (incl. visual stability). 3. `common/geometry`+`schema`+
   `manifest_io`. 4. `rectify` then `qr`. 5. `ink`. 6. `ocr` (Null + Tesseract).
   7. `decisions`. 8. `pipeline` + `gtd-scan`. 9. e2e tests + CI. Commit and push
   to `claude/sweet-mccarthy-CvVAW`.
