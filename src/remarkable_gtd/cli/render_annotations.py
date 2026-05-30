#!/usr/bin/env python3
"""gtd-render-annotations CLI entry point."""
from __future__ import annotations

import argparse
from pathlib import Path

from remarkable_gtd.rm.annotations import render_rmdoc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Render reMarkable annotations from an rmdoc file onto a flat PDF.")
    p.add_argument("rmdoc", help="Path to .rmdoc file.")
    p.add_argument("output_pdf", help="Path to output PDF.")
    args = p.parse_args(argv)

    try:
        render_rmdoc(Path(args.rmdoc), Path(args.output_pdf))
    except Exception as e:
        print(f"Error: {e}", file=__import__("sys").stderr)
        return 1

    print(f"✓ wrote {args.output_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
