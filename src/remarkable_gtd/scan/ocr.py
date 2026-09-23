"""Pluggable handwriting-recognition engines.

The vision pipeline only calls an engine where ink is present in a write-in
region (metadata slots, capture lines, or the action text of a row whose
✦ AI box is ticked), so a hosted vision model costs a handful of tiny image
requests per sheet. Engines:

- ``openrouter`` — a vision LLM through OpenRouter (default: Google Gemini
  Flash). Needs ``OPENROUTER_API_KEY``; ``OPENROUTER_MODEL`` overrides the
  model id. The ✦ AI call reasons by default (``OPENROUTER_REASONING``);
  slot transcription does not (``OPENROUTER_READ_REASONING``). Set
  ``OPENROUTER_TRACE_DIR`` to dump every call — prompt, crop, raw reply and
  the model's thinking — for when a reading needs explaining.
- ``tesseract`` — offline OCR via pytesseract (poor on handwriting; kept as a
  no-network fallback).
- ``null`` — transcribes nothing; keeps the ink-trigger logic testable.

Every engine takes an optional ``hint`` naming the kind of region
(``priority``, ``due``, ``project``, ``to``, ``capture``, ``act``, ``line``,
``block``) and an optional ``context`` string (the printed text of an
edited action) so a model can be told what it is looking at.

Deterministic first, AI only by explicit opt-in: ticks, QRs and fixed slots
are read by plain Python, and a model is called only to transcribe an inked
write-in region or — when I ticked ✦ AI, explicitly asking for it — to
interpret a whole row. That second call is ``interpret``, which crops the
entire row and asks for a structured ``gtd.ai/3`` reading
(:data:`AI_SCHEMA`) via OpenRouter's ``json_schema`` response format: the
verbatim handwriting, whether it was understood, and a list of vault
operations to apply. An unclear row comes back ``understood: false`` with no
operations rather than a guess.

✦ AI is an escape hatch, not an annotation: on such a row the deterministic
classifier does not act. It still runs — its reading is passed to
:func:`build_ai_prompt` as ``suggestion``, plainly labelled as a guess the
agent may ignore — but the agent's operations are the only write. That is
why the brief below tells the model that it, and not the gutter, decides.
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
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

DEFAULT_OPENROUTER_MODEL = "google/gemini-3.5-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Reasoning earns its keep on `interpret`, which reasons about the vault. On
# `read` — transcribing glyphs — it changed no transcription in testing while
# costing ~44% of the call, so it is off there by default.
DEFAULT_AI_REASONING = "medium"
DEFAULT_READ_REASONING = "off"

READ_ANSWER_TOKENS = 300
AI_ANSWER_TOKENS = 900
# Reasoning tokens are charged against max_tokens, so a budget sized for the
# answer alone truncates it mid-JSON once reasoning is on (finish_reason
# "length"), and the AI agent's operations are lost.
REASONING_TOKEN_HEADROOM = 2500


def _reasoning_block(explicit, env_var: str, default: str) -> dict | None:
    if isinstance(explicit, dict):
        return explicit
    return parse_reasoning(explicit or os.environ.get(env_var), default)


def parse_reasoning(value: str | None, default: str) -> dict | None:
    """A reasoning setting -> an OpenRouter reasoning block, or None.

    Accepts an effort level (``low``/``medium``/``high``), a token budget
    (``1500``), or ``off``.
    """
    setting = (value if value is not None else default).strip().lower()
    if setting in ("off", "none", "no", "0", "false"):
        return None
    if setting.isdigit():
        return {"enabled": True, "max_tokens": int(setting)}
    return {"enabled": True, "effort": setting}

# What the model is told about each region kind.
_HINT_PROMPTS = {
    "priority": "It is a small box labelled PRIORITY and should contain a single integer.",
    "due": "It is a small box labelled DUE and should contain a date (for example '6 Jun', '2026-06-06' or 'Fri').",
    "project": "It is a box labelled PROJECT and should contain a short project name.",
    "to": "It is a box labelled TO and should contain a person's name.",
    "name": "It is a box labelled RENAME TO and should contain a short project name.",
    "goal": "It is a box labelled NEW GOAL and should contain a one-sentence project goal, possibly over two lines.",
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

AI_SCHEMA_VERSION = "gtd.ai/3"

# Vault operations the AI agent may ask for. The whole vocabulary is
# listed to the model, with the row's own handle implied for the ones that
# act on "this item".
OPS = (
    "update", "complete", "delete", "move", "capture", "add_next_action",
    "delegate", "schedule", "add_to_tickler", "create_project",
    "add_project_action", "rename_project", "set_project_goal",
    "archive_project",
)

# Destinations for `move`.
MOVE_TARGETS = ("next", "delegated", "inbox", "scheduled", "tickler", "project")

TICKLER_PERIODS = ("1w", "1m", "1q")

# One flat, strict object per operation: OpenRouter's strict json_schema
# mode allows no oneOf/anyOf, so every key is present on every operation
# and the ones that do not apply are null.
_OP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "op", "text", "priority", "due", "project", "person", "to",
        "period", "name", "goal",
    ],
    "properties": {
        "op": {"type": "string", "enum": list(OPS)},
        "text": {"type": ["string", "null"],
                 "description": "New or new-item wording."},
        "priority": {"type": ["integer", "null"]},
        "due": {"type": ["string", "null"], "description": "YYYY-MM-DD."},
        "project": {"type": ["string", "null"],
                    "description": "Existing project name this item belongs to."},
        "person": {"type": ["string", "null"],
                   "description": "Who a delegated item is waiting on."},
        # No enum on the nullable fields: some providers reject a null inside
        # an enum under strict mode, so the allowed values are described and
        # checked in Python instead.
        "to": {"type": ["string", "null"],
               "description": "Destination list for op=move: one of "
                              + ", ".join(MOVE_TARGETS) + "."},
        "period": {"type": ["string", "null"],
                   "description": "Tickler bucket for op=move to=tickler or "
                                  "add_to_tickler: one of " + ", ".join(TICKLER_PERIODS) + "."},
        "name": {"type": ["string", "null"],
                 "description": "Project name for op=create_project / add_project_action, "
                                "and the EXISTING project's name for rename_project / "
                                "set_project_goal / archive_project."},
        "goal": {"type": ["string", "null"],
                 "description": "One-line outcome for op=create_project / set_project_goal."},
    },
}

AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["handwriting", "understood", "confidence", "note", "operations"],
    "properties": {
        "handwriting": {
            "type": "string",
            "description": "Everything handwritten in the crop, transcribed verbatim.",
        },
        "understood": {"type": "boolean"},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
        "operations": {"type": "array", "items": _OP_SCHEMA},
    },
}

_BUCKET_LABELS = {"inbox": "Inbox", "next": "Next Actions"}


def _bucket_description(task: dict) -> str:
    bucket = task.get("bucket")
    if bucket == "delegated":
        return f"Delegated, waiting on {task.get('to') or 'someone'}"
    if bucket == "tickler":
        return f"Tickler ({task.get('period') or 'unknown period'})"
    if bucket == "project":
        return f"an item on the page of project {task.get('proj') or 'unknown'} (one of its actions)"
    if bucket == "projhead":
        return (
            f"the project row of project {task.get('proj') or 'unknown'} — it stands for "
            f"the project itself (goal: \"{task.get('goal') or 'none'}\"), not for one action"
        )
    if bucket == "capture":
        proj = task.get("proj")
        if proj:
            return f"a blank add-an-action line on the page of project {proj}"
        return "a blank capture line on the Inbox page"
    return _BUCKET_LABELS.get(bucket, bucket or "unknown")


_GTD_BRIEF = """How this GTD system works — the lists and what each one means:
- Inbox: unprocessed capture. Anything written down but not yet decided on.
- Next actions: concrete actions on my own plate, each optionally with a
  priority (higher number = more urgent), a deadline and a project.
- Delegated: things I am waiting on another person for, with a chase-by date.
- Scheduled: a dated appointment or deadline I want reminding about.
- Tickler: deferred items that resurface by themselves in 1w, 1m or 1q.
- Project pages: an outcome plus an ordered checkbox list of actions. The
  first unchecked action is that project's current next action, and it is
  the one surfaced in Next actions / Delegated / Scheduled / Tickler.

The operations you may ask for (this row's own item is implied for
update, complete, delete and move):
- update: change this item in place — new wording, priority, due, project
  or person. Use it when the annotation amends the item where it is.
- complete: tick this item off as done.
- delete: throw this item away without doing it.
- move: send this item to another list; `to` says which
  (next, delegated, inbox, scheduled, tickler, project), with `period`
  (1w/1m/1q) for tickler and `project` for project.
- capture: put new raw text into the Inbox for me to process later.
- add_next_action: create a NEW next action (text, and optionally priority,
  due, project) — not a change to this row.
- delegate: create a NEW delegated item waiting on `person`, `due` = chase-by.
- schedule: create a NEW scheduled reminder on `due`.
- add_to_tickler: create a NEW deferred item resurfacing after `period`.
- create_project: create a NEW project page with `name` and `goal`.
- add_project_action: append an action (`text`) to the project named `name`.
- rename_project: rename the existing project `name` to `text`; every link
  to it follows.
- set_project_goal: replace the goal of the existing project `name` with
  `goal`.
- archive_project: the whole project `name` is finished — its page moves to
  Done/ and every row surfacing it goes.

A project's action (a step on a project page) is an ordinary action that
stays on the project's page: move with to=next/delegated/scheduled/tickler
keeps it there and only changes where it is surfaced (my plate, waiting on
`person` with chase-by `due`, a reminder on `due`, or the tickler for
`period`); move to=inbox or to=project takes it OUT of the project. On the
project row itself (the row that stands for the whole project) use
rename_project, set_project_goal and archive_project; `name` defaults to
that project.
"""


_ACTION_GLOSS = {
    "none": "no routing box ticked",
    "done": "✓ Done",
    "activate": "→ Now (activate from the Tickler)",
    "to_next": "→ Next Actions",
    "to_me": "↩ Back to me",
    "to_deleg": "→ Delegated",
    "drop": "✗ Drop",
    "defer": "Defer to the Tickler",
}


def describe_suggestion(suggestion: dict | None) -> str:
    """Render the deterministic reading of the row as a labelled *guess*.

    The tick boxes on an ✦ AI row are still read by plain Python, and what
    they say is usually right — so it is worth telling the agent. But it is
    a suggestion and nothing more: the wording here must never read as an
    instruction, because on an AI row the agent owns the only write.
    """
    if not suggestion:
        return ""
    action = suggestion.get("action") or "none"
    gloss = _ACTION_GLOSS.get(action, action)
    period = suggestion.get("period") or suggestion.get("defer_period")
    if action == "defer" and period:
        gloss += f" for {period}"
    bits = [f"routing boxes: {gloss}"]
    if suggestion.get("new_project"):
        bits.append("the NEW box is ticked (start a project)")
    fields = suggestion.get("fields") or {}
    written = ", ".join(f"{k.upper()}={v!r}" for k, v in sorted(fields.items()) if v)
    if written:
        bits.append(f"write-in boxes: {written}")
    if suggestion.get("text"):
        bits.append(f"the write-in line reads {suggestion['text']!r}")
    return (
        "\nFOR CONTEXT ONLY — a suggestion, not an instruction. Before "
        "calling you, plain Python read this row's boxes and would have "
        "done this: " + "; ".join(bits) + ". That guess has NOT been "
        "applied and will NOT be applied: you are the only thing that "
        "writes to my vault for this row. Use it as a hint about what I "
        "probably meant, agree with it or overrule it as the handwriting "
        "warrants, and if the handwriting says something else entirely, "
        "follow the handwriting.\n"
    )


def build_ai_prompt(
    task: dict,
    vocabulary: dict | None = None,
    today: str | None = None,
    suggestion: dict | None = None,
) -> str:
    """The brief sent alongside a whole-row crop when ✦ AI is ticked.

    This is the one place in the pipeline where a model is asked to decide
    anything: every other mark on the sheet is a tick box, a QR or a fixed
    slot read deterministically. So the brief spells out how the system
    works, what each operation means, and that saying "not understood" is
    always preferable to guessing.

    ✦ AI short-circuits the deterministic path entirely, so the agent's
    scope here is arbitrary: create a project, rename one, split the row
    into several actions. ``suggestion`` (the deterministic reading, see
    :func:`describe_suggestion`) rides along as a labelled hint only.
    """
    projects = (vocabulary or {}).get("projects") or []
    people = (vocabulary or {}).get("people") or []
    proj_list = ", ".join(projects) if projects else "none"
    people_list = ", ".join(people) if people else "none"
    return (
        "You are the agent for a paper GTD (Getting Things Done) system. "
        "The crop shows ONE row of a printed sheet that I annotated by hand "
        "on an e-ink tablet: the printed item text, a gutter of tick boxes "
        "(the ✦ AI box is ticked, which is the only reason you are being "
        "asked) and, on most rows, labelled write-in boxes PRIORITY, DUE, "
        "PROJECT, TO (on a project row, RENAME TO and NEW GOAL; there ✓ "
        "Finish means the whole project is done). Read my handwriting — new words, strike-throughs, "
        "arrows, anything in or near the boxes — and say what should happen "
        "to my vault.\n\n"
        "Ticking ✦ AI means I did not want this row handled by the rigid "
        "box-by-box rules, so those rules have been switched off for it: "
        "nothing else will touch this row, and whatever you return is the "
        "only change that gets made. Your scope is therefore whatever the "
        "handwriting implies — amend the item, start a project, rename an "
        "existing one, split the row into several actions, or all of "
        "those.\n\n"
        + _GTD_BRIEF
        + "\nThe printed row as it stands: "
        f"{_bucket_description(task)}; text \"{task.get('act', '')}\"; "
        f"priority {task.get('pri') or 'none'}; due {task.get('due') or 'none'}; "
        f"project {task.get('proj') or 'none'}; "
        f"delegated to {task.get('to') or 'nobody'}.\n\n"
        f"Today is {today or 'unknown'}. My existing projects, spelled "
        f"exactly as they are named: {proj_list}. People I delegate to: "
        f"{people_list}.\n"
        + describe_suggestion(suggestion)
        + "\n"
        "Answer as JSON. handwriting = verbatim transcription of everything "
        "handwritten in the crop. confidence = 0..1. note = one sentence on "
        "how you read the row. operations = the list of operations to apply: "
        "it may be empty (the annotation changes nothing), one operation, or "
        "several (for example update this item AND add a new next action). "
        "Every key of an operation must be present; set the ones that do not "
        "apply to null.\n\n"
        "Rules:\n"
        "- If you cannot read the handwriting, or you can read it but cannot "
        "tell what I want done, set understood = false, leave operations "
        "empty and say why in note. Never guess: an unapplied row I fix by "
        "hand costs me seconds, a wrong one costs me the trust in the whole "
        "system.\n"
        "- Never invent a project. Use create_project only when the "
        "handwriting plainly says to start a new one; otherwise use a "
        "project name exactly as listed above, and if the handwriting names "
        "something that is not on the list, say so in note.\n"
        "- Dates are YYYY-MM-DD, resolved from today.\n"
        "- Prefer update over delete-and-recreate; prefer one operation over "
        "several when one says it.\n"
        "- You are the only writer for this row: a gutter tick will NOT be "
        "applied behind you, so anything that should happen — including "
        "what a routing box plainly asks for — must be in your operations "
        "or it will not happen at all."
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

    def interpret(self, image, task, vocabulary=None, today=None, suggestion=None) -> dict | None:
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
    - ``OPENROUTER_AI_MODEL`` — model id for :meth:`interpret` only, so the
      ✦ AI agent (which reasons about the vault, not just glyphs) can be a
      stronger model than the one transcribing slots. Falls back to the
      pre-rename ``OPENROUTER_EDIT_MODEL``, then ``OPENROUTER_MODEL``, then
      to the default.

    Each ``read`` is one chat-completion request carrying one small PNG, so a
    typical sheet costs a few hundred image tokens in total.
    """

    name = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        ai_model: str | None = None,
        timeout: float = 60.0,
        retries: int = 2,
        url: str = OPENROUTER_URL,
        reasoning: str | dict | None = None,
        read_reasoning: str | dict | None = None,
        trace_dir: str | Path | None = None,
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter OCR needs OPENROUTER_API_KEY in the environment"
            )
        self.model = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL
        self.ai_model = (
            ai_model
            or os.environ.get("OPENROUTER_AI_MODEL")
            or os.environ.get("OPENROUTER_EDIT_MODEL")  # pre-rename name
            or os.environ.get("OPENROUTER_MODEL")
            or DEFAULT_OPENROUTER_MODEL
        )
        self.timeout = timeout
        self.retries = retries
        self.url = url
        self.requests_made = 0
        self.ai_reasoning = _reasoning_block(
            reasoning, "OPENROUTER_REASONING", DEFAULT_AI_REASONING
        )
        self.read_reasoning = _reasoning_block(
            read_reasoning, "OPENROUTER_READ_REASONING", DEFAULT_READ_REASONING
        )
        trace = trace_dir or os.environ.get("OPENROUTER_TRACE_DIR")
        self.trace_dir = Path(trace) if trace else None
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

    def _trace_request(self, payload: dict) -> str | None:
        """Save the crops this call is about to send; returns its trace stem."""
        if not self.trace_dir:
            return None
        kind = "interpret" if "response_format" in payload else "read"
        stem = f"{self.requests_made + 1:03d}-{kind}"
        crops = [
            part["image_url"]["url"] for part in payload["messages"][0]["content"]
            if part.get("type") == "image_url"
        ]
        for i, url in enumerate(crops):
            (self.trace_dir / f"{stem}-crop{i}.png").write_bytes(
                base64.b64decode(url.split(",", 1)[1])
            )
        return stem

    def _trace_reply(self, stem: str, payload: dict, data: dict) -> None:
        """Save the prompt, the raw reply and the model's thinking."""
        prompt = "\n".join(
            part["text"] for part in payload["messages"][0]["content"]
            if part.get("type") == "text"
        )
        message = (data.get("choices") or [{}])[0].get("message") or {}
        (self.trace_dir / f"{stem}.json").write_text(json.dumps({
            "model": payload["model"],
            "reasoning": payload.get("reasoning"),
            "max_tokens": payload.get("max_tokens"),
            "prompt": prompt,
            "thinking": message.get("reasoning") or message.get("reasoning_details"),
            "response": data,
        }, indent=2), encoding="utf-8")

    def _payload(self, model, prompt, image, answer_tokens, reasoning, **extra) -> dict:
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": answer_tokens + (REASONING_TOKEN_HEADROOM if reasoning else 0),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + _to_png_b64(image)},
                        },
                    ],
                }
            ],
            **extra,
        }
        if reasoning:
            payload["reasoning"] = reasoning
        return payload

    def _post(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        traced = self._trace_request(payload)
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
                    data = json.loads(resp.read().decode("utf-8"))
                if traced:
                    self._trace_reply(traced, payload, data)
                return data
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
        payload = self._payload(
            self.model, build_prompt(hint, context), image,
            READ_ANSWER_TOKENS, self.read_reasoning,
        )
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
        suggestion: dict | None = None,
    ) -> dict:
        """Structured reading of a whole ✦-AI row: see :data:`AI_SCHEMA`."""
        payload = self._payload(
            self.ai_model,
            build_ai_prompt(task, vocabulary, today, suggestion),
            image,
            AI_ANSWER_TOKENS, self.ai_reasoning,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "gtd_ai", "strict": True, "schema": AI_SCHEMA},
            },
        )
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
            raise RuntimeError(f"OpenRouter AI reply was not valid JSON: {text[:200]}")
        if not isinstance(parsed.get("operations"), list):
            raise RuntimeError(
                f"OpenRouter AI reply has no operations list: {text[:200]}"
            )
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
