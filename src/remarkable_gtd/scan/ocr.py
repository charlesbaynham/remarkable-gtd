"""Pluggable handwriting-recognition engines.

The vision pipeline only calls an engine where ink is present in a write-in
region (metadata slots, capture lines, or the action text of a row whose
Edit box is ticked), so a hosted vision model costs a handful of tiny image
requests per sheet. Engines:

- ``openrouter`` — a vision LLM through OpenRouter (default: Google Gemini
  Flash). Needs ``OPENROUTER_API_KEY``; ``OPENROUTER_MODEL`` overrides the
  model id.
- ``tesseract`` — offline OCR via pytesseract (poor on handwriting; kept as a
  no-network fallback).
- ``null`` — transcribes nothing; keeps the ink-trigger logic testable.

Every engine takes an optional ``hint`` naming the kind of region
(``priority``, ``due``, ``project``, ``to``, ``capture``, ``act``, ``line``,
``block``) and an optional ``context`` string (the printed text of an
edited action) so a model can be told what it is looking at.

When the whole row is ticked ✎ EDIT, ``read`` is not enough — the pipeline
instead crops the entire row and calls ``interpret``, which asks the model
for a structured ``gtd.edit/1`` reading (:data:`EDIT_SCHEMA`) via
OpenRouter's ``json_schema`` response format: what was handwritten, whether
it was understood, and where the item should end up.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

import numpy as np

DEFAULT_OPENROUTER_MODEL = "google/gemini-3.5-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# What the model is told about each region kind.
_HINT_PROMPTS = {
    "priority": "It is a small box labelled PRIORITY and should contain a single integer.",
    "due": "It is a small box labelled DUE and should contain a date (for example '6 Jun', '2026-06-06' or 'Fri').",
    "project": "It is a box labelled PROJECT and should contain a short project name.",
    "to": "It is a box labelled TO and should contain a person's name.",
    "capture": "It is a ruled capture line on a to-do sheet and contains a new task written in freehand.",
    "line": "It is a single handwritten line.",
    "block": "It may contain several lines of handwriting.",
}

_ACT_PROMPT = (
    "This crop shows a printed to-do item that the user has amended by hand "
    "(strike-throughs, insertions, or a rewritten line). The printed text "
    "reads:\n\n{context}\n\nReply with the full amended item text as the "
    "user intends it to read now — printed words that are struck through "
    "are removed, handwritten words are included. Reply with the text only."
)

_BASE_PROMPT = (
    "Transcribe the handwriting in this image exactly, as plain text on one "
    "line. {hint} Do not describe the image, do not add quotes or "
    "punctuation that is not written, and ignore any printed labels or box "
    "borders. If there is no legible handwriting reply with exactly: <empty>"
)

ROUTES = (
    "keep", "done", "drop", "next", "delegated",
    "tickler_1w", "tickler_1m", "tickler_1q", "inbox", "scheduled",
)

EDIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "handwriting", "understood", "confidence", "route",
        "text", "priority", "due", "project", "person", "note",
    ],
    "properties": {
        "handwriting": {
            "type": "string",
            "description": "Everything handwritten in the crop, transcribed verbatim.",
        },
        "understood": {"type": "boolean"},
        "confidence": {"type": "number"},
        "route": {"type": "string", "enum": list(ROUTES)},
        "text": {"type": ["string", "null"]},
        "priority": {"type": ["integer", "null"]},
        "due": {"type": ["string", "null"]},
        "project": {"type": ["string", "null"]},
        "person": {"type": ["string", "null"]},
        "note": {"type": "string"},
    },
}

_BUCKET_LABELS = {"inbox": "Inbox", "next": "Next Actions"}


def _bucket_description(task: dict) -> str:
    bucket = task.get("bucket")
    if bucket == "delegated":
        return f"Delegated, waiting on {task.get('to') or 'someone'}"
    if bucket == "tickler":
        return f"Tickler ({task.get('period') or 'unknown period'})"
    return _BUCKET_LABELS.get(bucket, bucket or "unknown")


def build_edit_prompt(
    task: dict, vocabulary: dict | None = None, today: str | None = None
) -> str:
    """The instruction sent alongside a whole-row crop when ✎ EDIT is ticked."""
    projects = (vocabulary or {}).get("projects") or []
    people = (vocabulary or {}).get("people") or []
    return (
        "You are reading one row of a printed GTD to-do sheet that the user "
        "has annotated by hand. The crop shows the whole row: the printed "
        "item text, a gutter of tick boxes (the ✎ EDIT box is ticked, "
        "which is why you are being asked) and labelled boxes PRIORITY, DUE, "
        "PROJECT, TO.\n\n"
        f"The printed row as it stands: list {_bucket_description(task)}; "
        f"text \"{task.get('act', '')}\"; priority {task.get('pri') or 'none'}; "
        f"due {task.get('due') or 'none'}; project {task.get('proj') or 'none'}.\n\n"
        f"Today is {today or 'unknown'}. Existing projects: "
        f"{', '.join(projects) if projects else 'none'}. People things are "
        f"delegated to: {', '.join(people) if people else 'none'}.\n\n"
        "Work out what the user wants done to this item from the "
        "handwriting (strike-throughs, arrows, words in or near the boxes) "
        "and answer as JSON: handwriting = verbatim transcription; "
        "understood = false if illegible or the intent is unclear, then "
        "leave every change null and explain in note, never guess; route = "
        "where the item should live afterwards: keep (stays where it is, "
        "changes applied in place; the default), done, drop, next (to Next "
        "Actions), delegated (waiting on someone; fill person), "
        "tickler_1w/1m/1q (defer a week/month/quarter), inbox, scheduled "
        "(dated appointment; fill due); text = the new wording if changed "
        "(struck-through printed words removed, handwritten words replace "
        "or extend them), null if unchanged; priority = new integer or "
        "null; due = new date as YYYY-MM-DD resolving relative dates from "
        "today, or null; project = exactly one of the existing projects "
        "when it clearly matches one, else the name as written, or null; "
        "person = who it is delegated to, or null; note = one sentence."
    )


@runtime_checkable
class OcrEngine(Protocol):
    """Protocol for handwriting-recognition backends."""

    name: str

    def read(
        self,
        image: np.ndarray,
        hint: str | None = None,
        context: str | None = None,
    ) -> str:
        """Transcribe handwriting in an image crop.

        Args:
            image: HxW grayscale or HxWx3 RGB uint8 crop.
            hint: Region kind (see module docstring) — tunes the prompt.
            context: Printed text of the region when ``hint == "act"``.

        Returns:
            The transcribed text (stripped); empty string if nothing read.
        """
        ...


class NullEngine:
    """No-op engine: flags regions for OCR without transcribing them."""

    name = "null"

    def read(self, image, hint=None, context=None) -> str:
        return ""

    def interpret(self, image, task, vocabulary=None, today=None) -> dict | None:
        return None


class TesseractEngine:
    """Offline OCR via pytesseract (requires the tesseract binary)."""

    name = "tesseract"

    def read(self, image, hint=None, context=None) -> str:
        import cv2
        import pytesseract

        img = image
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Upscale small crops — tesseract wants ~30px+ glyph height.
        h, w = img.shape[:2]
        if h < 60:
            scale = max(2, int(np.ceil(60 / max(1, h))))
            img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

        psm = 6 if hint in ("block", "act") else 7  # 7 = single line, 6 = block
        text = pytesseract.image_to_string(img, config=f"--psm {psm}")
        return text.strip()


def _to_png_b64(image: np.ndarray, min_height: int = 96, pad: int = 12) -> str:
    """Encode a crop as base64 PNG, padded white and upscaled if tiny.

    Vision models read small crops better with a margin and at a sane size,
    and the cost is per image token so this is still a very small request.
    """
    from PIL import Image

    if image.ndim == 3:
        pil = Image.fromarray(image).convert("L")
    else:
        pil = Image.fromarray(image)
    h, w = pil.size[1], pil.size[0]
    if h < min_height:
        scale = min_height / max(1, h)
        pil = pil.resize((max(1, int(w * scale)), int(h * scale)), Image.BICUBIC)
    padded = Image.new("L", (pil.size[0] + 2 * pad, pil.size[1] + 2 * pad), 255)
    padded.paste(pil, (pad, pad))
    buf = io.BytesIO()
    padded.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_prompt(hint: str | None, context: str | None) -> str:
    """The instruction sent alongside an image crop."""
    if hint == "act" and context:
        return _ACT_PROMPT.format(context=context.strip())
    prompt = _BASE_PROMPT.format(hint=_HINT_PROMPTS.get(hint or "line", _HINT_PROMPTS["line"]))
    if hint == "project" and context:
        prompt += (
            f" Existing projects: {context}. If the handwriting names one "
            "of them, reply with that name exactly as listed."
        )
    return prompt


def clean_reply(text: str) -> str:
    """Normalise a model reply to the transcribed text (or '')."""
    text = (text or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"<?\s*empty\s*>?\.?", text, flags=re.IGNORECASE):
        return ""
    # Strip a wrapping code fence or quotes the model may add despite instructions.
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    return re.sub(r"[ \t]+", " ", text).strip()


class OpenRouterEngine:
    """Handwriting recognition through a vision LLM on OpenRouter.

    Configuration (constructor arguments override the environment):

    - ``OPENROUTER_API_KEY`` — required.
    - ``OPENROUTER_MODEL`` — model id, default :data:`DEFAULT_OPENROUTER_MODEL`.

    Each ``read`` is one chat-completion request carrying one small PNG, so a
    typical sheet costs a few hundred image tokens in total.
    """

    name = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        retries: int = 2,
        url: str = OPENROUTER_URL,
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter OCR needs OPENROUTER_API_KEY in the environment"
            )
        self.model = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
        self.timeout = timeout
        self.retries = retries
        self.url = url
        self.requests_made = 0

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/charlesbaynham/remarkable-gtd",
                "X-Title": "remarkable-gtd",
            },
        )
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_err = exc
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
        raise RuntimeError(f"OpenRouter request failed: {last_err}")

    def read(self, image, hint=None, context=None) -> str:
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_prompt(hint, context)},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + _to_png_b64(image)},
                        },
                    ],
                }
            ],
        }
        data = self._post(payload)
        self.requests_made += 1
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"OpenRouter reply had no content: {json.dumps(data)[:300]}") from exc
        if isinstance(content, list):  # some providers return content parts
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return clean_reply(content)

    def interpret(
        self,
        image,
        task: dict,
        vocabulary: dict | None = None,
        today: str | None = None,
    ) -> dict:
        """Structured reading of a whole ✎-EDIT row: see :data:`EDIT_SCHEMA`."""
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 400,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_edit_prompt(task, vocabulary, today)},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + _to_png_b64(image)},
                        },
                    ],
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "gtd_edit", "strict": True, "schema": EDIT_SCHEMA},
            },
        }
        data = self._post(payload)
        self.requests_made += 1
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"OpenRouter reply had no content: {json.dumps(data)[:300]}") from exc
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", (content or "").strip()).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("understood"), bool):
            raise RuntimeError(f"OpenRouter edit reply was not valid JSON: {text[:200]}")
        return parsed


_ENGINES: dict[str, type] = {
    "null": NullEngine,
    "tesseract": TesseractEngine,
    "openrouter": OpenRouterEngine,
}

ENGINE_NAMES = sorted(_ENGINES)


def get_engine(name: str | OcrEngine = "null") -> OcrEngine:
    """Instantiate an engine by name, or pass an engine instance through."""
    if not isinstance(name, str):
        return name
    try:
        return _ENGINES[name]()
    except KeyError:
        raise ValueError(
            f"Unknown OCR engine {name!r}; available: {ENGINE_NAMES}"
        ) from None
