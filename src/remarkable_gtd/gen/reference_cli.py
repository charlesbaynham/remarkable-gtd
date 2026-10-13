"""gtd-gen-ref — render one markdown note to a device-sized reference PDF."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Render a markdown reference note to a reMarkable-sized PDF.")
    p.add_argument("note", help="Markdown note.")
    p.add_argument("--vault", default=".", help="Vault root (attachments/ lives here; default: cwd).")
    p.add_argument("--out", default=None, help="Output PDF (default: <note stem>.pdf).")
    p.add_argument("--source-sha", default=None, help="sha256 of the note bytes (default: computed).")
    p.add_argument("--date", default=None, help="Sheet date YYYY-MM-DD (default: today).")
    p.add_argument("--manifest", default=None, help="Also write the manifest JSON here.")
    p.add_argument("--html", default=None, help="Also dump the body HTML (debug).")
    args = p.parse_args(argv)

    from remarkable_gtd.gen.reference import render_reference

    note = Path(args.note)
    out = Path(args.out or f"{note.stem}.pdf")
    result = render_reference(
        note, Path(args.vault), out,
        source_sha=args.source_sha,
        the_date=datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None,
        manifest_path=Path(args.manifest) if args.manifest else None,
        debug_html=Path(args.html) if args.html else None,
    )
    print(f"wrote {out}  ({result['page_count']} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
