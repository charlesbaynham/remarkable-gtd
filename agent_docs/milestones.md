# remarkable-gtd — Milestones & Remaining Work

## ✅ COMPLETED

### M1: Environment Setup & Initial Fixes
- **Playwright browser version mismatch fixed** — Nix flake provides browser revision 1217, Python package 1.60.0 expects 1223. Created symlinks in `~/.playwright-browsers/` and updated `.envrc` to set `PLAYWRIGHT_BROWSERS_PATH`.
- **E2E test import failure fixed** — Added `tests/__init__.py` so `from tests.conftest import ...` works.
- **All 34 tests pass** (1 skipped).

### M2: First PDF Generated & Uploaded
- Created `tasks.json` with sample data for 2026-05-31.
- Generated `today.pdf` (4 pages) + `today.manifest.json` via `gtd-gen`.
- Uploaded to reMarkable Cloud at `/GTD/today` using `remarkable_gtd.rm.api.upload()`.

### M3: Annotation Rendering Bugs Identified
- Downloaded annotated rmdoc via `remarkable_gtd.rm.api.download()`.
- **Bug 1**: `extract_from_rmdoc()` only extracted the FIRST `.rm` file (alphabetically). An rmdoc has one `.rm` per annotated page.
- **Bug 2**: `render_annotations()` always rendered on `doc[0]`. Annotations from all pages were drawn on the inbox page.
- **Bug 3**: Coordinate transform used `+ rm_width/2` and `+ rm_height/2` offsets. Research shows x is centered but y is 0-based (see `research.md`).
- Fixed bugs 1 & 2 in `src/remarkable_gtd/rm/annotations.py`. Re-rendered PDF now shows annotations on correct pages.

## 🔄 IN PROGRESS

### M4: Annotation Coordinate Transform
**Status**: Partially fixed — annotations now on correct pages, but y-coordinate mapping is still off.

**Problem**: When scanning the rendered annotated PDF, ticks that appear visually in "done" boxes are detected in "defer" boxes or not at all.

**Root cause hypothesis**: The reMarkable v6 `.rm` coordinate system is:
- **x**: centered around 0 (range approx [-702, +702] for 1404px width)
- **y**: 0-based (range approx [0, 1872])
- Scale: uniform `72/226` pts/px for both axes (confirmed by `rmc` SVG exporter)

Current code in `annotations.py` (after fixes) uses 0-based for BOTH x and y, which is wrong for x.

**Evidence**:
- `rmc` (ricklupton's reference tool) uses `SCALE = 72.0/226`, `xx = scale`, `yy = scale` with no offset for SVG (SVG handles negative coords via viewBox).
- For PDF/PyMuPDF, x needs shifting: `x = (p.x + 702) * SCALE`.
- For y, the correct transform is unclear — the PDF page heights (720, 814, 599 pts) don't match `SCREEN_HEIGHT * SCALE = 596.3` pts.

**Empirical test results** (see `research.md` for full data):
- Transform `x = (p.x + 702) * (pdf_width/1404), y = p.y * (pdf_height/1872)`: Maps lines to NA-04/05/06 area, not NA-01/02/03.
- Transform `x = (p.x + 702) * SCALE, y = (p.y + 936) * SCALE`: Maps lines to NA-05/06 + off-page.
- Transform `x = (p.x + 702) * SCALE, y = p.y * SCALE`: Maps lines to NA-04/05/06.
- None perfectly match the 6 visible marks.

**Complication**: The user's rmdoc has 12 lines on the next-actions page, but the rendered image shows ~6 marks in done boxes. Some marks may be from a different source (e.g., original PDF artifacts, or other .rm files rendered on wrong pages before the fix).

## ⏳ REMAINING

### M5: Fix Annotation-to-PDF Coordinate Mapping
- Determine correct transform from reMarkable screen coordinates to PDF points.
- Verify by comparing rendered positions against manifest ROI positions.
- Re-run e2e tests after fix.

### M6: Scanner Scale Issue
- `gtd-scan-pdf` default `--scale 2` causes reg marks to exceed area threshold (8000 px²), failing detection.
- **Workaround**: Use `--scale 1` (confirmed working).
- **Proper fix**: Either adjust `find_reg_marks` area thresholds for 2× images, or change default scale to 1.

### M7: End-to-End Verification
- User writes on reMarkable → download → render → scan → verify decisions match what was written.
- Currently the scan detects `NA-01: defer_1w` when user likely ticked `done`.

### M8: Optional — rmrl Integration
- User suggested https://github.com/naturale0/rmrl as alternative rendering approach.
- May not support newest reMarkable file format (v6 .rm).

## Files Changed
- `src/remarkable_gtd/rm/annotations.py` — major rewrite (multi-page .rm support, page mapping, coordinate transform)
- `.envrc` — added `PLAYWRIGHT_BROWSERS_PATH`
- `tests/__init__.py` — new (fixes import)
