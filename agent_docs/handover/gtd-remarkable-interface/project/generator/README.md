# GTD reMarkable Sheet — generator

Generates a minimalist **4-page** GTD PDF for the **reMarkable 2**. Each
bucket is **one page**, 157.8 mm wide (the device panel width) and **exactly
as tall as its list needs** — no A4 truncation, no blank tails. Built for two
readers at once: **your stylus** and the **nightly vision agent** that reads
your handwritten marks.

| Page | Bucket | Per-task fields | Annotation gutter |
|------|--------|-----------------|-------------------|
| 0 | **Inbox** | *(blank — assign as you route)* Priority · Due · Project · To | → Next · → Deleg · Defer `1w/1m/1q` · ✗ Drop |
| 1 | **Next Actions** | Priority · Due · Project (+ blank *To*) | ✓ Done · → Deleg · Defer `1w/1m/1q` · ✎ Edit |
| 2 | **Delegated** | **To** · Priority · Due · Project | ✓ Done · ↩ To me · Defer `1w/1m/1q` · ✎ Edit |
| 3 | **Tickler** | *(none)* — split into Next week / month / quarter | → Now · ✓ Done · Re-defer `1w/1m/1q` · ✎ Edit |

Every metadata field prints as `current → ▭` — **strike the old value and
write the new one in the box beside it**, so corrections land in a known place.

## Why it reads reliably

- **100 % black on 100 % white** — no greys/fills (e-ink dithers greys into
  OCR noise).
- **Corner registration marks** on every page to de-skew the photo.
- **Stable ID + QR fiducial** beside every task (`NA-05`) — locks the agent
  onto the exact task even with handwriting over it.
- **Fixed, labelled gutter** — a tick in a known box is an unambiguous
  command. `✎ Edit` flags "I changed this row, re-read it".
- **One page per bucket, grows to any length** — a 3-item list is one screen;
  a 60-item list is one long scrollable page on the device.

## Why Chromium (Playwright), not WeasyPrint?

A PDF page **cannot auto-fit its height to content** in WeasyPrint — its page
box is fixed, so you'd get clipping or a big blank tail. To make each section's
page exactly as long as its list, we render the *approved* HTML/CSS in headless
Chromium, measure each section's pixel height, and emit a page cut to fit. The
result is a pixel-for-pixel match of `../GTD Sheet.html`.

## Install

```bash
cd generator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium      # one-time browser download
```

For best font fidelity install **IBM Plex Sans** + **IBM Plex Mono** as system
fonts. Otherwise Chromium fetches them from Google Fonts at render time (keep a
connection on first run), or vendor the font files and swap the `<link>` in
`template.html.j2` for a local `@font-face`.

## Run

```bash
python generate.py tasks.example.json --out today.pdf
# override the date, or dump the per-bucket HTML to debug:
python generate.py tasks.json --out today.pdf --date 2026-05-30 --html debug.html
```

## Task JSON

```jsonc
{
  "date": "2026-05-30",            // optional; defaults to today
  "inbox":     [ { "act": "…" } ], // ids optional → auto IN-01, IN-02…
  "next":      [ { "id": "NA-01", "pri": 7, "due": "30 May", "proj": "epsrc", "act": "…" } ],
  "delegated": [ { "id": "DG-01", "pri": 7, "due": "1 Jun", "proj": "…", "to": "Oliver", "act": "…" } ],
  "tickler":   { "week": [ { "act": "…" } ], "month": [ … ], "quarter": [ … ] }
}
```

- **`id`** — if your nightly system tracks task IDs, pass them and the QR
  encodes that exact string. Omit and the generator assigns
  `IN-/NA-/DG-/TK-NN`. **Use stable IDs** so annotations match back cleanly.
- **`pri`** shows as-is (any scale). **`due`** / **`proj`** are free strings.

## Files

| File | Role |
|------|------|
| `generate.py` | data shaping, QR generation, measure + render → PDF |
| `template.html.j2` | the layout (Jinja2); macros mirror the HTML preview |
| `gtd.css` | **shared** visual system (same file the on-screen preview uses) |
| `tasks.example.json` | sample data |

`gtd.css` is shared with the preview, so what you approve on screen is what
the PDF produces. To re-sync after a design tweak, copy the project-root
`gtd.css` over this one.

## Page geometry

`gtd.css` sets `@page { size: 157.8mm auto }` and the generator emits each page
at `157.8 mm × <measured height>`. The width is the exact reMarkable 2 panel
(1404 px @ 226 dpi), so the sheet fills the screen and scrolls vertically.
For a different device, change `PAGE_W_MM` in `generate.py` and `width` in
`gtd.css` (e.g. Paper Pro ≈ 163 mm).
