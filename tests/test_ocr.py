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
