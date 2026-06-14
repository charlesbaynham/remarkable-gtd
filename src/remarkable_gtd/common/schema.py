"""Schema version constants for manifest and decisions JSON formats."""

MANIFEST_SCHEMA = "gtd.manifest/1"
DECISIONS_SCHEMA = "gtd.decisions/1"

# Page width constant (reMarkable 2 panel)
PAGE_W_MM = 157.8

# Manifest top-level keys
MANIFEST_KEY_SCHEMA = "schema"
MANIFEST_KEY_DATE = "date"
MANIFEST_KEY_PAGE_W_MM = "page_w_mm"
MANIFEST_KEY_PAGES = "pages"

# Per-page manifest keys
PAGE_KEY_BUCKET = "bucket"
PAGE_KEY_PAGE_NO = "page_no"
PAGE_KEY_RENDER = "render"
PAGE_KEY_ROIS = "rois"

# Render sub-keys
RENDER_KEY_W_PX = "w_px"
RENDER_KEY_H_PX = "h_px"

# Decisions top-level keys
DECISIONS_KEY_SCHEMA = "schema"
DECISIONS_KEY_SOURCE_IMAGE = "source_image"
DECISIONS_KEY_MANIFEST = "manifest"
DECISIONS_KEY_BUCKET = "bucket"
DECISIONS_KEY_DATE = "date"
DECISIONS_KEY_HEADER_QR = "header_qr"
DECISIONS_KEY_RECTIFY = "rectify"
DECISIONS_KEY_TASKS = "tasks"
DECISIONS_KEY_CAPTURES = "captures"
DECISIONS_KEY_WARNINGS = "warnings"

# QR header format: GTD|<bucket>|<date>
QR_HEADER_PREFIX = "GTD"
QR_HEADER_SEP = "|"


def make_page_key(bucket: str, date_stamp: str) -> str:
    """Create the manifest page key string."""
    return f"GTD{QR_HEADER_SEP}{bucket}{QR_HEADER_SEP}{date_stamp}"


def parse_page_key(key: str) -> dict:
    """Parse a page key string into its components."""
    parts = key.split(QR_HEADER_SEP)
    if len(parts) != 3 or parts[0] != QR_HEADER_PREFIX:
        raise ValueError(f"Invalid page key: {key!r}")
    return {"prefix": parts[0], "bucket": parts[1], "date": parts[2]}
