# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`remarkable-gtd` is a GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet. It has two halves:

1. **Generation (`gtd-gen`)** — Renders a PDF, one tall auto-height page each for Inbox / Next Actions / Delegated / Tickler, then a read-only Projects summary, one page per project, and a New Projects page of blank rows, with task rows, checkboxes, and QR fiducials. Uses Playwright/Chromium to measure content height and emit exact-size pages.
2. **Scanning (`gtd-scan-pdf`, `gtd-scan`)** — Machine-vision pipeline that reads handwritten ticks from the annotated sheet and produces a structured decisions JSON. Handwriting is transcribed only where ink is found, by sending that crop to a vision LLM via OpenRouter.

The two halves are bridged by a **layout manifest**: the generator exports normalized bounding rectangles of every interactive element via `data-roi` attributes, and the scanner rectifies the page against 4 corner registration marks then samples ink at those exact rects. The manifest and the task list are **embedded in the PDF** as attachments, so the sheet that comes back from the device is self-describing.

This package is vault-agnostic: tasks JSON in, decisions JSON out. The Obsidian-vault adapter and the nightly GitLab CI job live in the `gtd` repository under `.gtd/remarkable/`.

## Design principle

**Deterministic first, AI only by explicit opt-in.** Everything the sheet can
express with a tick box, a QR, a fixed slot or a printed id is applied by
plain Python with no model in the loop — that is exactly why the sheet is
covered in fiducials and labelled boxes. A vision/LLM step runs only where
the user explicitly asked for it by ticking ✦ AI, plus the transcription of
handwriting found in an inked write-in slot. When the agent is invoked it is
told how GTD works and what every vault operation means, may return none,
one or several operations, and must answer "not understood" rather than
guess. Ambiguity is reported, never resolved silently.

**✦ AI is an escape hatch, and there is exactly one writer per row.** Ticking
it switches the deterministic path off for that row. The classifier still
*runs* — its result becomes the `suggestion` handed to the agent as a
plainly-labelled hint — but `resolve_task` then emits `action: "none"` and
`new_project: false`, so no deterministic write can be derived from an AI
row at all. The agent's operations are the single write, and its scope is
whatever the handwriting implies. Never reintroduce a path where both the
gutter and the agent can act on the same row: that is the bug the demotion
into `suggestion` exists to make structurally impossible.

This constrains the layout: `data-roi` attributes and the CSS are a contract
with the scanner, so changes to them must keep every region a rectangle the
manifest can name and the pipeline can measure. Add regions; do not make the
meaning of an existing one depend on a model.

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
gtd-overlay today.pdf -o overlay/                  # ROI boxes drawn on each page, for eyeballing alignment
```

## Architecture

### PDF generation pipeline (`src/remarkable_gtd/gen/`)

- `generate.py` — Core library. `render_pdf()` launches Chromium, renders each bucket's HTML via Jinja2, measures pixel height with `page.evaluate()`, and emits a PDF page cut to fit. It then attaches `gtd.manifest.json` and `gtd.tasks.json` to the PDF (`common/embedded.py`) and returns both documents. Assets (CSS, template) are loaded via `importlib.resources.files("remarkable_gtd.gen.assets")` — **not** `Path(__file__).parent`.
- `assets/template.html.j2` — Jinja2 template with `data-roi` attributes on every interactive element. The `data-roi` values use canonical verbs (e.g. `NA-01:done`, `NA-01:defer_1w`, `CP-01:act`, `NA-01:new_project`, `NA-01:ai`, `link:P01`). The ✦ AI box's verb is `ai`; sheets printed before the rename carry `edit`, which the scanner still reads as the AI flag (`decisions.AI_TICK_KEYS`).
- `assets/gtd.css` — Print stylesheet. Fixed width (`157.8mm`, reMarkable 2 panel width = 1404 px at 226 dpi, so the device shows the page 1:1); height grows to content.
- **Page order is fixed**: `inbox`, `next`, `delegated`, `tickler`, `projects`, `project-01`, `project-02`…, `new-projects`. `scan_pdf` matches PDF pages to manifest keys *by position*, and `tests/fixtures/annotated_scan` is a real device sheet with a stored 4-page manifest, so new page kinds may only be appended. `new-projects` is deliberately last for exactly that reason — do not insert anything after it either without moving it.
- **Row ids**: `IN-01`/`NA-01`/`DG-01`/`TK-01` (list rows), `CP-01`…`CP-06` (blank capture rows at the foot of the Inbox), `P01-PJ` (project 1's project row, standing for the project itself), `P01-03` (the 3rd item of project 1, printed only while unchecked), `P01-C1`…`P01-C4` (blank add-an-action lines on project 1's page), `NP-01`…`NP-06` (blank rows on the New Projects page).
- **Page kinds**: `kind` is `flat`, `sectioned` (tickler), `summary` (the projects index — no rows, `scan: false` in the manifest, skipped by the scanner), `project`, or `newproj` (the New Projects page: nothing but blank write-in rows). A manifest page entry carries `scan` and, on a project page, `project: {index, name}`.
- **Internal links**: `add_internal_links()` turns every `link:<ref>` ROI into a pypdf `Link` GoTo annotation after the render loop — summary block → project page, summary → `new-projects`, `← Projects` → summary. ROI fractions are flipped into PDF user space against the page media box.
- `manifest.py` — `collect_rois(page)` runs JS that walks `[data-roi]` elements and returns rects normalized to the `.page` bounding box (fractions 0..1). `build_manifest()` assembles the document.

### Machine-vision pipeline (`src/remarkable_gtd/scan/`)

`sheet.scan_rmdoc()` / `sheet.scan_pdf()` drive the per-page pipeline `pipeline.run_scan(image_path, manifest, cfg, page_key, tasks)`:

1. **Load** — `cv2.imread`; EXIF transpose via PIL; grayscale + Otsu binary.
2. **Reg marks** (`rectify.find_reg_marks`) — Searches 15% corner quadrants for cross-shaped components. Raises `RegistrationError` if < 4 found.
3. **Rectify** (`rectify.rectify`) — `cv2.getPerspectiveTransform` from detected marks to manifest `reg:*` centers, warped to a canonical canvas (width 1404 px). Residual is stored for QA.
4. **QR decode** (`qr.py`) — `cv2.QRCodeDetector` (pyzbar as optional second decoder). `decode_region` widens the crop progressively — the header QR needs a bigger quiet zone than the row QRs. `decode_header` checks `page:qr`; `decode_task_qrs` verifies per-row identity.
5. **Ink detection** (`ink.py`) — `detect_box()` crops the ROI from the rectified binary, insets by `inner_inset_frac` (0.22 for tick boxes, 0.15 for slots) to exclude the printed border, and measures dark-pixel fill ratio. Tick threshold 0.06, slot/capture threshold 0.03. Slots are deliberately much larger than their contents (8 mm tall) — a single digit in a 10×8 mm box is still ≈6 % fill, so the thresholds did not move when they grew. ⚠️ A fill ratio is area-blind, and a capture line is 53 mm wide: "Test" on one is 2.3 % fill and was silently dropped (gtd#3). Every write-in region (slots, `act` on a capture/newproj row) therefore also passes `min_ink_px` — an absolute floor of `write_in_ink_mm2` (1.5 mm², about one handwritten character, `ink_floor_px()`) — and ink below it is reported as a warning rather than ignored.
6. **Handwriting** (`ocr.py`) — `OcrEngine` Protocol `read(image, hint, context)`. `OpenRouterEngine` (default in production; `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `OPENROUTER_AI_MODEL` for `interpret()` alone — falls back to the pre-rename `OPENROUTER_EDIT_MODEL`, then `OPENROUTER_MODEL`, then the default), `TesseractEngine`, `NullEngine` (tests). Invoked only where ink is present, on a tight crop, with a hint naming the region (`priority`/`due`/`project`/`to`/`name`/`goal`/`capture`/`act`) and, for an amended action, the printed text as context. When a row's ✦ AI box is ticked, the pipeline instead crops the whole row (`row_roi()`) and calls the engine's optional `interpret(image, task, vocabulary, today, suggestion=...)`, which returns a structured `gtd.ai/3` reading (schema `ocr.AI_SCHEMA`) — `handwriting`, `understood`, and a list of vault `operations`. `suggestion` is the deterministic reading, rendered by `describe_suggestion()` as an explicitly inert hint; `_call_interpret` omits the keyword for an engine written against the older signature. `act_text` is set from the first operation carrying new `text`. `NullEngine.interpret()` returns `None`; engines without `interpret` (or one returning `None`) fall back to re-reading just the action crop.
7. **Decisions** (`decisions.py`) — `resolve_action()` is the whole deterministic classifier: it maps ticked verbs to a single `action` using bucket-specific precedence (`done > activate > to_next > to_me > to_deleg > drop > defer`), keyed on the task's *own* bucket from the tasks document (falling back to the page's), with no side effects. `resolve_task()` wraps it into the decisions entry. `new_project` is an orthogonal flag, never an action. `ai` is not a flag alongside the action but a replacement for it: on an AI row the entry gets `action: "none"`, `new_project: false`, and the classifier's result under `suggestion` (`build_suggestion()`). Conflict warnings are emitted when multiple boxes are ticked.

Four buckets exist only on the sheet: `capture` (a blank write-in row — the Inbox's `CP-*` rows and each project page's `P01-C*` add-lines), `newproj` (the New Projects page's `NP-*` rows: the line is a would-be project's first action, the PROJECT slot its name), `project` (a step on a project page: `done`/`to_deleg`/`drop` plus the defer trio, with DUE and TO slots — routing it re-surfaces the step and leaves it on the page) and `projhead` (the `P01-PJ` project row: `done` = the whole project is finished, `slot_name` = RENAME TO, `slot_goal` = NEW GOAL; its tasks entry carries `proj` and `goal`). `BUCKET_ACTIONS` gives `capture` and `newproj` the Inbox vocabulary — on a `newproj` row a routing tick means "on reflection this is not a project, file the text instead" — and `run_scan` measures both buckets' `act` regions with the slot thresholds plus the ink floor, transcribes them with the `capture` hint and sets `inked` and `ink_fill`.

### Debugging

`scan/overlay.py` / `gtd-overlay` draw every manifest ROI onto the rasterised (and by default rectified) page. Run it on any generated PDF, `.rmdoc` or page image first when ticks are misread: if the boxes are off, the manifest and the sheet do not belong together.

### reMarkable I/O (`src/remarkable_gtd/rm/`)

- `api.py` — thin wrapper over the `rmapi` binary (ddvk fork): `list_sheets`, `download` (`rmapi get` → `.rmdoc`), `upload`, `move`, `mkdir`; `write_config_from_env()` builds rmapi's config from `RMAPI_DEVICE_TOKEN` for headless runs.
- `annotations.py` — unpacks an `.rmdoc`, parses v6 strokes with `rmscene`, draws them onto the original PDF with PyMuPDF. ⚠️ **An erased stroke is not an unreadable one.** The tablet records an erasure as a line item with no value and the point count in `deleted_length`, so a written-on-then-rubbed-out sheet parses perfectly and yields no strokes — "no strokes" is therefore no evidence the ink could not be read. `read_annotations()` raises `UnreadableLayer` only when the blocks genuinely will not parse; `parse_annotations()` is the lenient wrapper the renderer uses. Anything deciding whether a sheet has been written on must use the former: conflating the two wedged the GTD pipeline for 13 hourly runs on 2026-09-24. Coordinate transform (calibrated 2026-09-15 with `gtd-calibrate`, see README): scale = page width / 1410 px, x shifted by +705.9 px, y 0-based, uniform. Not 72/226 and not 1404 — that was 0.42 % off and put ink 2.6 mm low at the foot of a tall page.
- `cli/calibrate.py` — `gtd-calibrate make` (target document) / `fit` (recover the transform from the traced `.rmdoc`).

### Key design decisions

- **Manifest as the bridge**: the scanner knows exactly where every box is because the generator measured it in Chromium. This is the central architectural invariant.
- **State travels in the PDF**: manifest + tasks are PDF attachments; the reMarkable keeps the uploaded PDF byte-for-byte, so nothing has to be stored between the upload run and the processing run.
- **Normalized rects**: all manifest ROIs are fractions of the page box (0..1); the scanner scales them to the rectified canvas.
- **Deterministic tests**: core CV tests use `NullEngine`/a recording engine. `tests/fixtures/annotated_scan/` is a sheet that was really ticked on a device (tasks.example.json for 2026-06-02) with its matching manifest — regenerate the manifest with `gtd-gen tests/fixtures/tasks.example.json --date 2026-06-02` if the template ever changes.
- **Bucket action vocabularies**: `BUCKET_ACTIONS` in `decisions.py`. The defer trio keys differ between normal buckets (`defer_1w/1m/1q`) and tickler (`redefer_1w/1m/1q`); both surface as action `defer` + `defer_period`.

### Schemas

- **Manifest** (`gtd.manifest/1`): `{schema, date, page_w_mm, pages: {"GTD|<page>|<date>": {bucket, page_no, render: {w_px, h_px}, scan, project?, rois: {"<key>": {x, y, w, h}}}}}`. The page key's middle part is the page name (`inbox`, `projects`, `project-01`) and `parse_page_key` requires exactly three `|`-separated parts; `bucket` is the *kind* (`project` for every `project-NN` page).
- **Tasks** (`gtd.tasks/1`, embedded): `{schema, date, tasks: {"<id>": {act, bucket, period?, pri?, due?, proj?, to?, ...caller extras}}, context?: {projects: [...], people: [...]}}`
- **Decisions** (`gtd.decisions/2`): per page `{page_key, page_no, bucket, project?, date, header_qr, rectify: {residual_px, reg_marks_found}, tasks: [{id, qr_verified, action, defer_period?, ai, new_project, inked?, act_text?, suggestion?, ai_reading?, fields: {<f>: {text, fill}}, ticks}], captures: [...], warnings}`; `suggestion` and `ai_reading` are present only on an AI row, where `action` is always `"none"`; a `scan: false` page yields `{page_key, page_no, skipped: true}` instead. `gtd-scan-pdf` wraps pages in `{schema, source_pdf, pages: [...]}`. `captures` is the legacy capture-line list, empty for sheets printed since capture lines became rows.
- **AI reading** (`gtd.ai/3`, stored under `ai_reading`, present when ✦ AI was ticked): `{handwriting, understood, confidence, note, operations: [Op]}`. `Op` is one flat, strict object — OpenRouter's strict `json_schema` mode allows no `oneOf`/`anyOf`, so every key is present and the inapplicable ones are `null`: `{op, text, priority, due, project, person, to, period, name, goal}`. `op` ∈ `update, complete, delete, move, capture, add_next_action, delegate, schedule, add_to_tickler, create_project, add_project_action, rename_project, set_project_goal, archive_project`; `to` (for `move`) ∈ `next, delegated, inbox, scheduled, tickler, project`. `build_ai_prompt` is the agent's brief: what each GTD list means, what each operation does, the printed row's context, today, the project/person vocabulary, the deterministic `suggestion` (labelled as a hint that will *not* be applied), the fact that the agent is the row's only writer, and the rule that an unclear row comes back `understood: false` with no operations. See `ocr.AI_SCHEMA`.

## CI

`.github/workflows/ci.yml`: **scan-unit** (no browser) and **full** (Playwright Chromium) jobs.
