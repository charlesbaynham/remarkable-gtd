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


def test_build_edit_prompt_mentions_printed_text_project_and_today():
    task = {"id": "NA-06", "bucket": "next", "act": "Make a plan", "pri": 3, "due": None, "proj": None}
    prompt = ocr.build_edit_prompt(
        task, vocabulary={"projects": ["Wedding 2026"], "people": ["Louise"]}, today="2026-09-15"
    )
    assert "Make a plan" in prompt
    assert "Wedding 2026" in prompt
    assert "2026-09-15" in prompt
    assert "Louise" in prompt


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
        "route": "keep",
        "text": "Tell Louise I pulled out",
        "priority": None,
        "due": None,
        "project": None,
        "person": None,
        "note": "struck through and rewritten",
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
    assert body["response_format"]["json_schema"]["schema"] == ocr.EDIT_SCHEMA
    prompt_text = body["messages"][0]["content"][0]["text"]
    assert "Make a plan" in prompt_text
    assert "Wedding 2026" in prompt_text
    assert "2026-09-15" in prompt_text


def test_openrouter_interpret_reply_wrapped_in_fence(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    reply_body = {
        "handwriting": "x", "understood": False, "confidence": 0.1, "route": "keep",
        "text": None, "priority": None, "due": None, "project": None, "person": None,
        "note": "illegible",
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
