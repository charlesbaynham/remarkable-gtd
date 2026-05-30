You are implementing a new Python project called remarkable-gtd from scratch. The git repo at /home/user/remarkable-gtd is empty (no commits yet). You must develop on branch claude/sweet-mccarthy-CvVAW, commit your work, and push it.

## Plan file
Read plan.md in full before doing anything else. Follow it exactly.

## Design source files (read these too, in order)
From handover.zip:
- /project/generator/generate.py — the working generator (port this into src/remarkable_gtd/gen/generate.py)
- /project/generator/template.html.j2 — Jinja2 template (port to src/remarkable_gtd/gen/assets/template.html.j2, adding data-roi attrs)
- /project/generator/gtd.css — shared stylesheet (copy verbatim to src/remarkable_gtd/gen/assets/gtd.css, do NOT change it)
- /project/generator/tasks.example.json — sample data (copy to tests/fixtures/tasks.example.json)
- /project/generator/requirements.txt — original deps (superseded by pyproject.toml)
- /project/uploads/example.pdf — copy to tests/fixtures/example.pdf (regression fixture)

## What to build (build sequence from plan)

**Step 1 — Scaffold packaging**
Create pyproject.toml (PEP 621, setuptools backend), README.md, src/remarkable_gtd/__init__.py with __version__ = "0.1.0". Two console scripts: gtd-gen = "remarkable_gtd.gen.cli:main" and gtd-scan = "remarkable_gtd.scan.cli:main". Extras: gen (playwright, pypdf, Jinja2, qrcode, Pillow), scan (opencv-python, pyzbar, pytesseract, PyMuPDF, numpy, Pillow), dev (pytest, pdf2image, plus gen+scan). Base deps: numpy, Pillow.

Port generate.py to src/remarkable_gtd/gen/generate.py with ONE key change: replace all Path(__file__).resolve().parent asset reads with importlib.resources.files("remarkable_gtd.gen.assets") so assets load correctly after pip install. Add __init__.py to every package directory and src/remarkable_gtd/gen/assets/__init__.py. The assets dir needs to be a package (for importlib.resources).

Port gen/cli.py as a thin argparse wrapper around render_pdf from generate.py. Keep the same CLI interface: gtd-gen tasks.json --out today.pdf [--date YYYY-MM-DD] [--html debug.html] [--manifest PATH] [--no-manifest].

Install the package in editable mode: pip install -e ".[gen,scan,dev]" and playwright install chromium.

**Step 2 — data-roi attributes + manifest export**

Edit src/remarkable_gtd/gen/assets/template.html.j2:
- Thread task id (t.id) into all macros that render interactive elements.
- Add data-roi="<id>:<verb>" to the inner .box div of each gutter cell:
  - inbox gutter: to_next, to_deleg, drop; defer trio: defer_1w, defer_1m, defer_1q
  - next gutter: done, to_deleg, edit; defer trio: defer_1w, defer_1m, defer_1q
  - delegated gutter: done, to_me, edit; defer trio: defer_1w, defer_1m, defer_1q
  - tickler gutter: activate, done, edit; defer trio: redefer_1w, redefer_1m, redefer_1q
- Add data-roi="<id>:slot_priority" etc to each .slot span.
- Add data-roi="<id>:qr" on the .rail .qr span, data-roi="<id>:act" on .act div.
- Add data-roi="capture:N<i>:box" on .cap-line .cbx, data-roi="capture:N<i>:line" on the .cap-line itself.
- Add data-roi="reg:tl", reg:tr, reg:bl, reg:br to the four .reg divs.
- Add data-roi="page:qr" on .head-qr.
- CRITICAL: do NOT change any CSS classes, layout, or visual structure. These are purely additive HTML attributes.

Create src/remarkable_gtd/gen/manifest.py with:

def collect_rois(page) -> dict:
    """Calls page.evaluate(JS) to gather normalized rects."""
    # JS walks [data-roi], computes each rect relative to .page, returns normalized fracs

def write_manifest(buckets_rois: list[dict], the_date, page_w_mm, out_path: Path) -> None:
    """Writes the manifest JSON (schema gtd.manifest/1)."""

Schema: {"schema": "gtd.manifest/1", "date": "...", "page_w_mm": 157.8, "pages": {"GTD|<bucket>|<date>": {"bucket": "...", "page_no": N, "render": {"w_px": N, "h_px": N}, "rois": {"<key>": {"x": f, "y": f, "w": f, "h": f}, ...}}}}

Update render_pdf in generate.py to call collect_rois after height measurement and assemble the manifest. Add manifest_path parameter (default out_path.with_suffix('.manifest.json')); --no-manifest sets it to None.

Create src/remarkable_gtd/common/geometry.py (Rect dataclass, normalize/denormalize helpers) and src/remarkable_gtd/common/schema.py (schema version constants).

**Step 3 — Scan pipeline**

Create these modules in src/remarkable_gtd/scan/:

manifest_io.py — load_manifest(path) -> dict, get_page(manifest, key) -> dict, list_page_keys(manifest) -> list[str].

rectify.py:

class RegistrationError(Exception): pass

def find_reg_marks(binary: np.ndarray) -> dict[str, tuple[float, float]]:
    """Finds the 4 cross-shaped reg marks in the four corner quadrants.
    Returns {"tl": (cx, cy), "tr": ..., "bl": ..., "br": ...} in pixel coords.
    Raises RegistrationError if fewer than 4 found."""
    # Strategy: split image into 4 quadrants (25%×25% corners).
    # In each quadrant, find contours; score each by "plus-ness":
    # - bbox roughly square (aspect ratio 0.6–1.4)
    # - dark pixel fill in horizontal center band > threshold
    # - dark pixel fill in vertical center band > threshold
    # - low fill in the 4 corner sub-regions
    # Return centroid of best-scoring candidate per quadrant.

def rectify(gray: np.ndarray, binary: np.ndarray, marks: dict, manifest_page: dict) -> tuple[np.ndarray, np.ndarray, tuple[int,int], float]:
    """Warp image so page corners align with manifest reg-mark positions.
    Returns (warped_gray, warped_binary, (canvas_w, canvas_h), residual_px)."""

qr.py:

class QRBackend(Protocol):
    def decode_all(self, img: np.ndarray) -> list[tuple[str, list]]: ...

class OpenCVBackend:  # uses cv2.QRCodeDetector
class PyzbarBackend:  # uses pyzbar.decode, skip gracefully if not installed

def get_backend(name="auto") -> QRBackend:
def decode_region(img: np.ndarray, roi: dict, canvas_size: tuple) -> str | None:
def decode_header(rectified: np.ndarray, manifest_page: dict, canvas_size: tuple) -> str:
def decode_task_qrs(rectified: np.ndarray, manifest_page: dict, canvas_size: tuple) -> dict[str, str]:

ink.py:

def roi_to_pixels(roi: dict, canvas_size: tuple) -> tuple[int,int,int,int]:
    """Convert normalized {x,y,w,h} to pixel (x1,y1,x2,y2)."""

def measure_fill(binary_crop: np.ndarray) -> float:
    """Fraction of dark pixels in the crop."""

def detect_box(rectified_binary: np.ndarray, roi: dict, canvas_size: tuple,
               inner_inset_frac: float = 0.22, threshold: float = 0.06) -> tuple[float, bool]:
    """Returns (fill_ratio, inked). Insets crop to exclude printed border."""

def select_one(results: dict[str, tuple[float, bool]]) -> str | None:
    """From a group of mutually-exclusive boxes, return key of max fill above threshold, or None."""

ocr.py:

class OcrEngine(Protocol):
    name: str
    def read(self, image_rgb: np.ndarray, hint: str | None = None) -> str: ...

class NullEngine:
    name = "null"
    def read(self, image_rgb, hint=None) -> str: return ""

class TesseractEngine:
    name = "tesseract"
    def read(self, image_rgb, hint=None) -> str:
        # pytesseract.image_to_string; psm 7 for slots/single-line, psm 6 for act
        ...

def get_engine(name: str = "null") -> OcrEngine:

decisions.py:

BUCKET_ACTIONS = {
    "inbox":     ["to_next", "to_deleg", "drop"],
    "next":      ["done", "to_deleg"],
    "delegated": ["done", "to_me"],
    "tickler":   ["activate", "done"],
}
DEFER_KEYS = ["defer_1w", "defer_1m", "defer_1q"]
REDEFER_KEYS = ["redefer_1w", "redefer_1m", "redefer_1q"]
PRECEDENCE = ["done", "activate", "to_next", "to_me", "to_deleg", "drop", "defer"]

def resolve_task(task_id: str, ticks: dict[str, tuple[float, bool]],
                 bucket: str, field_texts: dict, edited: bool) -> dict:
    """Build the per-task decisions entry."""

def build_decisions(bucket: str, task_results: list, captures: list,
                    rectify_meta: dict, header_qr: str, source_image: str,
                    manifest_path: str, the_date: str) -> dict:
    """Assemble the full decisions JSON."""

pipeline.py:

from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class ScanConfig:
    ink_fill_threshold: float = 0.06
    inner_inset_frac: float = 0.22
    ocr_engine: str = "null"
    canvas_width: int = 1404

def run_scan(image_path: Path, manifest: dict, cfg: ScanConfig,
             page_key: str | None = None) -> dict:
    """Full pipeline: load → rectify → QR → ink → OCR → decisions."""

scan/cli.py:

gtd-scan IMAGE --manifest M.json [--page KEY] [--ocr null|tesseract] [-o decisions.json]

**Step 4 — Tests**

tests/fixtures/tasks.min.json — tiny input: 1 inbox item, 1 next action (id NA-01), 1 delegated (DG-01), 1 capture line, minimal tickler.

tests/conftest.py — fixtures:
- tasks_min_path → path to tasks.min.json
- rendered_sheet(tmp_path) → call render_pdf → returns (pdf_path, manifest_path); skip if playwright/chromium not available
- rasterize_page(pdf_path, page_index, dpi=226) → PyMuPDF fitz → RGB ndarray
- paint_ink(img_rgb, manifest_page, roi_key, style) → Pillow draw cross/text into ROI rect; return modified image. style: "tick" (diagonal cross), "text:<str>" (write text).

tests/test_manifest.py — render sheet, load manifest, assert:
- All expected ROI keys present for each task
- All rects have x,y,w,h in [0,1]
- reg:tl is in top-left quadrant, reg:tr top-right, etc.
- No two tick-box ROIs overlap for same task
- Visual stability: rasterize output PDF and compare to rasterizing example.pdf fixture (just assert QR codes decode, not pixel-identical since dates differ)

tests/test_ink.py — unit tests without rendering, create synthetic binary crops:
- White 36×36 crop with a 2px black border → measure_fill ≈ 0, detect_box inked=False
- Same crop with a diagonal cross drawn → detect_box inked=True
- select_one: only one of three boxes inked → returns that key; none inked → None

tests/test_rectify.py — create a synthetic page image with known reg marks, apply cv2.warpPerspective with a known transform, then assert find_reg_marks finds them and rectify recovers ≤2px residual. Negative: occlude one corner → RegistrationError.

tests/test_qr.py — rasterize tests/fixtures/example.pdf (no Chromium needed), find any QR codes; if none found (since example.pdf is tiny/demo), skip gracefully. Test decode_region on a synthesized QR image.

tests/test_decisions.py — unit test resolve_task and build_decisions directly with synthetic tick results; test precedence (done > defer), edited flag, conflict warnings.

tests/test_end_to_end.py — integration test (skipped if Playwright not available):
1. Render tasks.min.json → pdf + manifest
2. Rasterize next-actions page
3. paint_ink NA-01 with done tick
4. run_scan with NullEngine
5. Assert decisions["tasks"][0]["action"] == "done"
6. Render with no ticks → all action: "none"

**Step 5 — CI**

.github/workflows/ci.yml — two jobs:
- scan-unit: ubuntu-latest, install pip install -e ".[scan,dev]" && apt-get install libzbar0 tesseract-ocr poppler-utils, run pytest -k "not (end_to_end or manifest)" (skip render-dependent tests)
- full: additionally install pip install -e ".[gen]" && playwright install --with-deps chromium, run full pytest

## Critical implementation notes

1. importlib.resources.files("remarkable_gtd.gen.assets") for reading CSS/template in the installed package. The assets dir must have __init__.py to be a proper package.
2. When threading task ids into the Jinja2 template macros, be careful: the defer macro and the three-box render must each receive the task id AND a stem (defer vs redefer). The fields macro must receive t.id.
3. The collect_rois JS must normalize all rects relative to the .page element (not the viewport). The CSS places .page at width: 157.8mm, so getBoundingClientRect() on .page gives the reference frame.
4. In rectify.py, the destination points for the homography come from the manifest reg:tl/tr/bl/br rects — use their CENTER: (roi.x + roi.w/2) * canvas_w. Canvas width = cfg.canvas_width = 1404; canvas height = 1404 * (render.h_px / render.w_px).
5. In ink.py detect_box, the inset must be at least 1 pixel on each side even for very small boxes (use max(1, int(...))).
6. The precedence in decisions.py means: if both done and defer_1m are ticked, done wins and a warning is added.
7. For scan CLI, if --page is not specified and the manifest has only one page, use it automatically.

## Git instructions
- Develop on branch claude/sweet-mccarthy-CvVAW
- Make multiple commits as you go (one per logical step)
- Push with git push -u origin claude/sweet-mccarthy-CvVAW
- Do NOT create a pull request

Make sure pip install -e ".[gen,scan,dev]" succeeds before committing. Run the test suite (pytest -x) and fix any failures before pushing. If playwright/chromium is not available in this environment, ensure tests that need it are properly skipped (not erroring).
