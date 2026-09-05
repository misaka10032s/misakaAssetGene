"""Tests for the VLM fidelity critic (spec §5.15 / C-spec.md §3).

Covers ``core.llm.vision.critique`` orchestration (two-pass consistency +
anti-hallucination gates), provider fallback order, and the offline gate
disabling OpenAI — all against a FAKE httpx transport (no real Ollama/OpenAI
network access; ``httpx.get``/``httpx.post`` are monkeypatched per-module,
matching the existing convention in ``tests/test_comfyui_adapter.py``).
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import HTTPException

from core.config import Settings
from core.llm import vision
from core.llm.providers import ollama, openai
from core.models.schemas import BodyRegion, FidelityCheck
from core.network.state import NetworkState


def _resp(url: str, method: str, **kwargs) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request(method, url), **kwargs)


def _settings(
    provider_order: str = "ollama",
    ollama_base_url: str = "http://fake-ollama:11434",
    openai_api_key: str = "",
) -> Settings:
    # NOTE: Settings' fields all carry an explicit ``alias=`` (env var name)
    # and ``model_config`` does not set ``populate_by_name=True`` — so
    # constructing with the snake_case FIELD name (e.g.
    # ``misaka_llm_provider_order=``) is silently a no-op (falls back to the
    # default/env value instead of raising). The ALIAS must be used here.
    return Settings(
        MISAKA_LLM_PROVIDER_ORDER=provider_order,
        MISAKA_OLLAMA_BASE_URL=ollama_base_url,
        MISAKA_OLLAMA_VISION_MODEL="qwen2.5vl:7b",
        OPENAI_API_KEY=openai_api_key,
        ANTHROPIC_API_KEY="",
        GEMINI_API_KEY="",
    )


def _checks() -> list[FidelityCheck]:
    return [
        FidelityCheck(
            id="head-1",
            label_zh="髮型",
            pass_criteria="銀色長髮",
            region_hint=BodyRegion.HEAD,
            fix_tags=["silver hair"],
            source="setting",
        ),
        FidelityCheck(
            id="torso-1",
            label_zh="服裝",
            pass_criteria="白色連身裙",
            region_hint=BodyRegion.TORSO,
            fix_tags=["white dress"],
            source="outfits",
        ),
    ]


def _stub_ollama_tags(monkeypatch: pytest.MonkeyPatch, models: list[str] | None = None) -> None:
    payload = {"models": [{"name": m} for m in (models or ["qwen2.5vl:7b"])]}
    monkeypatch.setattr(ollama.httpx, "get", lambda url, **k: _resp(url, "GET", json=payload))


def _stub_ollama_chat_sequence(monkeypatch: pytest.MonkeyPatch, contents: list[str]) -> None:
    """Each successive call to POST /api/chat returns the next ``contents``
    entry (the last one repeats if exhausted)."""
    queue = list(contents)

    def _fake_post(url: str, **kwargs):
        content = queue.pop(0) if len(queue) > 1 else queue[0]
        return _resp(url, "POST", json={"message": {"content": content}})

    monkeypatch.setattr(ollama.httpx, "post", _fake_post)


def _result_json(check_id: str, passed: bool, bbox: list[int] | None, confidence: float = 0.9) -> dict:
    return {"id": check_id, "passed": passed, "confidence": confidence, "region_bbox": bbox, "note": "seen"}


class TestHappyPath:
    def test_both_passes_agree_fail_stays_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        body = json.dumps(
            {
                "results": [
                    _result_json("head-1", False, [10, 10, 100, 100]),
                    _result_json("torso-1", True, None),
                ]
            }
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"fake-image-bytes", _checks(), image_width=1000, image_height=1000
        )
        by_id = {r.id: r for r in results}
        assert by_id["head-1"].passed is False
        assert by_id["head-1"].confidence == pytest.approx(0.9)
        assert by_id["head-1"].region_bbox == (10, 10, 100, 100)
        assert by_id["torso-1"].passed is True


class TestAntiHallucinationGates:
    def test_unlocalized_fail_downgraded_to_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        body = json.dumps({"results": [_result_json("head-1", False, None), _result_json("torso-1", True, None)]})
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.passed is True
        assert "unlocalized" in head_result.note

    def test_oversized_bbox_downgraded_to_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        # bbox covers 900x900 of a 1000x1000 image = 81% > 60% threshold.
        body = json.dumps({"results": [_result_json("head-1", False, [0, 0, 900, 900]), _result_json("torso-1", True, None)]})
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.passed is True
        assert "bbox too large" in head_result.note

    def test_region_incompatible_bbox_downgraded_to_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        # head-1 has region_hint=HEAD (expected band: top 35% of height), but
        # this bbox's vertical center sits at 90% down the image.
        body = json.dumps(
            {"results": [_result_json("head-1", False, [10, 880, 100, 920]), _result_json("torso-1", True, None)]}
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.passed is True
        assert "region mismatch" in head_result.note

    def test_two_pass_disagreement_downgraded_to_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        pass_a_body = json.dumps(
            {"results": [_result_json("head-1", False, [10, 10, 50, 50]), _result_json("torso-1", True, None)]}
        )
        pass_b_body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, None)]}
        )
        _stub_ollama_chat_sequence(monkeypatch, [pass_a_body, pass_b_body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.passed is True
        assert "two-pass disagreement" in head_result.note

    def test_two_pass_both_fail_stays_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        body = json.dumps(
            {"results": [_result_json("head-1", False, [10, 10, 50, 50]), _result_json("torso-1", True, None)]}
        )
        # Same body both passes -> both fail on head-1, localized + region-compatible + small bbox.
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.passed is False


class TestMalformedResponse:
    def test_malformed_json_defaults_all_checks_to_pass(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _stub_ollama_tags(monkeypatch)
        _stub_ollama_chat_sequence(monkeypatch, ["this is not valid json {{{"])

        with caplog.at_level("WARNING"):
            results = vision.critique(
                _settings(), b"img", _checks(), image_width=1000, image_height=1000
            )
        assert all(r.passed is True for r in results)
        assert any("malformed" in record.message for record in caplog.records)


class TestProviderFallbackOrder:
    def test_falls_through_to_openai_when_ollama_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(
            provider_order="ollama,openai",
            ollama_base_url="",  # unconfigured -> ollama_critique_image returns None immediately
            openai_api_key="sk-test",
        )
        openai_body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, None)]}
        )

        def _fake_openai_post(url: str, **kwargs):
            return _resp(
                url, "POST",
                json={"choices": [{"message": {"content": openai_body}}]},
            )

        monkeypatch.setattr(openai.httpx, "post", _fake_openai_post)

        results = vision.critique(
            settings, b"img", _checks(), image_width=1000, image_height=1000,
            network_state=NetworkState.ONLINE,
        )
        assert all(r.passed is True for r in results)

    def test_raises_when_no_provider_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(provider_order="ollama", ollama_base_url="")
        with pytest.raises(HTTPException) as exc_info:
            vision.critique(settings, b"img", _checks(), image_width=1000, image_height=1000)
        assert exc_info.value.status_code == 409


class TestOfflineGate:
    def test_offline_mode_disables_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(provider_order="openai", ollama_base_url="", openai_api_key="sk-test")

        def _boom(url: str, **kwargs):
            raise AssertionError("OpenAI must never be called while offline")

        monkeypatch.setattr(openai.httpx, "post", _boom)

        with pytest.raises(HTTPException):
            vision.critique(
                settings, b"img", _checks(), image_width=1000, image_height=1000,
                network_state=NetworkState.OFFLINE,
            )

    def test_degraded_mode_also_disables_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(provider_order="openai", ollama_base_url="", openai_api_key="sk-test")

        def _boom(url: str, **kwargs):
            raise AssertionError("OpenAI must never be called while degraded")

        monkeypatch.setattr(openai.httpx, "post", _boom)

        with pytest.raises(HTTPException):
            vision.critique(
                settings, b"img", _checks(), image_width=1000, image_height=1000,
                network_state=NetworkState.DEGRADED,
            )
