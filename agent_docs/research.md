# Research: reMarkable v6 Annotation Coordinate System

## Context
The `render_annotations()` function in `src/remarkable_gtd/rm/annotations.py` maps `.rm` file stroke coordinates to PDF page coordinates. The mapping has been wrong, causing annotations to appear in incorrect locations.

## reMarkable Device Specs
- Screen: 1404 × 1872 pixels
- DPI: 226
- Physical size: ~157.8 × 209.6 mm

## Coordinate System (from rmscene + rmc analysis)

### rmscene Library
- Reads raw `float32` x, y from `.rm` files — no internal transformation.
- Point dataclass: `Point(x: float, y: float, speed, direction, width, pressure)`

### rmc (ricklupton's reference SVG exporter)
```python
SCREEN_WIDTH = 1404
SCREEN_HEIGHT = 1872
SCREEN_DPI = 226
SCALE = 72.0 / SCREEN_DPI  # ≈ 0.3186

# For SVG rendering (handles negative coordinates via viewBox):
def scale(v): return v * SCALE
xx = scale  # x transform
yy = scale  # y transform

# Default bounding box from get_bounding_box:
# x: [-SCREEN_WIDTH//2, SCREEN_WIDTH//2] = [-702, 702]
# y: [0, SCREEN_HEIGHT] = [0, 1872]
```

**Key finding**: `xx` and `yy` are IDENTICAL — uniform scale. No offset applied in SVG because viewBox handles negative x.

### PyMuPDF / PDF Rendering
PyMuPDF does NOT support negative coordinates (clips to page rect). For PDF overlay:
- x must be shifted positive: `x = (p.x + 702) * SCALE`
- y is already ≥0 in raw data, so: `y = p.y * SCALE`

But this creates a canvas of size:
- width = 1404 * SCALE = 447.3 pts
- height = 1872 * SCALE = 596.3 pts

**Our PDF pages have different heights**: inbox=720, next=814, delegated=599, tickler=599 pts.

So simply using `SCALE = 72/226` does NOT match the PDF page dimensions.

## Empirical Data

### rmdoc Structure
Downloaded `today.rmdoc` contains:
- `d231abb0-.../3498d6ba-....rm` (page 2, delegated) — 1 line
- `d231abb0-.../bd347aab-....rm` (page 1, next) — 12 lines
- `d231abb0-.../f23a34e2-....rm` (page 0, inbox) — 40 lines

### Raw Coordinates (page 1, next actions)
```
Line 0:  x=228.9..312.9,  y=384.8..441.4   (15 points)
Line 1:  x=222.6..333.6,  y=667.1..769.1   (19 points)
Line 2:  x=289.6..379.8,  y=1036.4..1098.6 (18 points)
Line 3:  x=-435.9..-372.1, y=1276.3..1284.6 (5 points)
Lines 4-10: x=-403..-264, y=1276..1327     (short strokes, 5-10 pts each)
Line 11: x=569.3..609.3,  y=1386.0..1438.1 (15 points)
```

### Target Positions (NA-01 done box)
Manifest ROI `NA-01:done` center in PDF points: **(301.2, 137.9)**

### Transform Tests

#### Test A: Current code (0-based, pdf dims)
```python
scale_x = pdf_w / 1404  # 0.319
scale_y = pdf_h / 1872  # 0.435
x = p.x * scale_x
y = p.y * scale_y
```
Result: Line 0 → x=73..100, y=167..192 — far left of page, nowhere near done box.

#### Test B: Centered x, 0-based y, pdf dims
```python
x = (p.x + 702) * scale_x
y = p.y * scale_y
```
Result: Line 0 → x=296..324, y=167..192 — x matches done boxes, y between NA-01 and NA-01:slot_n.

#### Test C: Centered both axes, pdf dims (original buggy code)
```python
x = (p.x + 702) * scale_x
y = (p.y + 936) * scale_y
```
Result: Line 0 → x=296..324, y=574..599 — x matches, y near NA-05.

#### Test D: Centered both axes, uniform SCALE=72/226
```python
x = (p.x + 702) * SCALE
y = (p.y + 936) * SCALE
```
Result: Line 0 → x=296..324, y=421..439 — x matches, y near NA-04.
Line 1 → y=511..543 — near NA-05.
Line 2 → y=628..648 — near NA-06.

#### Test E: Centered x, 0-based y, uniform SCALE=72/226
```python
x = (p.x + 702) * SCALE
y = p.y * SCALE
```
Result: Line 0 → x=296..324, y=122..140 — x matches, y VERY CLOSE to NA-01 (137.9)!
Line 1 → y=212..245 — near NA-02 (241)!
Line 2 → y=330..350 — near NA-03 (345)!

**This is the best match!** Lines 0-2 map to NA-01, NA-02, NA-03 done boxes.

But wait — Lines 3-10 at y=555..577 would map to NA-05 area, and they're on the left side (x=85..140). Line 11 at y=441..458 maps to NA-04 area.

So with this transform:
- Lines 0-2 → NA-01, NA-02, NA-03 done boxes ✓
- Lines 3-10 → Left side scribbles at NA-05 height
- Line 11 → NA-04 area (right side)

This matches the visual observation of ~6 marks in done boxes!

### BUT: Scale mismatch with PDF page dimensions
Using `SCALE = 72/226` for y gives `y_max = 1872 * 72/226 = 596.3` pts.
But the next-actions PDF page is **814 pts tall**.

If we use `SCALE` for y, annotations at y=1872 would be at PDF y=596, not y=814.
This means the bottom of the page (y>596) would have no annotations.

However, the user's annotations are all in the upper portion of the page (raw y < 1438), so they'd all be within the 0..596 range. The page content above y=596 in the PDF might just be empty space.

Wait — but the PDF page is 814 pts tall, and the rendered content (tasks, checkboxes) fills most of that. If we scale y by 72/226, the annotations would be compressed into the top 596 pts, leaving the bottom 218 pts empty. Is that correct?

Looking at the rendered image, the annotations DO appear in the upper portion of the page. The bottom portion has capture lines. The user's marks are in the task rows, which are in the upper ~70% of the page.

Actually, looking at the manifest: the next-actions page render height is 1082 px at the generation DPI. But the PDF is 814 pts. At 226 DPI, 814 pts = 2555 px. The content is only in the upper portion.

Hmm, this is confusing. Let me think about what the reMarkable actually displays.

### What the reMarkable Displays
The reMarkable screen is 1404×1872 px. Our PDF at 226 DPI would be:
- Width: 448 pts × (226/72) = 1406 px ≈ screen width
- Height: 814 pts × (226/72) = 2555 px > screen height

So the PDF is TALLER than the screen. The reMarkable shows the top portion and the user scrolls to see the rest.

When the user writes, the reMarkable stores the screen coordinates (0..1404, 0..1872). These coordinates map to the VISIBLE portion of the PDF.

To convert back to PDF coordinates:
- The visible width is the full PDF width (1406 px ≈ 1404 px)
- The visible height is only the top portion of the PDF (1872 px out of 2555 px)

So:
- pdf_x = (screen_x + 702) × (pdf_width / 1404)  [x is centered]
- pdf_y = screen_y × (pdf_height_px / 1872)       [y is 0-based, but using PDF pixel height]

Wait, pdf_height_px = 814 × 226/72 = 2555 px.
So pdf_y = screen_y × (2555 / 1872) = screen_y × 1.365

But we're working in PDF points, not pixels. To convert to points:
pdf_y_pts = screen_y × (2555/1872) × (72/226) = screen_y × 1.365 × 0.319 = screen_y × 0.435

That's exactly the current scale_y = pdf_height / 1872 = 814/1872 = 0.435!

So the current y transform IS correct if y is 0-based!

But then why doesn't it match? Let me recalculate with x-centered, y-0based, pdf dims:

Line 0: x = (228.9+702)×0.319 = 296.1..323.8, y = 384.8×0.435 = 167.3..191.9

NA-01 done: (301.2, 137.9)
Line 0 y=167-192 is 30-55 pts BELOW NA-01.

Hmm, that's a consistent offset. What if there's a y offset due to the reMarkable UI (toolbar, header, etc.)?

The reMarkable has a top toolbar that takes up some screen space. The PDF content might start below the toolbar. If the toolbar is ~100 px tall, then:

pdf_y = (screen_y - toolbar_offset) × scale_y

If toolbar_offset ≈ 100 px:
(384.8 - 100) × 0.435 = 123.8 → close to 138!
(667.1 - 100) × 0.435 = 246.7 → close to 241!
(1036.4 - 100) × 0.435 = 408.8 → close to 345... not great.

Hmm, a fixed offset doesn't work for all lines.

What if the offset is proportional? Like the reMarkable crops a percentage from the top?

Actually, looking at the content file:
```json
{
  "customZoomCenterX": 0,
  "customZoomCenterY": 936,
  "customZoomOrientation": "portrait",
  "customZoomPageHeight": 1872,
  "customZoomPageWidth": 1404,
  "customZoomScale": 1,
  ...
}
```

customZoomCenterY: 936. This is the vertical center of the screen. If the zoom center is at the middle of the screen, and the zoom scale is 1, then the PDF is displayed centered at the middle of the page.

But what does "zoom center" mean? It might mean that when you zoom in, the zoom is centered at that point. With zoom scale 1 (no zoom), it might not matter.

Actually, I think the simplest explanation is that the reMarkable stores annotations in a coordinate system where:
- x is centered at 0: [-702, +702]
- y is 0-based: [0, 1872]

And the PDF is displayed at a 1:1 pixel mapping (1 PDF pixel = 1 screen pixel at 226 DPI), with the PDF centered horizontally.

In that case:
- pdf_x_pts = (screen_x + 702) × (72/226)
- pdf_y_pts = screen_y × (72/226)

This is Test E from above, and it matches NA-01, NA-02, NA-03 almost perfectly!

The issue is that this uses a fixed scale of 72/226, which gives a PDF height of 596.3 pts, not 814 pts. But maybe the annotations are only in the top portion of the page, and that's fine.

Wait, but if we render annotations with scale_y = 72/226 = 0.319 onto a PDF page that's 814 pts tall, the annotations would be compressed vertically. A tick that spans 50 screen pixels would only span 16 PDF points. That might make the ticks very small.

Let me check the actual rendered image. Looking at the annotated page, the ticks look like they're normal size, not compressed.

Hmm, but the user's marks are drawn with the reMarkable pen, which has a certain physical width. When mapped to PDF points with scale 0.319, the pen width would be very thin.

Actually, looking at the current code:
```python
avg_width = sum(p.width for p in points) / len(points) * scale_x * 0.3
```

With scale_x = 0.319, the pen width would be quite small. But in the rendered image, the marks look reasonably thick. So maybe the scale is larger.

OK, I'm going in circles again. Let me just state the key findings:

## Key Findings

1. **x coordinate is centered around 0** (range ~[-702, +702]). Must add 702 before scaling.
2. **y coordinate is 0-based** (range ~[0, 1872]). No offset needed.
3. **Scale factor is likely uniform** — the same for x and y. The rmc tool uses `72/226 ≈ 0.3186`.
4. **The PDF page dimensions (448×814 pts) do NOT directly determine the scale** — the reMarkable uses its own screen pixel space.
5. **Best empirical match**: `x = (p.x + 702) × 0.319`, `y = p.y × 0.319` maps lines 0-2 to NA-01/02/03 done boxes.

## Recommendation

Try the transform:
```python
SCALE = 72.0 / 226  # ≈ 0.3186
x = (p.x + rm_width / 2) * SCALE
y = p.y * SCALE
```

This ignores the PDF page dimensions for scaling and uses the reMarkable's native DPI conversion. The annotations will be rendered on a 447×596 pt canvas within the PDF page.

If this leaves too much empty space at the bottom of taller pages, consider adjusting the transform or accepting that annotations only occupy the top portion.

## References
- rmc SVG exporter: `/tmp/rmc_pkg/rmc-0.3.0-py3-none-any.whl` → `rmc/exporters/svg.py`
- rmscene source: `.venv/lib/python3.10/site-packages/rmscene/`
- reMarkable .lines format: https://plasma.ninja/blog/devices/remarkable/binary/format/2017/12/26/reMarkable-lines-file-format.html
