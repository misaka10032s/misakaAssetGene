"""Tests for ``core.llm.providers.gemini.critique_image`` (spec §5.15 / §3.1,
C5 fix 2026-09-06 — fidelity-critic-second-opinion). A FAKE ``httpx``
transport (``httpx.MockTransport``) stands in for the real Gemini API — no
network access, and the fake inspects the REAL serialized request body
(``httpx.Request.content``), which is stronger evidence than asserting on a
python dict handed to a monkeypatched function: it proves the exact bytes
``httpx`` would put on the wire.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from core.config import Settings
from core.llm.providers import gemini
from core.models.schemas import BodyRegion, FidelityCheck

_FAKE_KEY = "AIzaFakeTestKeyNeverLoggedAnywhere123"


def _settings(api_key: str = _FAKE_KEY) -> Settings:
    # See tests/test_vision_critic.py's own note: Settings fields all carry
    # an explicit alias= (env var name) and populate_by_name is not set, so
    # the ALIAS (not the snake_case field name) must be used here.
    return Settings(
        GEMINI_API_KEY=api_key,
        GEMINI_API_BASE_URL="https://generativelanguage.googleapis.com/v1beta",
        GEMINI_MODEL="gemini-3-flash",
    )


def _checks() -> list[FidelityCheck]:
    return [
        FidelityCheck(
            id="outfits-7",
            label_zh="身體服裝",
            pass_criteria="連身式無袖洋裝，圓領口，雙臂完全裸露。",
            region_hint=BodyRegion.TORSO,
            fix_tags=["sleeveless dress"],
            source="outfits",
            fine_detail=True,
        ),
    ]


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Route the module-level ``httpx.post`` (which ``gemini.critique_image``
    calls directly, matching every other provider in this repo) through a
    real ``httpx.MockTransport`` + ``httpx.Client`` — so the handler receives
    an ACTUAL ``httpx.Request`` with fully-serialized ``.content``/``.url``,
    not a hand-assembled python dict."""
    transport = httpx.MockTransport(handler)

    def _fake_post(url: str, **kwargs) -> httpx.Response:
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", _fake_post)


def _candidates_response(result_jsons: list[dict]) -> dict:
    body = json.dumps({"results": result_jsons})
    return {"candidates": [{"content": {"parts": [{"text": body}]}}]}


class TestNoApiKey:
    def test_returns_none_without_calling_httpx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must never call out with no GEMINI_API_KEY configured")

        _install_mock_transport(monkeypatch, _boom)
        result = gemini.critique_image(_settings(api_key=""), b"fake-png-bytes", _checks())
        assert result is None


class TestRequestShape:
    def test_body_carries_inline_data_image_and_response_mime_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        image_bytes = b"\x89PNG-fake-bytes-not-a-real-image"
        captured: dict[str, object] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=_candidates_response([]))

        _install_mock_transport(monkeypatch, _handler)
        gemini.critique_image(_settings(), image_bytes, _checks())

        assert "models/gemini-3-flash:generateContent" in captured["url"]
        assert f"key={_FAKE_KEY}" in captured["url"]  # query param, exactly how the real API expects auth

        body = captured["body"]
        parts = body["contents"][0]["parts"]
        image_part = next(part for part in parts if "inline_data" in part)
        assert image_part["inline_data"]["mime_type"] == "image/png"
        assert image_part["inline_data"]["data"] == base64.b64encode(image_bytes).decode("ascii")
        text_part = next(part for part in parts if "text" in part)
        assert "outfits-7" in text_part["text"]  # the check id is in the built prompt

        assert body["generationConfig"]["response_mime_type"] == "application/json"

    def test_api_key_never_appears_in_json_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The key is query-param auth (matching every other provider in
        this repo) — it must never leak into the JSON body itself."""
        captured_body: dict[str, object] = {}

        def _handler(request: httpx.Request) -> httpx.Response:
            captured_body["value"] = request.content.decode("utf-8")
            return httpx.Response(200, json=_candidates_response([]))

        _install_mock_transport(monkeypatch, _handler)
        gemini.critique_image(_settings(), b"img", _checks())
        assert _FAKE_KEY not in captured_body["value"]

    def test_api_key_never_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Even on a malformed-response warning path (``core.llm.
        critic_support`` logs a WARNING), the key must never appear in any
        emitted log record."""

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "not valid json {{{"}]}}]},
            )

        _install_mock_transport(monkeypatch, _handler)
        with caplog.at_level("WARNING"):
            gemini.critique_image(_settings(), b"img", _checks())
        assert any("malformed" in record.message for record in caplog.records)
        assert all(_FAKE_KEY not in record.message for record in caplog.records)
        assert all(_FAKE_KEY not in str(record.args) for record in caplog.records)


class TestResponseParsing:
    def test_parses_candidates_into_fidelity_check_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_candidates_response(
                    [
                        {
                            "id": "outfits-7", "passed": False, "confidence": 0.9,
                            "region_bbox": [420, 500, 480, 600], "note": "long sleeves with white cuffs visible",
                        }
                    ]
                ),
            )

        _install_mock_transport(monkeypatch, _handler)
        results = gemini.critique_image(_settings(), b"img", _checks())
        assert results is not None
        assert len(results) == 1
        assert results[0].id == "outfits-7"
        assert results[0].passed is False
        assert results[0].region_bbox == (420, 500, 480, 600)

    def test_returns_none_on_no_candidates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"candidates": []})

        _install_mock_transport(monkeypatch, _handler)
        assert gemini.critique_image(_settings(), b"img", _checks()) is None

    def test_returns_none_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "internal"})

        _install_mock_transport(monkeypatch, _handler)
        assert gemini.critique_image(_settings(), b"img", _checks()) is None

    def test_returns_none_on_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _install_mock_transport(monkeypatch, _handler)
        assert gemini.critique_image(_settings(), b"img", _checks()) is None
