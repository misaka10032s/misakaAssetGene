"""Integration test — real Ollama vision critique for the fidelity loop
(spec §5.15 / C-spec.md §7 "整合(slow, skip-if-unavailable): 真 Ollama + 真
ComfyUI 跑 1 輪").

Skips (never fails a normal ``pytest -q`` run) unless BOTH live services
answer:

- Ollama's ``/api/tags`` is reachable AND lists
  ``settings.misaka_ollama_vision_model``.
- ComfyUI's health-check URL (``workers/manifest.json``'s
  ``comfyui.health_check``, ``http://127.0.0.1:8188/system_stats``) is
  reachable.

Only ONE real call happens here: ``core.llm.vision.critique`` against a tiny
synthetic solid-colour PNG fixture (``tests/fixtures``, never a real
character image). ComfyUI is checked for reachability only — this test
NEVER calls ``GenerationService.refine_asset``/``execute_job`` or anything
else that would dispatch a real ComfyUI generation; the assertion is only
"round-0 critique returns N results with the gate fields", not
correctness of the verdicts themselves (a solid colour has no character
features to actually match against).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from core.config import get_settings
from core.consultant.fidelity import parse_character_checklist
from core.llm import vision
from core.network.state import NetworkState

pytestmark = pytest.mark.slow

_COMFYUI_HEALTH_URL = "http://127.0.0.1:8188/system_stats"
_PROBE_TIMEOUT_SEC = 2.0
_FIXTURE_PNG = Path(__file__).parent / "fixtures" / "fidelity_solid_64x64.png"
_FIXTURE_WIDTH = 64
_FIXTURE_HEIGHT = 64

# 100% synthetic minimal SSOT pair — never a real character's text (repo
# convention, see tests/test_fidelity_parser.py's own fixture docstring).
_SETTING_MD = """# 角色設定：測試花

## 🎨 外型特徵
- **髮型**：銀色長髮。
- **瞳色**：紫色瞳孔。
"""

_OUTFITS_MD = """# 服裝與形態變體：測試花

## 👗 常駐服裝
1. **[Default] 常駐服裝**:
    - **身體服裝**：白色連身裙。
    - **生成提示詞**：
        - **ComfyUI**: `white dress`
"""


def _ollama_has_vision_model() -> bool:
    settings = get_settings()
    try:
        response = httpx.get(f"{settings.misaka_ollama_base_url.rstrip('/')}/api/tags", timeout=_PROBE_TIMEOUT_SEC)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        models = response.json().get("models", [])
    except ValueError:
        return False
    names = [str(model.get("name", "")) for model in models if isinstance(model, dict)]
    return any(settings.misaka_ollama_vision_model in name for name in names)


def _comfyui_reachable() -> bool:
    try:
        response = httpx.get(_COMFYUI_HEALTH_URL, timeout=_PROBE_TIMEOUT_SEC)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _skip_reason() -> str | None:
    settings = get_settings()
    if not _ollama_has_vision_model():
        return (
            f"Ollama at {settings.misaka_ollama_base_url} unreachable or missing "
            f"vision model {settings.misaka_ollama_vision_model!r} (GET /api/tags)"
        )
    if not _comfyui_reachable():
        return f"ComfyUI unreachable at {_COMFYUI_HEALTH_URL} (GET /system_stats)"
    return None


def test_real_round0_critique_returns_gated_results_for_every_check() -> None:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)

    settings = get_settings()
    checks = parse_character_checklist(_SETTING_MD, _OUTFITS_MD, "Default")
    image_bytes = _FIXTURE_PNG.read_bytes()

    results = vision.critique(
        settings, image_bytes, checks, _FIXTURE_WIDTH, _FIXTURE_HEIGHT, NetworkState.OFFLINE
    )

    # This test's contract is intentionally NOT "the critic agrees the
    # solid-colour fixture matches" (it obviously never will) — only that a
    # real live call round-trips: one gated FidelityCheckResult per check,
    # each with well-formed gate fields.
    assert len(results) == len(checks)
    result_ids = {result.id for result in results}
    assert result_ids == {check.id for check in checks}
    for result in results:
        assert isinstance(result.passed, bool)
        assert 0.0 <= result.confidence <= 1.0
        if result.region_bbox is not None:
            x0, y0, x1, y1 = result.region_bbox
            assert x0 < x1
            assert y0 < y1
