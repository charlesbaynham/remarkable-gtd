"""gtd-overlay — draw the manifest's boxes onto a sheet for eyeballing alignment."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Write PNGs of a sheet with every manifest region outlined, as the "
            "scanner sees it (rectified) or on the plain raster (--raw). Input "
            "is a generated PDF, an annotated PDF, an .rmdoc from the device, "
            "or a single page image. The manifest is read from the PDF's "
            "embedded files unless --manifest is given."
        )
    )
    p.add_argument("input", help="PDF, .rmdoc, or page image (PNG/JPG).")
    p.add_argument("--manifest", default=None, help="Manifest JSON (default: embedded in the PDF).")
    p.add_argument("--page", default=None, help="Manifest page key for a single image input.")
    p.add_argument("-o", "--out-dir", default="overlay", help="Output directory (default: ./overlay).")
    p.add_argument("--raw", action="store_true", help="Skip registration/rectification.")
    p.add_argument("--labels", action="store_true", help="Label tick boxes and slots with their ROI key.")
    args = p.parse_args(argv)

    import cv2

    from remarkable_gtd.scan.manifest_io import list_page_keys, load_manifest
    from remarkable_gtd.scan.overlay import overlay_page, overlay_pdf

    src = Path(args.input)
    out_dir = Path(args.out_dir)
    manifest = load_manifest(args.manifest) if args.manifest else None

    if src.suffix.lower() in (".rmdoc", ".zip"):
        from remarkable_gtd.common.embedded import read_state
        from remarkable_gtd.rm.annotations import extract_from_rmdoc, render_rmdoc

        out_dir.mkdir(parents=True, exist_ok=True)
        if manifest is None:
            pdf_bytes, _ = extract_from_rmdoc(src)
            manifest, _ = read_state(pdf_bytes or b"")
        pdf = out_dir / f"{src.stem}.annotated.pdf"
        render_rmdoc(src, pdf)
        src = pdf
    if src.suffix.lower() == ".pdf":
        if manifest is None:
            from remarkable_gtd.common.embedded import read_state

            manifest, _ = read_state(src.read_bytes())
        if manifest is None:
            print("Error: no --manifest given and none embedded in the PDF", file=sys.stderr)
            return 1
        outs = overlay_pdf(src, manifest, out_dir, raw=args.raw, labels=args.labels)
        print(f"wrote {len(outs)} overlay(s) to {out_dir}")
        return 0

    # Single image.
    if manifest is None:
        print("Error: a page image needs --manifest", file=sys.stderr)
        return 1
    keys = list_page_keys(manifest)
    key = args.page or (keys[0] if len(keys) == 1 else None)
    if key is None:
        from remarkable_gtd.scan.pipeline import detect_page_key, load_image

        key = detect_page_key(load_image(src)[0], manifest)
    if key is None:
        print(f"Error: could not tell which page this is; pass --page (one of {keys})", file=sys.stderr)
        return 1
    img, info = overlay_page(src, manifest["pages"][key], raw=args.raw, labels=args.labels)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}.overlay.png"
    cv2.imwrite(str(out), img)
    print(f"wrote {out}  {key}  {json.dumps(info)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
