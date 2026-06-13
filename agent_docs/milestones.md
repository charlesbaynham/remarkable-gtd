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

### M4/M5: Annotation Coordinate Transform — FIXED
**Status**: Complete — annotations now land in correct boxes.

**Fix in `src/remarkable_gtd/rm/annotations.py`:**
- Replaced per-page-dimension scaling with uniform `SCALE = 72.0 / 226` (reMarkable DPI → PDF points)
- **x**: centered around 0 (range [-702, +702]) → shift by +702 before scaling: `x = (p.x + rm_width/2) * scale`
- **y**: 0-based, no offset needed: `y = p.y * scale`
- Verified by e2e scan: NA-01 and NA-02 correctly detected as `done`

### M6: Scanner Scale Issue — FIXED
**Status**: Complete — `gtd-scan-pdf` now works with defaults.

**Fix in `src/remarkable_gtd/scan/rectify.py`:**
- Replaced hardcoded area thresholds (`area < 100 or area > 8000`) with scale-aware relative thresholds:
  - `min_area = max(100, search_area * 0.005)` (noise rejection floor)
  - `max_area = search_area * 0.15` (accommodates larger marks at higher scale)
- Replaced hardcoded `CORNER_R = 80` with scale-aware `corner_r` capped to search region bounds

**Fix in `src/remarkable_gtd/cli/scan_pdf.py`:**
- Changed `--scale` default from `2` to `1`

### M7: End-to-End Verification — PASSED
**Status**: Complete — full pipeline works.

**Results from scanning `today.rmdoc` → `today_fixed.pdf`:**
| Task | Action | Notes |
|------|--------|-------|
| NA-01 | **done** ✅ | Correct. Done box ticked (fill=0.166). Defer trio has light marks (fill=0.074) handled by precedence rule. |
| NA-02 | **done** ✅ | Correct. Done box ticked (fill=0.177). |
| NA-03 | **to_deleg** ✅ | Correct per user's actual mark. |
| NA-04 | none (edited) | Edit box ticked. |
| NA-05 | none | No marks. |
| NA-06 | none | No marks. |

All 34 tests pass (1 skipped).

## ⏳ REMAINING

### M8: Optional — rmrl Integration
- User suggested https://github.com/naturale0/rmrl as alternative rendering approach.
- May not support newest reMarkable file format (v6 .rm).
- **Not needed** — current `rmscene` + PyMuPDF approach works correctly.

## Files Changed
- `src/remarkable_gtd/rm/annotations.py` — coordinate transform fix (uniform scale, centered x, 0-based y)
- `src/remarkable_gtd/scan/rectify.py` — scale-aware reg mark thresholds
- `src/remarkable_gtd/cli/scan_pdf.py` — default scale changed from 2 to 1
- `.envrc` — added `PLAYWRIGHT_BROWSERS_PATH`
- `tests/__init__.py` — new (fixes import)
