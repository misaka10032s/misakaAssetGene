"""API-level tests for Brief 3 (spec §5.15 / C-spec.md §4.3-4.5):

- ``FidelitySuggestionCard`` emission on the project-scoped consultant
  clarify route, wired through real project state (an imported IMAGE asset
  + a real CharacterSheet with ``sheet_source_path``).
- ``PATCH /api/v1/projects/{project_id}/settings`` (``auto_loop_enabled``).
- ``FidelityLoopStartRequest.auto_continue`` defaulting from the project
  setting when omitted.

All critic/mask/refine calls are FAKED via ``FidelityService``'s injection
points, same idiom as ``tests/test_fidelity_loop_api.py`` — no real
Ollama/ComfyUI network access anywhere in this file.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.consultant.fidelity_service import FidelityService
from core.models.schemas import FidelityCheckResult
from core.project.manager import ProjectManager

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _flat_png(width: int = 32, height: int = 32, rgb: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes(rgb) * width
    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        raw.extend(row)
    idat = zlib.compress(bytes(raw))
    return _PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


SETTING_MD = """# 角色設定：測試花

## 🎨 外型特徵
- **髮型**：銀色長髮。

## 🏷️ 標籤
`test hana, silver hair`
"""

OUTFITS_MD = """# 服裝與形態變體：測試花

## 👗 常駐服裝
1. **[Default] 常駐服裝**:
    - **身體服裝**：白色連身裙。
    - **生成提示詞**：
        - **ComfyUI**: `white dress`
2. **[Gothic] 哥德服裝**:
    - **身體服裝**：黑色蕾絲洋裝。
    - **生成提示詞**：
        - **ComfyUI**: `black lace dress`
"""


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    monkeypatch.setattr(main.fidelity_service, "project_manager", manager)
    return TestClient(main.app)


def _create_project(client: TestClient, name: str = "SuggestionProj") -> str:
    resp = client.post("/api/v1/projects", json={"name": name, "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


def _create_character_sheet(client: TestClient, project_id: str, tmp_path: Path, *, with_source: bool = True) -> str:
    payload: dict[str, object] = {"name": "Test Hana"}
    if with_source:
        source_dir = tmp_path / "character-source"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "setting.md").write_text(SETTING_MD, encoding="utf-8")
        (source_dir / "outfits.md").write_text(OUTFITS_MD, encoding="utf-8")
        payload["sheet_source_path"] = str(source_dir)
    resp = client.post(f"/api/v1/projects/{project_id}/characters", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["character"]["id"]


def _import_root_asset(client: TestClient, project_id: str) -> str:
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("root.png", io.BytesIO(_flat_png()), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "Root"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["assets"][-1]["id"]


class TestFidelitySuggestionCardEmission:
    def test_clarify_response_has_no_card_without_asset_or_sheet(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.post(
            f"/api/v1/projects/{project_id}/consultant/clarify",
            json={"prompt": "hello", "modality": "text"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["fidelity_suggestion_cards"] == []

    def test_clarify_response_has_no_card_when_sheet_missing_source_path(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        project_id = _create_project(client)
        _create_character_sheet(client, project_id, tmp_path, with_source=False)
        _import_root_asset(client, project_id)
        resp = client.post(
            f"/api/v1/projects/{project_id}/consultant/clarify",
            json={"prompt": "hello", "modality": "text"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["fidelity_suggestion_cards"] == []

    def test_clarify_response_has_card_when_asset_and_sheet_with_source_path_exist(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)

        resp = client.post(
            f"/api/v1/projects/{project_id}/consultant/clarify",
            json={"prompt": "hello", "modality": "text"},
        )
        assert resp.status_code == 200, resp.text
        cards = resp.json()["data"]["fidelity_suggestion_cards"]
        assert len(cards) == 1
        card = cards[0]
        assert card["action"] == "start_fidelity_loop"
        assert card["asset_id"] == asset_id
        assert card["character_sheet_id"] == sheet_id
        assert set(card["outfit_variant_choices"]) == {"default", "gothic"}
        assert card["auto_continue"] is False  # project default, untouched
        assert card["reason"]

    def test_clarify_card_auto_continue_reflects_project_setting(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        project_id = _create_project(client)
        _create_character_sheet(client, project_id, tmp_path)
        _import_root_asset(client, project_id)

        patch_resp = client.patch(
            f"/api/v1/projects/{project_id}/settings", json={"auto_loop_enabled": True}
        )
        assert patch_resp.status_code == 200, patch_resp.text

        resp = client.post(
            f"/api/v1/projects/{project_id}/consultant/clarify",
            json={"prompt": "hello", "modality": "text"},
        )
        cards = resp.json()["data"]["fidelity_suggestion_cards"]
        assert cards[0]["auto_continue"] is True

    def test_session_start_response_also_carries_the_card(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        project_id = _create_project(client)
        _create_character_sheet(client, project_id, tmp_path)
        _import_root_asset(client, project_id)

        resp = client.post(
            f"/api/v1/projects/{project_id}/consultant/session",
            json={"prompt": "hello", "modality": "text"},
        )
        assert resp.status_code == 200, resp.text
        cards = resp.json()["data"]["result"]["fidelity_suggestion_cards"]
        assert len(cards) == 1


class TestProjectSettings:
    def test_settings_default_false(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.get(f"/api/v1/projects/{project_id}")
        assert resp.json()["data"]["project"]["auto_loop_enabled"] is False

    def test_patch_settings_then_get_reflects_it(self, client: TestClient) -> None:
        project_id = _create_project(client)
        patch_resp = client.patch(
            f"/api/v1/projects/{project_id}/settings", json={"auto_loop_enabled": True}
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["data"]["project"]["auto_loop_enabled"] is True

        get_resp = client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.json()["data"]["project"]["auto_loop_enabled"] is True

    def test_patch_settings_omitted_field_leaves_value_unchanged(self, client: TestClient) -> None:
        project_id = _create_project(client)
        client.patch(f"/api/v1/projects/{project_id}/settings", json={"auto_loop_enabled": True})
        # Second PATCH with an empty body must NOT reset it back to False.
        second = client.patch(f"/api/v1/projects/{project_id}/settings", json={})
        assert second.status_code == 200, second.text
        assert second.json()["data"]["project"]["auto_loop_enabled"] is True

    def test_patch_settings_unknown_project_returns_404(self, client: TestClient) -> None:
        resp = client.patch("/api/v1/projects/no-such-project/settings", json={"auto_loop_enabled": True})
        assert resp.status_code == 404


class TestAutoContinueDefaultFromProjectSetting:
    def _install_fake_service_all_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_critique(image_bytes: bytes, checks: list, width: int, height: int) -> list[FidelityCheckResult]:
            return [FidelityCheckResult(id=check.id, passed=True, confidence=1.0, note="") for check in checks]

        service = FidelityService(
            main.project_manager,
            main.generation_service,
            main._fidelity_store,
            main._character_sheet_resolver,
            critique_fn=fake_critique,
        )
        monkeypatch.setattr(main, "fidelity_service", service)

    def test_omitted_auto_continue_uses_project_default_true(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        client.patch(f"/api/v1/projects/{project_id}/settings", json={"auto_loop_enabled": True})
        self._install_fake_service_all_pass(monkeypatch)

        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "Default"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["loop"]["auto_continue"] is True

    def test_omitted_auto_continue_uses_project_default_false(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        self._install_fake_service_all_pass(monkeypatch)

        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "Default"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["loop"]["auto_continue"] is False

    def test_explicit_auto_continue_overrides_project_default(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        client.patch(f"/api/v1/projects/{project_id}/settings", json={"auto_loop_enabled": True})
        self._install_fake_service_all_pass(monkeypatch)

        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "Default", "auto_continue": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["loop"]["auto_continue"] is False
