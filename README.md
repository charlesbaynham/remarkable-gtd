# remarkable-gtd

GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet.

## Overview

Two halves, bridged by a **layout manifest**:

1. **Generation (`gtd-gen`)** — renders a 4-page PDF (one tall, auto-height
   page per bucket: Inbox / Next Actions / Delegated / Tickler) with a row per
   task, a QR code per row, labelled tick boxes in a right-hand gutter, blank
   metadata slots and capture lines. Chromium measures every box and the
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
  "context":   {"projects": ["test", "epsrc"], "people": ["Dave", "Priya"]}
}
```

Ids (`IN-01`, `NA-01`, `DG-01`, `TK-01`…) are assigned in order; any extra
keys (such as a caller's `handle`) ride along into the embedded
`gtd.tasks.json` (schema `gtd.tasks/1`, `{id: item}`) so the scan result can
be mapped back to whatever produced the task. The optional top-level
`context` (`projects`, `people`) is the vocabulary the sheet was printed
against; it rides along in the embedded tasks document too, and is handed to
the model reading a ✎-edited row so it can match handwriting against real
project/person names instead of guessing spelling.

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
`OPENROUTER_MODEL` picks the model, default Google Gemini Flash),
`tesseract`, or `null` (flag inked regions, transcribe nothing).

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

## Decisions JSON (`gtd.decisions/1`)

```json
{"pages": [{
  "page_key": "GTD|next|2026-06-01", "bucket": "next",
  "rectify": {"residual_px": 0.4, "reg_marks_found": 4},
  "tasks": [{"id": "NA-02", "action": "to_deleg", "edited": false, "qr_verified": true,
             "fields": {"to": {"text": "Dave", "fill": 0.05}},
             "ticks": {"done": {"inked": false, "fill": 0.0}, "...": {}}}],
  "captures": [{"line": "N1", "inked": true, "text": "Buy milk"}],
  "warnings": []
}]}
```

Actions per bucket: inbox `to_next | to_deleg | drop | defer`; next
`done | to_deleg | defer`; delegated `done | to_me | defer`; tickler
`activate | done | defer` (re-defer). `defer` carries `defer_period`
(`1w`/`1m`/`1q`). `edited` is set when the ✎ box is ticked. Raw fill ratios
stay under `ticks` for auditing.

When ✎ is ticked, the whole row is cropped (the manifest's `<id>:row` ROI, or
the union of the task's other ROIs on a sheet printed before `row` existed)
and sent to the OCR engine's `interpret()` call along with the row's printed
fields (`act`, `bucket`, `pri`, `due`, `proj`, `to`/`period`), today's date,
and the vocabulary from the tasks document's `context`. The reply is a
`gtd.edit/1` object, stored under `edit`:

```json
{"handwriting": "Tell Louise I pulled out", "understood": true, "confidence": 0.92,
 "route": "keep", "text": "Tell Louise I pulled out", "priority": null, "due": null,
 "project": null, "person": null, "note": "struck through printed text, rewritten below"}
```

- `handwriting` — verbatim transcription of everything handwritten in the crop.
- `understood` — `false` means the model couldn't work out the intent; every
  other field is then `null` and `note` says why, rather than guessing.
- `route` — where the item should end up: `keep` (stays put, other fields
  applied in place — the default), `done`, `drop`, `next`, `delegated`
  (fills `person`), `tickler_1w`/`1m`/`1q`, `inbox`, `scheduled` (fills `due`).
- `text`, `priority`, `due`, `project`, `person` — the new value for that
  field, or `null` if unchanged.
- `note` — one sentence on how the row was read, or why it wasn't.

`act_text` is still set (to `edit.text` when `edit.understood` and `text` is
non-null, or from a plain re-read of the action-only crop when the engine has
no `interpret()`) so older consumers that only look at `act_text` keep
working.

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
