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
for it by ticking ✦ **AI**. That agent may return no operations at all, and is
told to say "not understood" rather than guess: ambiguity is reported, never
resolved silently.

✦ AI is an **escape hatch**, not an annotation. Ticking it switches the
deterministic rules off for that row: the row's gutter ticks and slots are
still read, but only to build a *suggestion* passed to the agent as a
labelled hint, and the entry comes back with `action: "none"` so nothing
downstream can derive a write from it. The agent's operations are the row's
single write, and its scope is therefore whatever the handwriting implies —
amend the item, start a project, rename one, split the row into several
actions. One row, one writer.

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
the agent reading a ✦ AI row so it can match handwriting against real
project/person names instead of guessing spelling.

### Capture rows and project pages

The Inbox page ends with six blank rows `CP-01`…`CP-06`. Each is a full inbox
row — write the new item on the ruled area and tick in the same gutter where
it should go (minus ✦ AI: there is no printed row to re-read), fill in priority/due/project as usual, and tick **NEW** next to
the PROJECT slot if the project you wrote does not exist yet. The `NEW` box
(`<id>:new_project`) is on inbox, next-action and delegated rows too, and
comes back as an orthogonal `new_project` flag, never an action. A project
created from a row is seeded with **that row's own text as its first and
only action** — nothing is invented on your behalf.

`projects` adds a read-only summary page — one block per project with its
goal, open-item count, current next action and a badge saying which view that
action is surfaced in (`NA`/`DG`/`SC`/`TK`, or `STALLED`) — followed by one
page per project. A project page prints every unchecked item as a row
(`P01-03`, gutter ✓ Done + ✦ AI only), lists the checked ones struck
through, and ends with four blank add-an-action lines (`P01-C1`…`P01-C4`).
The summary is marked `scan: false` in the manifest and the scanner skips it
entirely.

### The New Projects page

The sheet's **last** page, `new-projects`, is the Inbox's blank capture
lines one level up: six blank rows `NP-01`…`NP-06` for projects that do not
exist yet. Write the project's first action on the ruled line and its name
in the PROJECT box; tick nothing else and the project is created with that
action as its first.

Each row carries the full Inbox routing gutter plus ✦ AI, because something
you wrote down as a project often turns out to be one delegable action, or
something to defer, or nothing at all. A routing tick means exactly that:
*do not create a project* — file this text like any Inbox item, with the
PROJECT slot then naming an existing project to file it under. ✦ AI hands
the row to the agent instead, which is how you say more than a name and a
line will carry.

Its rows are bucket `newproj` in the tasks document (the Inbox verb
vocabulary; see `BUCKET_ACTIONS`), and the page is appended rather than
inserted — `scan_pdf` matches PDF pages to manifest keys by position, so
every existing page keeps its index.

Each summary block is a PDF GoTo link to that project's page, the summary
also links to the New Projects page, and each of those has a `← Projects`
link back. Whether the reMarkable's own
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
`OPENROUTER_AI_MODEL` overrides it for the ✦ AI agent alone — that call
reasons about your vault rather than reading glyphs, so it is worth a
stronger model), `tesseract`, or `null` (flag inked regions, transcribe
nothing).

**Reasoning** is on for the ✦ AI agent and off for slot transcription.
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
(`finish_reason: length`) and the AI agent's operations are lost. The engine
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

## Decisions JSON (`gtd.decisions/2`)

```json
{"pages": [{
  "page_key": "GTD|next|2026-06-01", "bucket": "next",
  "rectify": {"residual_px": 0.4, "reg_marks_found": 4},
  "tasks": [{"id": "NA-02", "action": "to_deleg", "ai": false,
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
and a New Projects row take the inbox routing verbs (the capture row carries
no ✦ AI box). `new_project` is set when the NEW box is ticked — a flag, never
an action. Raw fill ratios stay under `ticks` for auditing. The read-only
projects summary is returned as `{"page_key", "page_no", "skipped": true}`.

`ai` is set when the ✦ AI box is ticked, and it is not a flag alongside the
action — it *replaces* it. Such an entry always has `action: "none"` and
`new_project: false`, and the deterministic reading it would otherwise have
produced moves into `suggestion`:

```json
{"id": "IN-01", "ai": true, "action": "none", "new_project": false,
 "suggestion": {"action": "to_deleg", "new_project": true,
                "fields": {"to": "Louise"}, "text": null}}
```

`suggestion` exists to be read — by the agent, as a labelled hint, and by
you, when auditing what the boxes said — and never to be applied. Applying
it and the agent's operations both would give one row two writers, which is
the thing the escape hatch exists to prevent.

When ✦ AI is ticked, the whole row is cropped (the manifest's `<id>:row` ROI,
or the union of the task's other ROIs on a sheet printed before `row` existed)
and sent to the OCR engine's `interpret()` call along with the row's printed
fields (`act`, `bucket`, `pri`, `due`, `proj`, `to`/`period`/`proj`), today's
date, the vocabulary from the tasks document's `context`, and that
`suggestion`.

This is the **only** place a model decides anything: ticks, QRs and slots are
read deterministically, and the agent runs because you asked it to by ticking
the box. It is given a brief — what each GTD list means, what each vault
operation does, the printed row, today, your project and people names, the
deterministic suggestion, and the fact that it alone writes for this row —
and replies with a `gtd.ai/3` object, stored under `ai_reading`:

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

The agent's operations are the row's **only** write: the gutter tick does not
get applied behind it, which is why the brief says so explicitly — a routing
box that should still take effect has to come back as an operation, or it
does not happen. `act_text` is still set (from the first operation carrying
new `text`, or from a plain re-read of the action-only crop when the engine
has no `interpret()`) so older consumers keep working.

Blank capture rows (`CP-*`, `P01-C*`) and New Projects rows (`NP-*`) carry no
printed text: their whole action area is the write-in region, measured with
the slot thresholds and transcribed only when there is ink. They come back
with `inked` and, if written on, `act_text` — plus whatever the gutter says,
on the Inbox and New Projects pages.

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
