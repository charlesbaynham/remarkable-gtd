# Handover Summary — 2026-05-31

## What We Were Doing
Getting the first GTD PDF onto the reMarkable tablet, having the user write on it, then downloading and scanning it back using the built-in tooling.

## What Works
- **PDF generation**: `gtd-gen tasks.json --out today.pdf` works. Produces 4-page PDF + manifest.
- **Upload to reMarkable**: `remarkable_gtd.rm.api.upload()` works. File at `/GTD/today`.
- **Download from reMarkable**: `remarkable_gtd.rm.api.download()` works. Downloads `.rmdoc` file.
- **Scan pipeline**: `gtd-scan-pdf` works with `--scale 1`. Detects reg marks, QR codes, ink fill.
- **All 34 tests pass**.

## What's Broken

### 1. Annotation Rendering (CRITICAL)
`src/remarkable_gtd/rm/annotations.py` renders reMarkable handwriting onto the PDF. Three bugs were found:

**Fixed**:
- Only first `.rm` file was extracted (now extracts all, maps to pages via `.content`)
- All annotations rendered on `doc[0]` (now renders on correct page)

**Still broken**:
- Coordinate transform wrong. Ticks that should be in "done" boxes land in "defer" boxes or wrong task rows.
- See `research.md` for deep analysis. Likely fix: use centered x (`+702`) and 0-based y with uniform scale `72/226`.

### 2. Scanner Default Scale
- `gtd-scan-pdf` defaults to `--scale 2`, which makes reg marks too large for the area threshold.
- **Workaround**: Always use `--scale 1`.
- **Proper fix**: Adjust `find_reg_marks` area thresholds or change default.

### 3. Playwright Browser Path (Nix-only)
- Nix flake provides browser revision 1217, Python package expects 1223.
- **Fixed** via symlink in `~/.playwright-browsers/` and `.envrc` override.

## Files to Check
- `src/remarkable_gtd/rm/annotations.py` — needs coordinate fix
- `src/remarkable_gtd/scan/rectify.py` — area thresholds may need 2× adjustment
- `today.rmdoc` / `today_fixed.pdf` — downloaded/rendered test artifacts
- `today.manifest.json` — manifest for the generated PDF
- `page_images_fixed/` — rendered page images for visual inspection

## Next Steps
1. Fix coordinate transform in `annotations.py` using findings from `research.md`
2. Re-render `today.rmdoc` → `today_fixed.pdf`
3. Run `gtd-scan-pdf today_fixed.pdf --manifest today.manifest.json --scale 1 --ocr tesseract`
4. Verify detected decisions match what the user actually wrote
5. Fix `--scale 2` default in `gtd-scan-pdf` or `find_reg_marks`

## Commands
```bash
# Generate PDF
uv run gtd-gen tasks.json --out today.pdf

# Upload
uv run python -c "from remarkable_gtd.rm.api import upload; upload(Path('today.pdf'), 'GTD')"

# Download
uv run python -c "from remarkable_gtd.rm.api import download; download('/GTD/today', Path('.'))"

# Render annotations
uv run python -c "from remarkable_gtd.rm.annotations import render_rmdoc; render_rmdoc(Path('today.rmdoc'), Path('today_fixed.pdf'))"

# Scan
uv run gtd-scan-pdf today_fixed.pdf --manifest today.manifest.json --scale 1 --ocr tesseract -o decisions.json
```
