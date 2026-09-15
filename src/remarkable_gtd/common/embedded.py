"""Sheet state carried inside the PDF itself.

The generator attaches the layout manifest and the tasks document to the
PDF as standard embedded files. The reMarkable stores the uploaded PDF
byte-for-byte (annotations live in separate ``.rm`` files), so whatever
sheet comes back from the device brings its own manifest and task ids with
it — no sidecar files have to be kept in sync between runs.
"""
from __future__ import annotations

import io
import json

ATTACH_MANIFEST = "gtd.manifest.json"
ATTACH_TASKS = "gtd.tasks.json"
TASKS_SCHEMA = "gtd.tasks/1"


def attach_state(writer, manifest: dict | None, tasks: dict | None) -> None:
    """Attach the manifest and tasks documents to a ``pypdf.PdfWriter``."""
    if manifest is not None:
        writer.add_attachment(ATTACH_MANIFEST, json.dumps(manifest).encode("utf-8"))
    if tasks is not None:
        writer.add_attachment(ATTACH_TASKS, json.dumps(tasks).encode("utf-8"))


def read_state(pdf_bytes: bytes) -> tuple[dict | None, dict | None]:
    """Return ``(manifest, tasks)`` embedded in a PDF, each ``None`` if absent."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    try:
        attachments = reader.attachments
    except Exception:  # a PDF with no EmbeddedFiles tree
        return None, None

    def _load(name: str) -> dict | None:
        blobs = attachments.get(name)
        if not blobs:
            return None
        return json.loads(blobs[0].decode("utf-8"))

    return _load(ATTACH_MANIFEST), _load(ATTACH_TASKS)


def tasks_document(buckets: list[dict], the_date: str) -> dict:
    """Flatten the generator's bucket list into ``{id: item}`` keyed by task id.

    Every item keeps the fields it was given (``act``, ``pri``, ``due``,
    ``proj``, ``to`` and any caller extras such as a vault ``handle``) plus
    ``bucket`` (``inbox``/``next``/``delegated``/``tickler``) and, for
    tickler items, ``period`` (``week``/``month``/``quarter``).
    """
    out: dict[str, dict] = {}
    for b in buckets:
        if b["kind"] == "sectioned":
            groups = [(sec["title"], sec["items"]) for sec in b["sections"]]
            period_of = {"Next week": "week", "Next month": "month", "Next quarter": "quarter"}
            for title, items in groups:
                for t in items:
                    entry = dict(t)
                    entry["bucket"] = b["key"]
                    entry["period"] = period_of.get(title, title)
                    out[t["id"]] = entry
        else:
            for t in b["items"]:
                entry = dict(t)
                entry["bucket"] = b["key"]
                out[t["id"]] = entry
    return {"schema": TASKS_SCHEMA, "date": the_date, "tasks": out}
