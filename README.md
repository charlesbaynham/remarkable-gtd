# remarkable-gtd

GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet.

## Overview

Two halves, bridged by a **layout manifest**:

1. **Generation (`gtd-gen`)** — renders a tall, auto-height page per bucket
   (Inbox / Next Actions / Delegated / Tickler), then a read-only Projects
   summary and one page per project, with a row per task, a QR code per row,
   labelled tick boxes in a right-hand gutter, blank metadata slots and blank
   capture rows. Chromium measures every box and the
   manifest (normalised rectangles keyed by `<task id>:<verb>`) is written
   next to the PDF **and embedded inside it** together with the task list, so a
   sheet that comes back from the device carries everything needed to read it.
2. **Scanning (`gtd-scan-pdf` / `gtd-scan`)** — machine vision reads the
   handwritten marks: rectify against the four corner marks, verify the QR
   codes, measure ink fill inside every known box, and resolve the ticks to
   one decision per task. Only where ink is found in a *write-in* region
   (metadata slot, capture line, or an amended action) is a small crop sent to
   a handwriting engine — by default a vision LLM through OpenRouter, so a
   whole sheet costs a handful of tiny image requests.

**Deterministic first, AI only by explicit opt-in.** Everything the sheet can
say with a tick box, a QR, a fixed slot or a printed id is applied by plain
Python with no model involved — that is what all the fiducials and labelled
boxes are for. A vision model runs only to transcribe handwriting found in an
inked write-in region, and to interpret a whole row when you explicitly asked
for it by ticking ✎ Edit. That agent may return no operations at all, and is
told to say "not understood" rather than guess: ambiguity is reported, never
resolved silently.

The vault side (turning a GTD vault into `tasks.json`, applying the decisions
back, the nightly schedule) lives with the vault: see `.gtd/remarkable/` in the
[gtd repository](https://gitlab.com/charlesbaynham/gtd). This package knows
nothing about markdown — it takes a tasks JSON in and gives a decisions JSON
out.

## Installation

```bash
pip install -e ".[dev]"
playwright install --with-deps chromium     # for generation
```

Optional extras: `[tesseract]` (offline OCR fallback, needs the `tesseract`
binary) and `[zbar]` (second QR decoder, needs `libzbar0`). The reMarkable
cloud is reached through the [`rmapi`](https://github.com/ddvk/rmapi) binary
(`RMAPI_BIN` if it is not on `PATH`).

## Usage

### Generate a sheet

```bash
gtd-gen tasks.json --out today.pdf [--date 2026-06-01] [--html debug.html]
```

`tasks.json` (see `tests/fixtures/tasks.example.json`):

```json
{
  "date": "2026-06-01",
  "inbox":     [{"act": "...", "handle": "opaque caller id"}],
  "next":      [{"act": "...", "pri": 7, "due": "2 Jun", "proj": "test"}],
  "delegated": [{"act": "...", "to": "Dave", "due": "9 Jun"}],
  "tickler":   {"week": [{"act": "..."}], "month": [], "quarter": []},
  "projects":  [{"name": "epsrc", "goal": "Submit the grant",
                 "status": ["costings with the research office"],
                 "stalled": false,
                 "items": [{"text": "Draft the case", "done": true, "handle": "..."},
                           {"text": "Send to Oliver", "done": false,
                            "handle": "...", "surfaced": "next"}]}],
  "context":   {"projects": ["test", "epsrc"], "people": ["Dave", "Priya"]}
}
```

Pages come out in a fixed order — inbox, next, delegated, tickler, projects,
project-01, project-02… — because the scanner matches PDF pages to manifest
keys by position.

Ids (`IN-01`, `NA-01`, `DG-01`, `TK-01`…) are assigned in order; any extra
keys (such as a caller's `handle`) ride along into the embedded
`gtd.tasks.json` (schema `gtd.tasks/1`, `{id: item}`) so the scan result can
be mapped back to whatever produced the task. The optional top-level
`context` (`projects`, `people`) is the vocabulary the sheet was printed
against; it rides along in the embedded tasks document too, and is handed to
the model reading a ✎-edited row so it can match handwriting against real
project/person names instead of guessing spelling.

### Capture rows and project pages

The Inbox page ends with six blank rows `CP-01`…`CP-06`. Each is a full inbox
row — write the new item on the ruled area and tick in the same gutter where
it should go (minus ✎ Edit: there is no printed row to re-read), fill in priority/due/project as usual, and tick **NEW** next to
the PROJECT slot if the project you wrote does not exist yet. The `NEW` box
(`<id>:new_project`) is on inbox, next-action and delegated rows too, and
comes back as an orthogonal `new_project` flag, never an action.

`projects` adds a read-only summary page — one block per project with its
goal, open-item count, current next action and a badge saying which view that
action is surfaced in (`NA`/`DG`/`SC`/`TK`, or `STALLED`) — followed by one
page per project. A project page prints every unchecked item as a row
(`P01-03`, gutter ✓ Done + ✎ Edit only), lists the checked ones struck
through, and ends with four blank add-an-action lines (`P01-C1`…`P01-C4`).
The summary is marked `scan: false` in the manifest and the scanner skips it
entirely.

Each summary block is a PDF GoTo link to that project's page, and each
project page has a `← Projects` link back. Whether the reMarkable's own
reader follows internal links is untested firmware behaviour — the page
numbers are printed on the summary (`P01 · p6`) so the sheet still navigates
by hand if it does not.

### Scan an annotated sheet

```bash
# .rmdoc straight from `rmapi get "GTD Daily/<name>"` — strokes are rendered
# onto the PDF, manifest and tasks come from the PDF's embedded files:
gtd-scan-pdf sheet.rmdoc --ocr openrouter -o decisions.json

# an already-flattened PDF, or a sheet from before manifests were embedded:
gtd-scan-pdf annotated.pdf --manifest today.manifest.json -o decisions.json

# a single page image (photo/scan):
gtd-scan page.png --manifest today.manifest.json -o decisions.json
```

Handwriting engines (`--ocr`): `openrouter` (needs `OPENROUTER_API_KEY`;
`OPENROUTER_MODEL` picks the model, default Google Gemini Flash, and
`OPENROUTER_EDIT_MODEL` overrides it for the ✎ EDIT agent alone — that call
reasons about your vault rather than reading glyphs, so it is worth a
stronger model), `tesseract`, or `null` (flag inked regions, transcribe
nothing).

**Reasoning** is on for the ✎ EDIT agent and off for slot transcription.
`OPENROUTER_REASONING` (default `medium`) and `OPENROUTER_READ_REASONING`
(default `off`) each take an effort level (`low`/`medium`/`high`), a token
budget (`1500`), or `off`.

The split is measured, not a guess. Per call on `google/gemini-3.5-flash`:

| call | cost | prompt | answer | reasoning |
|---|---|---|---|---|
| `read` | $0.0043 | ~345 | 56–84 | 284–288 |
| `interpret` | $0.0102–0.0118 | ~1205 | 211–231 | 638–862 |

Reasoning is ~44% of the price of a `read` and roughly triples its latency
(3 s → 9–15 s), and it changed no transcription on the test sheet — it is
reading glyphs, not thinking. On `interpret`, which reasons about the vault,
it does visible work.

⚠️ **Reasoning tokens are charged against `max_tokens`**, so enabling it
*without* raising the budget truncates the reply mid-JSON
(`finish_reason: length`) and the EDIT agent's operations are lost. The engine
adds `REASONING_TOKEN_HEADROOM` on top of the answer budget whenever reasoning
is on.

**Tracing.** Set `OPENROUTER_TRACE_DIR` and every call writes
`NNN-{read,interpret}.json` — the prompt, the model, the reasoning block sent,
the model's thinking, and the complete raw reply — beside `NNN-…-crop0.png`,
the exact pixels sent. Nothing else in the pipeline keeps any of that.

### Check alignment by eye

```bash
gtd-overlay today.pdf -o overlay/          # manifest from the PDF; rectified like a real scan
gtd-overlay sheet.rmdoc -o overlay/         # strokes rendered first
gtd-overlay page.png --manifest m.json --raw --labels
```

Writes one PNG per page with every manifest region outlined (blue tick
boxes with their measured inner area, red write-in slots, green QRs,
magenta registration marks). `--raw` skips rectification, which separates a
manifest problem from a registration problem.

### Render annotations only

```bash
gtd-render-annotations sheet.rmdoc annotated.pdf
```

### Calibrate the stroke transform

```bash
gtd-calibrate make --out gtd_calibration.pdf      # crosses at known positions, 4 page heights
rmapi put gtd_calibration.pdf "GTD Daily/Calibration"
#   ...trace every cross on the device, then:
rmapi get "GTD Daily/Calibration/gtd_calibration"
gtd-calibrate fit gtd_calibration.rmdoc
```

`fit` pairs each traced cross with its printed position and reports the
stroke→page transform per page and pooled, ending with the two constants
`rm/annotations.py` uses (`RM_FIT_WIDTH_PX`, `RM_X_CENTRE_PX`). Measured
2026-09-15: the device lays the page width over **1410 px** (not the panel's
1404) with x centred at 705.9 px, uniform on both axes, no dependence on page
height, rms 0.24 mm over 78 targets. The nominal 72/226 was 0.42 % too large,
which put ink 2.6 mm low at the foot of a 620 mm page — enough to push a name
written in a TO box out of its slot. Re-run this if a firmware update moves
the ticks.

## Decisions JSON (`gtd.decisions/1`)

```json
{"pages": [{
  "page_key": "GTD|next|2026-06-01", "bucket": "next",
  "rectify": {"residual_px": 0.4, "reg_marks_found": 4},
  "tasks": [{"id": "NA-02", "action": "to_deleg", "edited": false,
             "new_project": false, "qr_verified": true,
             "fields": {"to": {"text": "Dave", "fill": 0.05}},
             "ticks": {"done": {"inked": false, "fill": 0.0}, "...": {}}}],
  "captures": [],
  "warnings": []
}, {
  "page_key": "GTD|projects|2026-06-01", "page_no": 5, "skipped": true
}]}
```

Actions per bucket: inbox `to_next | to_deleg | drop | defer`; next
`done | to_deleg | defer`; delegated `done | to_me | defer`; tickler
`activate | done | defer` (re-defer). `defer` carries `defer_period`
(`1w`/`1m`/`1q`). A project-page item can only be `done`; a blank capture row
takes the inbox routing verbs but carries no ✎ box. `edited` is set when the
✎ box is ticked and
`new_project` when the NEW box is; both are flags, never actions. Raw fill
ratios stay under `ticks` for auditing. The read-only projects summary is
returned as `{"page_key", "page_no", "skipped": true}`.

When ✎ is ticked, the whole row is cropped (the manifest's `<id>:row` ROI, or
the union of the task's other ROIs on a sheet printed before `row` existed)
and sent to the OCR engine's `interpret()` call along with the row's printed
fields (`act`, `bucket`, `pri`, `due`, `proj`, `to`/`period`/`proj`), today's
date, and the vocabulary from the tasks document's `context`.

This is the **only** place a model decides anything: ticks, QRs and slots are
read deterministically, and the agent runs because you asked it to by ticking
the box. It is given a brief — what each GTD list means, what each vault
operation does, the printed row, today, your project and people names — and
replies with a `gtd.edit/2` object, stored under `edit`:

```json
{"handwriting": "→ Louise, chase Fri", "understood": true, "confidence": 0.92,
 "note": "handed over to Louise with a chase-by date",
 "operations": [
   {"op": "move", "to": "delegated", "person": "Louise", "due": "2026-09-19",
    "text": null, "priority": null, "project": null, "period": null,
    "name": null, "goal": null}]}
```

- `handwriting` — verbatim transcription of everything handwritten in the crop.
- `understood` — `false` means the model could not work out the intent;
  `operations` is then empty and `note` says why, rather than guessing.
- `operations` — none, one or several. `op` is one of `update`, `complete`,
  `delete`, `move` (with `to` = `next`/`delegated`/`inbox`/`scheduled`/
  `tickler`/`project`, `period` `1w`/`1m`/`1q` for the tickler), `capture`,
  `add_next_action`, `delegate`, `schedule`, `add_to_tickler`,
  `create_project` (`name`, `goal`), `add_project_action` (`name`, `text`).
  The row's own item is implied by `update`/`complete`/`delete`/`move`.
  Every key is present on every operation; the inapplicable ones are `null`.
- `note` — one sentence on how the row was read, or why it wasn't.

A gutter tick is read separately and always wins over the agent; the agent is
told not to repeat it. `act_text` is still set (from the first operation
carrying new `text`, or from a plain re-read of the action-only crop when the
engine has no `interpret()`) so older consumers keep working.

Blank capture rows (`CP-*`, `P01-C*`) carry no printed text: their whole
action area is the write-in region, measured with the slot thresholds and
transcribed only when there is ink. They come back with `inked` and, if
written on, `act_text` — plus whatever the gutter says, on the Inbox page.

## Project structure

```
src/remarkable_gtd/
  gen/      — PDF generation (Playwright/Chromium), manifest export
  scan/     — rectify / qr / ink / ocr / decisions / pipeline / sheet
  common/   — schema constants, geometry, embedded PDF state
  rm/       — rmapi wrapper + v6 stroke renderer (rmscene + PyMuPDF)
  cli/      — gtd-scan-pdf, gtd-render-annotations
tests/      — pytest suite; fixtures/annotated_scan is a real hand-ticked sheet
```

## Testing

```bash
pytest --tb=short                                          # everything (needs Chromium)
pytest --ignore=tests/test_end_to_end.py --ignore=tests/test_manifest.py   # no browser
```

`tests/test_annotated_fixture.py` scans a sheet that was actually ticked on
a reMarkable 2 and checks every decision; `tests/test_end_to_end.py` renders a
sheet, paints synthetic ink into manifest boxes and checks the scanner
recovers exactly those choices.
