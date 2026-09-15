"""gtd-scan-pdf — scan a multi-page annotated PDF or an .rmdoc into decisions JSON."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Scan an annotated sheet (multi-page PDF, or the .rmdoc downloaded "
            "from the reMarkable) and write a combined decisions JSON. The "
            "manifest is read from the PDF's embedded files unless --manifest "
            "is given."
        )
    )
    p.add_argument("input", help="Annotated PDF, or .rmdoc from `rmapi get`.")
    p.add_argument("--manifest", default=None, help="Manifest JSON (default: embedded in the PDF).")
    p.add_argument("--tasks", default=None, help="Tasks JSON (gtd.tasks/1; default: embedded).")
    p.add_argument(
        "--ocr", default="null", choices=["null", "tesseract", "openrouter"],
        help="Handwriting engine (default: null = flag inked regions only).",
    )
    p.add_argument("-o", "--output", default="decisions.json", help="Output decisions JSON path.")
    p.add_argument("--work-dir", default=None, help="Where page rasters / rendered PDF go (default: next to output).")
    args = p.parse_args(argv)

    from remarkable_gtd.scan.manifest_io import load_manifest
    from remarkable_gtd.scan.pipeline import ScanConfig
    from remarkable_gtd.scan.sheet import scan_pdf, scan_rmdoc, summarize, task_texts_from_tasks

    src = Path(args.input)
    out_path = Path(args.output)
    work_dir = Path(args.work_dir) if args.work_dir else out_path.parent
    cfg = ScanConfig(ocr_engine=args.ocr)

    manifest = None
    if args.manifest:
        manifest = load_manifest(args.manifest)
        manifest["_path"] = str(args.manifest)
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8")) if args.tasks else None

    if src.suffix.lower() in (".rmdoc", ".zip"):
        decisions, _, _, annotated = scan_rmdoc(src, work_dir, cfg, manifest, tasks)
        print(f"  rendered annotations -> {annotated}")
    else:
        if manifest is None:
            from remarkable_gtd.common.embedded import read_state

            manifest, emb_tasks = read_state(src.read_bytes())
            tasks = tasks or emb_tasks
            if manifest is None:
                print("Error: no --manifest given and none embedded in the PDF", file=sys.stderr)
                return 1
        decisions = scan_pdf(src, manifest, cfg, work_dir, task_texts=task_texts_from_tasks(tasks))

    out_path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    s = summarize(decisions)
    for page in decisions["pages"]:
        if "error" in page:
            print(f"  ✗ page {page['page_no']}: {page['page_key']} — {page['error']}", file=sys.stderr)
        else:
            print(f"  ✓ page {page['page_no']}: {page['page_key']} — {len(page['tasks'])} tasks")
    print(
        f"✓ wrote {out_path} ({s['pages']} pages, {s['actions']} actions, "
        f"{s['edits']} edits, {s['captures']} captures, {s['warnings']} warnings)"
    )
    return 0 if s["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
