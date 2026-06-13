# remarkable-gtd

GTD (Getting Things Done) paper workflow for a reMarkable 2 e-ink tablet.

## Overview

This package generates tall, auto-height PDF pages (one per GTD bucket:
**Inbox / Next Actions / Delegated / Tickler**) and provides a machine-vision
pipeline that reads handwritten marks from a scanned/photographed sheet.

It also integrates directly with an **Obsidian GTD vault** (e.g. `~/gtd`) to
automate the full daily loop: extract tasks → generate PDF → upload to
reMarkable → (later) download annotations → scan marks → update vault.

## Installation

```bash
pip install -e ".[gen,scan,dev]"
playwright install chromium
```

System dependencies (Ubuntu/Debian):
```bash
sudo apt-get install libzbar0 tesseract-ocr poppler-utils
```

For reMarkable annotation rendering (optional):
```bash
pip install -e ".[rm]"   # installs rmscene for v6 annotation support
```

## Usage

### Low-level commands

#### Generate a GTD sheet from tasks.json

```bash
gtd-gen tasks.json --out today.pdf
```

Options:
- `--date YYYY-MM-DD` — override sheet date
- `--html debug.html` — also dump per-bucket HTML for debugging
- `--manifest PATH` — custom manifest output path
- `--no-manifest` — suppress manifest sidecar

#### Scan a marked sheet

```bash
gtd-scan scan.png --manifest today.manifest.json -o decisions.json
```

Options:
- `--page KEY` — select a specific page from the manifest
- `--ocr null|tesseract` — OCR engine (default: null)

### Vault-integrated commands (new)

These commands operate directly on an Obsidian GTD vault.

#### Parse vault into tasks.json

```bash
gtd-vault-to-tasks --gtd-dir ~/gtd -o tasks.json
```

Reads `Inbox.md`, `Next actions.md`, `Delegated.md`, and `Tickler/*.md`,
producing the JSON format expected by `gtd-gen`.

#### Daily workflow — generate and upload

```bash
gtd-daily [--gtd-dir ~/gtd] [--remarkable-folder "GTD Daily"]
```

1. `git pull` the vault
2. Parse all vault files into `tasks.json`
3. Generate PDF + manifest via `gtd-gen`
4. Upload both to reMarkable (timestamped name)

#### Process annotations — download, scan, apply

```bash
gtd-process [--gtd-dir ~/gtd] [--remarkable-folder "GTD Daily"]
```

1. `git pull` the vault
2. Download latest annotated `.rmdoc` from reMarkable
3. Render v6 annotations onto a flat PDF
4. Scan each page against the manifest
5. Apply decisions (done → remove, defer → tickler, captures → inbox, etc.)
6. `git commit` and `git push`
7. Regenerate fresh PDF and upload

#### Render reMarkable annotations

```bash
gtd-render-annotations download.rmdoc annotated.pdf
```

Extracts the PDF and `.rm` stroke files from an `.rmdoc` zip, parses v6
annotations with `rmscene`, and draws them onto the PDF with `PyMuPDF`.

#### Scan a multi-page annotated PDF

```bash
gtd-scan-pdf annotated.pdf --manifest sheet.manifest.json -o decisions.json
```

Renders each page of the PDF to an image and runs the full `gtd-scan`
pipeline (rectify → QR → ink → OCR → decisions) for each page.

#### Apply decisions to vault

```bash
gtd-apply-decisions decisions.json --tasks-json tasks.json --gtd-dir ~/gtd
```

Applies scanned decisions back to the GTD vault files.

## Docker

A Docker Compose stack is provided for containerized operation.

### Setup

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your settings:
   - `GTD_REPO_URL` — SSH URL to your GTD git repository
   - `GTD_GIT_BRANCH` — branch name (default: `master`)
   - `REMARKABLE_FOLDER` — reMarkable folder (default: `GTD Daily`)

3. Place your SSH private key at `./secrets/ssh_private_key`:
   ```bash
   mkdir -p secrets
   cp ~/.ssh/id_ed25519 secrets/ssh_private_key
   ```

### Running

```bash
# Build the image
docker compose build

# Run daily workflow
docker compose run --rm gtd gtd-daily

# Process annotated sheet
docker compose run --rm gtd gtd-process

# Run individual commands
docker compose run --rm gtd gtd-vault-to-tasks
docker compose run --rm gtd gtd-scan-pdf /data/output/annotated.pdf --manifest /data/output/sheet.manifest.json
```

### Cron scheduling

Add to your host crontab:
```bash
# Daily at 5 AM
0 5 * * * cd /path/to/remarkable-gtd && docker compose run --rm gtd gtd-daily
```

## Project structure

```
remarkable-gtd/
  src/remarkable_gtd/
    gen/          — PDF generation (Playwright/Chromium)
    scan/         — Machine vision pipeline
    common/       — Shared geometry / schema helpers
    vault/        — GTD vault parser + decisions applier
    rm/           — reMarkable API wrapper + annotation renderer
    cli/          — All CLI entry points
  scripts/        — Shell helpers for rmapi upload/download
  tests/          — pytest suite
```

## Testing

### Run the full test suite

```bash
pytest --tb=short
```

### Run specific test groups

```bash
# Vault parser tests (no browser needed)
pytest tests/test_vault_parser.py -v

# Vault applier tests (no browser needed)
pytest tests/test_vault_applier.py -v

# Scan-only tests (no browser needed)
pytest -k "not (end_to_end or manifest)" --tb=short

# Individual test files
pytest tests/test_ink.py -v
pytest tests/test_decisions.py -v
pytest tests/test_rectify.py -v
```

### Test with your real GTD vault

```bash
# Parse your vault and inspect the output
PYTHONPATH=src python -m remarkable_gtd.cli.vault_to_tasks --gtd-dir ~/gtd -o /tmp/my_tasks.json

# Generate a PDF from your vault
PYTHONPATH=src python -m remarkable_gtd.cli.vault_to_tasks --gtd-dir ~/gtd | \
  PYTHONPATH=src python -m remarkable_gtd.gen.cli --out /tmp/my_sheet.pdf

# Test the applier on a copy of your vault
cp -r ~/gtd /tmp/test_gtd
PYTHONPATH=src python -c "
from remarkable_gtd.vault.parser import build_tasks_json
from remarkable_gtd.vault.applier import apply_task_decision
from pathlib import Path
import json

gtd_dir = Path('/tmp/test_gtd')
tasks = build_tasks_json(gtd_dir)
tasks_path = Path('/tmp/test_tasks.json')
tasks_path.write_text(json.dumps(tasks))

# Simulate marking NA-01 as done
result = apply_task_decision('NA-01', 'done', {}, tasks, gtd_dir)
print(result)
"
```

### Test the annotation renderer (requires rmscene)

```bash
# If you have an .rmdoc file from reMarkable
gtd-render-annotations my_file.rmdoc annotated.pdf
```

### End-to-end test (requires Playwright/Chromium)

```bash
pytest tests/test_end_to_end.py -v
```

This renders a test sheet, paints synthetic ticks into the ROIs, and verifies
the scan pipeline detects them correctly.
