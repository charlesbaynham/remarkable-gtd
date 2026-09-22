"""OCR engine plumbing: prompts, reply cleaning, and the OpenRouter request."""
from __future__ import annotations

import base64
import io
import json
from unittest import mock

import numpy as np
import pytest

from remarkable_gtd.scan import ocr


def test_clean_reply_strips_noise():
    assert ocr.clean_reply("  Buy milk  ") == "Buy milk"
    assert ocr.clean_reply("<empty>") == ""
    assert ocr.clean_reply("EMPTY") == ""
    assert ocr.clean_reply('"Call Dave"') == "Call Dave"
    assert ocr.clean_reply("```\n6 Jun\n```") == "6 Jun"
    assert ocr.clean_reply("") == ""


def test_prompt_mentions_region_kind():
    assert "PRIORITY" in ocr.build_prompt("priority", None)
    assert "DUE" in ocr.build_prompt("due", None)
    assert "person" in ocr.build_prompt("to", None)
    act = ocr.build_prompt("act", "Read Ben's paper")
    assert "Read Ben's paper" in act and "amended" in act
    # Unknown hint falls back to the single-line prompt.
    assert "handwritten line" in ocr.build_prompt("weird", None)


def test_get_engine_by_name_and_instance():
    assert ocr.get_engine("null").name == "null"
    eng = ocr.NullEngine()
    assert ocr.get_engine(eng) is eng
    with pytest.raises(ValueError):
        ocr.get_engine("nope")


def test_openrouter_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        ocr.OpenRouterEngine()


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openrouter_request_and_reply(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "google/gemini-test")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data.decode("utf-8"))
        reply = {"choices": [{"message": {"content": " Call Dave about the EOM \n"}}]}
        return _FakeResponse(json.dumps(reply).encode("utf-8"))

    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        eng = ocr.OpenRouterEngine()
        img = np.full((20, 120), 255, dtype=np.uint8)
        img[8:12, 10:100] = 0
        text = eng.read(img, hint="to")

    assert text == "Call Dave about the EOM"
    assert eng.requests_made == 1
    assert captured["url"] == ocr.OPENROUTER_URL
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    body = captured["body"]
    assert body["model"] == "google/gemini-test"
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "text" and "TO" in parts[0]["text"]
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    png = base64.b64decode(url.split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_openrouter_content_parts_and_empty(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def fake_urlopen(req, timeout=None):
        reply = {"choices": [{"message": {"content": [{"type": "text", "text": "<empty>"}]}}]}
        return _FakeResponse(json.dumps(reply).encode("utf-8"))

    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        assert ocr.OpenRouterEngine().read(np.zeros((10, 10), np.uint8)) == ""


def test_build_ai_prompt_mentions_printed_text_project_and_today():
    task = {"id": "NA-06", "bucket": "next", "act": "Make a plan", "pri": 3, "due": None, "proj": None}
    prompt = ocr.build_ai_prompt(
        task, vocabulary={"projects": ["Wedding 2026"], "people": ["Louise"]}, today="2026-09-15"
    )
    assert "Make a plan" in prompt
    assert "Wedding 2026" in prompt
    assert "2026-09-15" in prompt
    assert "Louise" in prompt


def test_build_ai_prompt_is_a_brief_not_a_transcription_order():
    prompt = ocr.build_ai_prompt({"bucket": "next", "act": "x"})
    # Explains the buckets...
    for phrase in ("Inbox", "Next actions", "Delegated", "Tickler",
                   "Scheduled", "Project pages"):
        assert phrase in prompt
    # ...names every operation it may ask for...
    for op in ocr.OPS:
        assert op in prompt
    # ...and forbids guessing or inventing a project.
    assert "understood = false" in prompt
    assert "Never guess" in prompt
    assert "Never invent a project" in prompt
    assert "none/one/several" in prompt or "empty" in prompt


def test_build_ai_prompt_describes_a_project_page_row():
    prompt = ocr.build_ai_prompt(
        {"bucket": "project", "act": "Order the cake", "proj": "Wedding 2026"}
    )
    assert "item on the page of project Wedding 2026" in prompt


def test_build_ai_prompt_describes_the_project_row_and_its_ops():
    prompt = ocr.build_ai_prompt(
        {"bucket": "projhead", "act": "Wedding 2026", "proj": "Wedding 2026",
         "goal": "Married without debt"}
    )
    assert "project row of project Wedding 2026" in prompt
    assert "Married without debt" in prompt
    for op in ("rename_project", "set_project_goal", "archive_project"):
        assert op in prompt
        assert op in ocr.OPS
    # a step delegated from its project stays on the project's page
    assert "keeps it there" in prompt


def test_edit_schema_is_v2_flat_operations():
    assert ocr.AI_SCHEMA_VERSION == "gtd.ai/3"
    assert not hasattr(ocr, "ROUTES")
    props = ocr.AI_SCHEMA["properties"]
    assert set(props) == {
        "handwriting", "understood", "confidence", "note", "operations"
    }
    assert set(ocr.AI_SCHEMA["required"]) == set(props)
    assert ocr.AI_SCHEMA["additionalProperties"] is False

    op = props["operations"]["items"]
    # One flat strict object: OpenRouter's strict mode allows no oneOf/anyOf.
    assert op["additionalProperties"] is False
    assert set(op["required"]) == set(op["properties"])
    assert set(op["properties"]) == {
        "op", "text", "priority", "due", "project", "person", "to",
        "period", "name", "goal",
    }
    assert "oneOf" not in json.dumps(op) and "anyOf" not in json.dumps(op)
    assert op["properties"]["op"]["enum"] == list(ocr.OPS)
    assert "create_project" in ocr.OPS and "add_project_action" in ocr.OPS
    # nullable fields carry no enum (strict-mode providers reject null in one)
    assert "enum" not in op["properties"]["to"]
    assert all(t in op["properties"]["to"]["description"] for t in ocr.MOVE_TARGETS)
    assert "enum" not in op["properties"]["period"]
    # Every non-op key is nullable.
    for key, spec in op["properties"].items():
        if key == "op":
            continue
        assert "null" in spec["type"], key


def test_edit_model_env_precedence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_EDIT_MODEL", raising=False)
    eng = ocr.OpenRouterEngine()
    assert eng.model == eng.ai_model == ocr.DEFAULT_OPENROUTER_MODEL

    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/cheap")
    eng = ocr.OpenRouterEngine()
    assert eng.model == "vendor/cheap" and eng.ai_model == "vendor/cheap"

    monkeypatch.setenv("OPENROUTER_EDIT_MODEL", "vendor/clever")
    eng = ocr.OpenRouterEngine()
    assert eng.model == "vendor/cheap" and eng.ai_model == "vendor/clever"

    # Constructor arguments win over both.
    eng = ocr.OpenRouterEngine(model="a/b", ai_model="c/d")
    assert eng.model == "a/b" and eng.ai_model == "c/d"


def test_null_engine_interpret_is_none():
    assert ocr.NullEngine().interpret(np.zeros((10, 10), np.uint8), {"act": "x", "bucket": "next"}) is None


def test_build_prompt_project_vocabulary():
    prompt = ocr.build_prompt("project", "Wedding 2026, ERC grant")
    assert "Wedding 2026" in prompt and "ERC grant" in prompt


def test_openrouter_interpret_request_and_reply(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    captured: dict = {}
    reply_body = {
        "handwriting": "Tell Louise I pulled out",
        "understood": True,
        "confidence": 0.9,
        "note": "struck through and rewritten",
        "operations": [
            {"op": "update", "text": "Tell Louise I pulled out", "priority": None,
             "due": None, "project": None, "person": None, "to": None,
             "period": None, "name": None, "goal": None},
        ],
    }

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        reply = {"choices": [{"message": {"content": json.dumps(reply_body)}}]}
        return _FakeResponse(json.dumps(reply).encode("utf-8"))

    task = {"id": "NA-06", "bucket": "next", "act": "Make a plan", "pri": 3, "due": None, "proj": "Wedding 2026"}
    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        eng = ocr.OpenRouterEngine()
        img = np.full((60, 400), 255, dtype=np.uint8)
        result = eng.interpret(img, task, vocabulary={"projects": ["Wedding 2026"]}, today="2026-09-15")

    assert result == reply_body
    assert eng.requests_made == 1
    body = captured["body"]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == ocr.AI_SCHEMA
    prompt_text = body["messages"][0]["content"][0]["text"]
    assert "Make a plan" in prompt_text
    assert "Wedding 2026" in prompt_text
    assert "2026-09-15" in prompt_text


def test_interpret_uses_the_edit_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/cheap")
    monkeypatch.setenv("OPENROUTER_EDIT_MODEL", "vendor/clever")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured.setdefault("models", []).append(
            json.loads(req.data.decode("utf-8"))["model"]
        )
        content = json.dumps({
            "handwriting": "", "understood": False, "confidence": 0.0,
            "note": "blank", "operations": [],
        })
        return _FakeResponse(json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8"))

    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        eng = ocr.OpenRouterEngine()
        eng.interpret(np.zeros((10, 10), np.uint8), {"act": "x", "bucket": "next"})
        eng.read(np.zeros((10, 10), np.uint8), hint="due")

    assert captured["models"] == ["vendor/clever", "vendor/cheap"]


def test_interpret_rejects_a_reply_without_operations(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def fake_urlopen(req, timeout=None):
        content = json.dumps({
            "handwriting": "x", "understood": True, "confidence": 0.9, "note": "",
        })
        return _FakeResponse(json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8"))

    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        with pytest.raises(RuntimeError, match="operations"):
            ocr.OpenRouterEngine().interpret(
                np.zeros((10, 10), np.uint8), {"act": "x", "bucket": "next"}
            )


def test_openrouter_interpret_reply_wrapped_in_fence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    reply_body = {
        "handwriting": "x", "understood": False, "confidence": 0.1,
        "note": "illegible", "operations": [],
    }

    def fake_urlopen(req, timeout=None):
        content = "```json\n" + json.dumps(reply_body) + "\n```"
        reply = {"choices": [{"message": {"content": content}}]}
        return _FakeResponse(json.dumps(reply).encode("utf-8"))

    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        eng = ocr.OpenRouterEngine()
        result = eng.interpret(np.zeros((10, 10), np.uint8), {"act": "x", "bucket": "next"})

    assert result == reply_body


def test_openrouter_interpret_unparseable_reply_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def fake_urlopen(req, timeout=None):
        reply = {"choices": [{"message": {"content": "not json at all"}}]}
        return _FakeResponse(json.dumps(reply).encode("utf-8"))

    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        eng = ocr.OpenRouterEngine()
        with pytest.raises(RuntimeError):
            eng.interpret(np.zeros((10, 10), np.uint8), {"act": "x", "bucket": "next"})


@pytest.mark.parametrize("setting,default,expected", [
    (None, "medium", {"enabled": True, "effort": "medium"}),
    (None, "off", None),
    ("high", "off", {"enabled": True, "effort": "high"}),
    ("1500", "off", {"enabled": True, "max_tokens": 1500}),
    ("off", "medium", None),
])
def test_parse_reasoning(setting, default, expected):
    assert ocr.parse_reasoning(setting, default) == expected


def _capture(monkeypatch, call, trace_dir=None):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        reply = {"choices": [{"message": {
            "content": json.dumps({"understood": False, "operations": []}),
            "reasoning": "It is a digit.",
        }}]}
        return _FakeResponse(json.dumps(reply).encode("utf-8"))

    img = np.zeros((10, 10), np.uint8)
    with mock.patch.object(ocr.urllib.request, "urlopen", fake_urlopen):
        eng = ocr.OpenRouterEngine(trace_dir=trace_dir)
        eng.read(img) if call == "read" else eng.interpret(img, {"id": "NA-01"})
    return captured["body"]


def test_read_does_not_reason_by_default(monkeypatch):
    monkeypatch.delenv("OPENROUTER_READ_REASONING", raising=False)
    body = _capture(monkeypatch, "read")
    assert "reasoning" not in body
    assert body["max_tokens"] == ocr.READ_ANSWER_TOKENS


def test_interpret_reasons_by_default_with_room_for_the_answer(monkeypatch):
    monkeypatch.delenv("OPENROUTER_REASONING", raising=False)
    body = _capture(monkeypatch, "interpret")
    assert body["reasoning"] == {"enabled": True, "effort": "medium"}
    assert body["max_tokens"] == ocr.AI_ANSWER_TOKENS + ocr.REASONING_TOKEN_HEADROOM
    assert body["response_format"]["json_schema"]["strict"] is True


def test_read_reasoning_can_be_turned_on(monkeypatch):
    monkeypatch.setenv("OPENROUTER_READ_REASONING", "high")
    body = _capture(monkeypatch, "read")
    assert body["reasoning"] == {"enabled": True, "effort": "high"}
    assert body["max_tokens"] == ocr.READ_ANSWER_TOKENS + ocr.REASONING_TOKEN_HEADROOM


def test_interpret_reasoning_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("OPENROUTER_REASONING", "off")
    body = _capture(monkeypatch, "interpret")
    assert "reasoning" not in body
    assert body["max_tokens"] == ocr.AI_ANSWER_TOKENS


def test_trace_dir_keeps_prompt_crop_and_thinking(monkeypatch, tmp_path):
    _capture(monkeypatch, "read", trace_dir=tmp_path)
    assert (tmp_path / "001-read-crop0.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    traced = json.loads((tmp_path / "001-read.json").read_text())
    assert "Transcribe" in traced["prompt"]
    assert traced["thinking"] == "It is a digit."


# --- the deterministic suggestion handed to the AI agent --------------------


def test_describe_suggestion_is_labelled_a_suggestion_not_an_instruction():
    text = ocr.describe_suggestion(
        {"action": "to_deleg", "new_project": True,
         "fields": {"to": "Louise", "project": "Lab move"}}
    )
    low = text.lower()
    assert "suggestion, not an instruction" in low
    assert "→ delegated" in low          # the gloss, not the raw verb
    assert "not be applied" in low        # it is explicitly inert
    assert "only thing that writes" in low
    assert "TO='Louise'" in text and "PROJECT='Lab move'" in text
    assert "new box is ticked" in low


def test_describe_suggestion_of_nothing_is_nothing():
    assert ocr.describe_suggestion(None) == ""
    assert ocr.describe_suggestion({}) == ""


def test_describe_suggestion_names_the_defer_period():
    text = ocr.describe_suggestion({"action": "defer", "defer_period": "1q"})
    assert "for 1q" in text


def test_build_ai_prompt_carries_the_suggestion_and_owns_the_write():
    prompt = ocr.build_ai_prompt(
        {"bucket": "inbox", "act": "Book the PSU repair"},
        today="2026-09-17",
        suggestion={"action": "to_next", "new_project": False},
    )
    assert "suggestion, not an instruction" in prompt
    # The brief must say the gutter will not be applied behind the agent,
    # or the model may reasonably leave routing to "the deterministic part".
    assert "only writer for this row" in prompt
    assert "will NOT be applied" in prompt


def test_build_ai_prompt_without_a_suggestion_is_still_valid():
    prompt = ocr.build_ai_prompt({"bucket": "next", "act": "x"})
    assert "suggestion, not an instruction" not in prompt
    assert "only writer for this row" in prompt
