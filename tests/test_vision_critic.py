"""Tests for the VLM fidelity critic (spec §5.15 / C-spec.md §3).

Covers ``core.llm.vision.critique`` orchestration (two-pass consistency +
anti-hallucination gates), provider fallback order, and the offline gate
disabling OpenAI — all against a FAKE httpx transport (no real Ollama/OpenAI
network access; ``httpx.get``/``httpx.post`` are monkeypatched per-module,
matching the existing convention in ``tests/test_comfyui_adapter.py``).
"""

from __future__ import annotations

import json
from collections.abc import Callable

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
    gemini_api_key: str = "",
    second_opinion: str = "gemini",
    pass_min_confidence: float = 0.7,
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
        GEMINI_API_KEY=gemini_api_key,
        MISAKA_FIDELITY_SECOND_OPINION=second_opinion,
        MISAKA_FIDELITY_PASS_MIN_CONFIDENCE=pass_min_confidence,
    )


def _checks(*, fine_detail_id: str | None = None) -> list[FidelityCheck]:
    """Two checks (``head-1``/HEAD, ``torso-1``/TORSO). ``fine_detail_id``
    marks one of them ``fine_detail=True`` (C5 fix gate #5 target) — neither
    is fine_detail by default, matching every pre-C5 test in this file."""
    return [
        FidelityCheck(
            id="head-1",
            label_zh="髮型",
            pass_criteria="銀色長髮",
            region_hint=BodyRegion.HEAD,
            fix_tags=["silver hair"],
            source="setting",
            fine_detail=fine_detail_id == "head-1",
        ),
        FidelityCheck(
            id="torso-1",
            label_zh="服裝",
            pass_criteria="白色連身裙",
            region_hint=BodyRegion.TORSO,
            fix_tags=["white dress"],
            source="outfits",
            fine_detail=fine_detail_id == "torso-1",
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


def _stub_ollama_and_gemini(
    monkeypatch: pytest.MonkeyPatch,
    ollama_content: str,
    gemini_handler: Callable[[str, dict], httpx.Response] | None,
    captured_gemini_requests: list[dict] | None = None,
) -> None:
    """Route ``httpx.post`` to an Ollama-shaped or Gemini-shaped fake
    response by URL. NOTE: ``ollama.httpx``/``gemini.httpx``/``openai.httpx``
    are literally the SAME ``httpx`` module object (one process-wide import
    cache) — monkeypatching one provider's ``.post`` therefore replaces
    every OTHER provider's ``.post`` too, since they all read the identical
    shared attribute at call time. Patching two providers separately in the
    same test (as an earlier draft of these tests did) silently makes the
    LAST patch win for every call, including the primary Ollama pass — this
    single dispatcher is the fix. ``gemini_handler=None`` means "Gemini must
    never be called in this test" (raises loudly if it is)."""

    def _fake_post(url: str, **kwargs):
        if "fake-ollama" in url:
            return _resp(url, "POST", json={"message": {"content": ollama_content}})
        if captured_gemini_requests is not None:
            captured_gemini_requests.append(kwargs)
        if gemini_handler is None:
            raise AssertionError(f"Gemini must never be called in this test (POST {url})")
        return gemini_handler(url, kwargs)

    monkeypatch.setattr(httpx, "post", _fake_post)


def _result_json(
    check_id: str, passed: bool, bbox: list[int] | None, confidence: float = 0.9, note: str = "seen"
) -> dict:
    return {"id": check_id, "passed": passed, "confidence": confidence, "region_bbox": bbox, "note": note}


def _gemini_response_json(*result_jsons: dict) -> dict:
    """Wrap one or more :func:`_result_json` dicts into a Gemini
    ``generateContent`` response envelope (candidates -> content -> parts ->
    text), mirroring the real shape ``core.llm.providers.gemini.
    critique_image`` parses."""
    body = json.dumps({"results": list(result_jsons)})
    return {"candidates": [{"content": {"parts": [{"text": body}]}}]}


class TestHappyPath:
    def test_both_passes_agree_fail_stays_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        # torso-1's pass carries a small, region-compatible bbox (TORSO band
        # 0.2-0.65 of height; center_y=450/1000=0.45) so gate #4 (C5 fix)
        # never touches it — this test is about the two-pass fail merge
        # (head-1), not the localized-pass gate.
        body = json.dumps(
            {
                "results": [
                    _result_json("head-1", False, [10, 10, 100, 100]),
                    _result_json("torso-1", True, [400, 400, 500, 500]),
                ]
            }
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"fake-image-bytes", _checks(), image_width=1000, image_height=1000
        )
        by_id = {r.id: r for r in results}
        assert by_id["head-1"].passed is False
        assert by_id["head-1"].verdict == "fail"
        assert by_id["head-1"].confidence == pytest.approx(0.9)
        assert by_id["head-1"].region_bbox == (10, 10, 100, 100)
        assert by_id["torso-1"].passed is True
        assert by_id["torso-1"].verdict == "pass"


class TestAntiHallucinationGates:
    def test_unlocalized_fail_downgraded_to_unverified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A fail the critic could not localize at all (gate #1) is no
        longer promoted all the way to a blind ``pass`` (pre-C5 behavior) —
        gate #4 (C5 fix) immediately re-flags it ``unverified``, since it
        still has no bbox to confirm the pass against."""
        _stub_ollama_tags(monkeypatch)
        body = json.dumps({"results": [_result_json("head-1", False, None), _result_json("torso-1", True, [400, 400, 500, 500])]})
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
        assert head_result.passed is False
        assert "unlocalized" in head_result.note

    def test_oversized_bbox_downgraded_to_unverified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same shape for gate #2's oversized-bbox fail-discard: the bbox
        that was too large to trust as a FAIL is equally too large to trust
        as a PASS (gate #4), so it lands on ``unverified`` rather than a
        blind ``pass``."""
        _stub_ollama_tags(monkeypatch)
        # bbox covers 900x900 of a 1000x1000 image = 81% > 60% threshold.
        body = json.dumps({"results": [_result_json("head-1", False, [0, 0, 900, 900]), _result_json("torso-1", True, [400, 400, 500, 500])]})
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
        assert head_result.passed is False
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
    def test_malformed_json_defaults_all_checks_to_unverified(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed-JSON default-pass (``confidence=0.0``, no bbox) is
        EXACTLY the shape gate #4 (C5 fix) exists to catch — it is never
        blindly trusted as ``pass`` anymore, it lands on ``unverified``."""
        _stub_ollama_tags(monkeypatch)
        _stub_ollama_chat_sequence(monkeypatch, ["this is not valid json {{{"])

        with caplog.at_level("WARNING"):
            results = vision.critique(
                _settings(), b"img", _checks(), image_width=1000, image_height=1000
            )
        assert all(r.verdict == "unverified" for r in results)
        assert all(r.passed is False for r in results)
        assert any("malformed" in record.message for record in caplog.records)


class TestProviderFallbackOrder:
    def test_falls_through_to_openai_when_ollama_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = _settings(
            provider_order="ollama,openai",
            ollama_base_url="",  # unconfigured -> ollama_critique_image returns None immediately
            openai_api_key="sk-test",
        )
        # Both localized (small, region-compatible bbox) + high confidence
        # so gate #4 (C5 fix) leaves them as genuine passes — this test is
        # about provider FALLBACK ORDER, not the localized-pass gate.
        openai_body = json.dumps(
            {
                "results": [
                    _result_json("head-1", True, [400, 50, 500, 150]),
                    _result_json("torso-1", True, [400, 400, 500, 500]),
                ]
            }
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


class TestGate4LocalizedPassRequired:
    """spec §3.4 gate #4 (C5 fix, 2026-09-06) — a ``pass`` this untrustworthy
    is never reported as a confirmed pass. Second opinion left OFF here
    (``second_opinion="off"``) so these tests isolate gate #4 alone."""

    def test_pass_without_bbox_becomes_unverified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, [400, 400, 500, 500])]}
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(second_opinion="off"), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
        assert head_result.passed is False
        assert "unlocalized pass" in head_result.note

    def test_pass_with_oversized_bbox_becomes_unverified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        # bbox covers 900x900 of a 1000x1000 image = 81% > 60% threshold.
        body = json.dumps(
            {"results": [_result_json("head-1", True, [0, 0, 900, 900]), _result_json("torso-1", True, [400, 400, 500, 500])]}
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(second_opinion="off"), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
        assert "bbox too large pass" in head_result.note

    def test_low_confidence_pass_becomes_unverified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        body = json.dumps(
            {
                "results": [
                    _result_json("head-1", True, [400, 50, 500, 150], confidence=0.4),
                    _result_json("torso-1", True, [400, 400, 500, 500]),
                ]
            }
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(second_opinion="off"), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
        assert head_result.passed is False
        assert "low confidence" in head_result.note

    def test_confident_localized_pass_stays_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        body = json.dumps(
            {
                "results": [
                    _result_json("head-1", True, [400, 50, 500, 150], confidence=0.95),
                    _result_json("torso-1", True, [400, 400, 500, 500]),
                ]
            }
        )
        _stub_ollama_chat_sequence(monkeypatch, [body])

        results = vision.critique(
            _settings(second_opinion="off"), b"img", _checks(), image_width=1000, image_height=1000
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "pass"
        assert head_result.passed is True


class TestGate5SecondOpinion:
    """spec §3.4 gate #5 (C5 fix, 2026-09-06). Gemini's own httpx call is
    faked (never a real network call); ``network_state=ONLINE`` + a fake
    ``GEMINI_API_KEY`` are required for the gate to actually call out."""

    def test_fine_detail_pass_confirmed_by_second_opinion_stays_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {
                "results": [
                    _result_json("head-1", False, [10, 10, 50, 50]),  # confirmed fail, never sent to gemini
                    _result_json("torso-1", True, [400, 400, 500, 500]),
                ]
            }
        )
        captured_requests: list[dict] = []

        def _gemini_handler(url: str, kwargs: dict) -> httpx.Response:
            return _resp(
                url, "POST",
                json=_gemini_response_json(_result_json("torso-1", True, [410, 410, 490, 490], confidence=0.95)),
            )

        _stub_ollama_and_gemini(monkeypatch, ollama_body, _gemini_handler, captured_requests)

        results = vision.critique(
            _settings(gemini_api_key="fake-gemini-key"), b"img", _checks(fine_detail_id="torso-1"),
            image_width=1000, image_height=1000, network_state=NetworkState.ONLINE,
        )
        torso_result = next(r for r in results if r.id == "torso-1")
        assert torso_result.verdict == "pass"
        assert torso_result.passed is True
        assert "second opinion agrees" in torso_result.note
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "fail"  # untouched — not fine_detail, not unverified

        assert len(captured_requests) == 1  # ONLY the fine_detail check triggers a call
        sent_text = captured_requests[0]["json"]["contents"][0]["parts"][0]["text"]
        assert "torso-1" in sent_text
        assert "head-1" not in sent_text

    def test_fine_detail_pass_contradicted_by_second_opinion_becomes_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {"results": [_result_json("head-1", True, [400, 50, 500, 150]), _result_json(
                "torso-1", True, [400, 400, 500, 500], note="local: looks sleeveless"
            )]}
        )

        def _gemini_handler(url: str, kwargs: dict) -> httpx.Response:
            return _resp(
                url, "POST",
                json=_gemini_response_json(
                    _result_json("torso-1", False, [420, 500, 480, 600], note="long sleeves with white cuffs visible")
                ),
            )

        _stub_ollama_and_gemini(monkeypatch, ollama_body, _gemini_handler)

        results = vision.critique(
            _settings(gemini_api_key="fake-gemini-key"), b"img", _checks(fine_detail_id="torso-1"),
            image_width=1000, image_height=1000, network_state=NetworkState.ONLINE,
        )
        torso_result = next(r for r in results if r.id == "torso-1")
        assert torso_result.verdict == "fail"
        assert torso_result.passed is False
        assert "looks sleeveless" in torso_result.note  # local note preserved
        assert "long sleeves with white cuffs visible" in torso_result.note  # second-opinion note preserved

    def test_unverified_check_confirmed_by_second_opinion_becomes_pass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``pass`` gate #4 already downgraded to ``unverified`` (no bbox)
        can still be RESCUED by an agreeing second opinion — never left
        stuck unverified forever when a resolution is actually available."""
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, [400, 400, 500, 500])]}
        )

        def _gemini_handler(url: str, kwargs: dict) -> httpx.Response:
            return _resp(
                url, "POST",
                json=_gemini_response_json(_result_json("head-1", True, [400, 50, 500, 150], confidence=0.9)),
            )

        _stub_ollama_and_gemini(monkeypatch, ollama_body, _gemini_handler)

        results = vision.critique(
            _settings(gemini_api_key="fake-gemini-key"), b"img", _checks(),
            image_width=1000, image_height=1000, network_state=NetworkState.ONLINE,
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "pass"
        assert head_result.passed is True

    def test_second_opinion_unavailable_keeps_unverified(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No ``GEMINI_API_KEY`` configured -> ``gemini.critique_image``
        returns ``None`` BEFORE ever calling ``httpx.post`` -> gate #5
        degrades gracefully: stays ``unverified``, logged at INFO, never
        silently promoted to pass."""
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, [400, 400, 500, 500])]}
        )
        # gemini_handler=None: asserts Gemini's httpx.post is never reached
        # (the no-key short-circuit inside gemini.critique_image must fire
        # first) — a stricter check than merely stubbing a response.
        _stub_ollama_and_gemini(monkeypatch, ollama_body, None)

        with caplog.at_level("INFO"):
            results = vision.critique(
                _settings(gemini_api_key=""), b"img", _checks(),
                image_width=1000, image_height=1000, network_state=NetworkState.ONLINE,
            )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
        assert head_result.passed is False
        assert any("unavailable" in record.message for record in caplog.records)

    def test_second_opinion_off_leaves_unverified_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, [400, 400, 500, 500])]}
        )
        _stub_ollama_and_gemini(monkeypatch, ollama_body, None)  # Gemini must never be called

        results = vision.critique(
            _settings(second_opinion="off", gemini_api_key="fake-gemini-key"), b"img", _checks(),
            image_width=1000, image_height=1000, network_state=NetworkState.ONLINE,
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"

    def test_non_fine_detail_pass_never_gets_second_opinion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A confidently localized pass on an ORDINARY (non-fine_detail)
        check is never sent for a second opinion — gate #5 is narrow, not a
        blanket re-check of everything."""
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {
                "results": [
                    _result_json("head-1", True, [400, 50, 500, 150]),
                    _result_json("torso-1", True, [400, 400, 500, 500]),
                ]
            }
        )
        _stub_ollama_and_gemini(monkeypatch, ollama_body, None)  # Gemini must never be called

        results = vision.critique(
            _settings(gemini_api_key="fake-gemini-key"), b"img", _checks(),
            image_width=1000, image_height=1000, network_state=NetworkState.ONLINE,
        )
        assert all(r.verdict == "pass" for r in results)

    def test_second_opinion_offline_never_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Gemini is CLOUD (core.llm.router) — offline gates it exactly like
        the primary-provider offline gate does."""
        _stub_ollama_tags(monkeypatch)
        ollama_body = json.dumps(
            {"results": [_result_json("head-1", True, None), _result_json("torso-1", True, [400, 400, 500, 500])]}
        )
        _stub_ollama_and_gemini(monkeypatch, ollama_body, None)  # Gemini must never be called

        results = vision.critique(
            _settings(gemini_api_key="fake-gemini-key"), b"img", _checks(),
            image_width=1000, image_height=1000, network_state=NetworkState.OFFLINE,
        )
        head_result = next(r for r in results if r.id == "head-1")
        assert head_result.verdict == "unverified"
