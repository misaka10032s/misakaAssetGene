"""Tests for §7.1.1 SQLite CRUD — AssetStore + API routes.

Coverage per entity (CharacterSheet / DatasetPack / TrainingRecipe / LoraPreset):
  - create → get → list → update → delete round-trip
  - project scoping isolation (records from project A invisible in project B)
  - 404 paths (get/update/delete on missing id)
  - persistence across store re-open (survives new AssetStore instance)

API-level tests (via TestClient):
  - POST / GET / PATCH / DELETE per entity through FastAPI routes
  - 404 on unknown project_id
  - 404 on unknown record id
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from core.models.schemas import (
    CharacterSheetCreateRequest,
    CharacterSheetUpdateRequest,
    DatasetPackCreateRequest,
    DatasetPackUpdateRequest,
    ImageToVideoRecipeCreateRequest,
    ImageToVideoRecipeUpdateRequest,
    LoraLayer,
    LoraPresetCreateRequest,
    LoraPresetUpdateRequest,
    TrainingRecipeCreateRequest,
    TrainingRecipeUpdateRequest,
)
from core.training.asset_store import AssetStore


# ---------------------------------------------------------------------------
# AssetStore unit tests
# ---------------------------------------------------------------------------

class TestCharacterSheetStore:
    def test_create_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        req = CharacterSheetCreateRequest(
            name="Kyuoka",
            visual_anchors=["blue_hair", "twin_tails"],
            trigger_words=["kyuoka_"],
            forbidden_features=["red_eyes"],
            reference_image_refs=["refs/kyuoka_v1.png"],
        )
        created = store.create_character_sheet("proj-a", req)
        assert created.name == "Kyuoka"
        assert created.project_id == "proj-a"
        assert "blue_hair" in created.visual_anchors

        fetched = store.get_character_sheet("proj-a", created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.trigger_words == ["kyuoka_"]

    def test_list(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        store.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="A"))
        store.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="B"))
        items = store.list_character_sheets("proj-a")
        assert len(items) == 2

    def test_update(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="Orig"))
        updated = store.update_character_sheet(
            "proj-a", created.id,
            CharacterSheetUpdateRequest(name="Renamed", trigger_words=["new_trigger_"]),
        )
        assert updated is not None
        assert updated.name == "Renamed"
        assert updated.trigger_words == ["new_trigger_"]
        assert updated.updated_at >= created.updated_at

    def test_delete(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="Del"))
        assert store.delete_character_sheet("proj-a", created.id) is True
        assert store.get_character_sheet("proj-a", created.id) is None

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        assert store.delete_character_sheet("proj-a", "ghost-id") is False

    def test_project_scoping_isolation(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="OnlyA"))
        # same id lookup in different project returns None
        assert store.get_character_sheet("proj-b", created.id) is None
        assert store.list_character_sheets("proj-b") == []

    def test_persistence_survives_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "memory.sqlite"
        store_a = AssetStore(db)
        created = store_a.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="Persist"))

        store_b = AssetStore(db)
        fetched = store_b.get_character_sheet("proj-a", created.id)
        assert fetched is not None
        assert fetched.name == "Persist"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        assert store.get_character_sheet("proj-a", "nonexistent") is None

    def test_update_missing_returns_none(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        result = store.update_character_sheet("proj-a", "ghost", CharacterSheetUpdateRequest(name="X"))
        assert result is None

    def test_sheet_source_path_roundtrip(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_character_sheet(
            "proj-a",
            CharacterSheetCreateRequest(name="Saiko", sheet_source_path="/chars/saiko"),
        )
        assert created.sheet_source_path == "/chars/saiko"
        fetched = store.get_character_sheet("proj-a", created.id)
        assert fetched is not None
        assert fetched.sheet_source_path == "/chars/saiko"

        updated = store.update_character_sheet(
            "proj-a", created.id,
            CharacterSheetUpdateRequest(sheet_source_path="/chars/saiko-v2"),
        )
        assert updated is not None
        assert updated.sheet_source_path == "/chars/saiko-v2"

    def test_sheet_source_path_defaults_to_none(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_character_sheet("proj-a", CharacterSheetCreateRequest(name="NoPath"))
        assert created.sheet_source_path is None

    def test_migration_loads_pre_existing_rows_without_sheet_source_path(self, tmp_path: Path) -> None:
        """Spec §7.1.1 backward-compat: a ``character_sheets`` table created
        before ``sheet_source_path`` existed must still load through the new
        AssetStore, with the new field defaulting to ``None`` for old rows.
        """
        db_path = tmp_path / "memory.sqlite"
        # Simulate a pre-migration DB: the OLD schema, no sheet_source_path column.
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE character_sheets (
                    id                  TEXT PRIMARY KEY,
                    project_id          TEXT NOT NULL,
                    name                TEXT NOT NULL,
                    visual_anchors      TEXT NOT NULL DEFAULT '[]',
                    trigger_words       TEXT NOT NULL DEFAULT '[]',
                    forbidden_features  TEXT NOT NULL DEFAULT '[]',
                    reference_image_refs TEXT NOT NULL DEFAULT '[]',
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT INTO character_sheets "
                "(id, project_id, name, visual_anchors, trigger_words, "
                " forbidden_features, reference_image_refs, created_at, updated_at) "
                "VALUES ('old-1','proj-a','Legacy','[]','[]','[]','[]',"
                "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
        finally:
            conn.close()

        # Opening with the new AssetStore must migrate in place, not crash.
        store = AssetStore(db_path)
        fetched = store.get_character_sheet("proj-a", "old-1")
        assert fetched is not None
        assert fetched.name == "Legacy"
        assert fetched.sheet_source_path is None

        # And new writes on the migrated table work normally.
        created = store.create_character_sheet(
            "proj-a", CharacterSheetCreateRequest(name="New", sheet_source_path="/chars/new")
        )
        assert created.sheet_source_path == "/chars/new"


class TestDatasetPackStore:
    def test_create_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        req = DatasetPackCreateRequest(
            source="danbooru",
            cleaning_status="cleaned",
            tags=["portrait", "character"],
            license="CC-BY",
            split_strategy="80/20",
            members=["img_001.png", "img_002.png"],
        )
        created = store.create_dataset_pack("proj-a", req)
        assert created.source == "danbooru"
        assert created.cleaning_status == "cleaned"
        fetched = store.get_dataset_pack("proj-a", created.id)
        assert fetched is not None
        assert fetched.tags == ["portrait", "character"]

    def test_list_and_update(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        req = DatasetPackCreateRequest(source="pixiv", cleaning_status="raw")
        created = store.create_dataset_pack("proj-a", req)
        assert len(store.list_dataset_packs("proj-a")) == 1

        updated = store.update_dataset_pack(
            "proj-a", created.id,
            DatasetPackUpdateRequest(cleaning_status="tagged", tags=["new_tag"]),
        )
        assert updated is not None
        assert updated.cleaning_status == "tagged"
        assert updated.tags == ["new_tag"]

    def test_delete(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_dataset_pack("proj-a", DatasetPackCreateRequest(source="s", cleaning_status="raw"))
        assert store.delete_dataset_pack("proj-a", created.id) is True
        assert store.get_dataset_pack("proj-a", created.id) is None

    def test_project_scoping(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_dataset_pack("proj-a", DatasetPackCreateRequest(source="s", cleaning_status="c"))
        assert store.get_dataset_pack("proj-b", created.id) is None

    def test_persistence_survives_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "memory.sqlite"
        store_a = AssetStore(db)
        created = store_a.create_dataset_pack("proj-a", DatasetPackCreateRequest(source="src", cleaning_status="raw"))
        store_b = AssetStore(db)
        assert store_b.get_dataset_pack("proj-a", created.id) is not None


class TestTrainingRecipeStore:
    def test_create_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        req = TrainingRecipeCreateRequest(
            base_model="sd-xl-base-1.0",
            rank=32,
            epochs=10,
            optimizer="AdamW8bit",
            caption_strategy="wd14",
        )
        created = store.create_training_recipe("proj-a", req)
        assert created.base_model == "sd-xl-base-1.0"
        assert created.rank == 32
        fetched = store.get_training_recipe("proj-a", created.id)
        assert fetched is not None
        assert fetched.epochs == 10

    def test_list_and_update(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        req = TrainingRecipeCreateRequest(base_model="m", rank=4, epochs=5, optimizer="adam", caption_strategy="blip")
        created = store.create_training_recipe("proj-a", req)
        assert len(store.list_training_recipes("proj-a")) == 1

        updated = store.update_training_recipe(
            "proj-a", created.id,
            TrainingRecipeUpdateRequest(epochs=20, rank=16),
        )
        assert updated is not None
        assert updated.epochs == 20
        assert updated.rank == 16

    def test_delete(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_training_recipe(
            "proj-a",
            TrainingRecipeCreateRequest(base_model="m", rank=4, epochs=1, optimizer="a", caption_strategy="c"),
        )
        assert store.delete_training_recipe("proj-a", created.id) is True
        assert store.get_training_recipe("proj-a", created.id) is None

    def test_project_scoping(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_training_recipe(
            "proj-a",
            TrainingRecipeCreateRequest(base_model="m", rank=4, epochs=1, optimizer="a", caption_strategy="c"),
        )
        assert store.get_training_recipe("proj-b", created.id) is None

    def test_persistence_survives_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "memory.sqlite"
        store_a = AssetStore(db)
        created = store_a.create_training_recipe(
            "proj-a",
            TrainingRecipeCreateRequest(base_model="m", rank=4, epochs=1, optimizer="a", caption_strategy="c"),
        )
        store_b = AssetStore(db)
        assert store_b.get_training_recipe("proj-a", created.id) is not None


class TestLoraPresetStore:
    def test_create_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        layers = [
            LoraLayer(kind="character", lora_ref="kyuoka_char.safetensors", weight=1.0),
            LoraLayer(kind="style", lora_ref="painterly_style.safetensors", weight=0.7),
        ]
        req = LoraPresetCreateRequest(name="Kyuoka Portrait", layers=layers)
        created = store.create_lora_preset("proj-a", req)
        assert created.name == "Kyuoka Portrait"
        assert len(created.layers) == 2

        fetched = store.get_lora_preset("proj-a", created.id)
        assert fetched is not None
        assert fetched.layers[0].kind == "character"
        assert fetched.layers[1].weight == 0.7

    def test_list_and_update(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_lora_preset("proj-a", LoraPresetCreateRequest(name="Orig"))
        assert len(store.list_lora_presets("proj-a")) == 1

        new_layers = [LoraLayer(kind="costume", lora_ref="outfit_a.safetensors", weight=0.8)]
        updated = store.update_lora_preset(
            "proj-a", created.id,
            LoraPresetUpdateRequest(name="Updated", layers=new_layers),
        )
        assert updated is not None
        assert updated.name == "Updated"
        assert updated.layers[0].kind == "costume"

    def test_delete(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_lora_preset("proj-a", LoraPresetCreateRequest(name="Del"))
        assert store.delete_lora_preset("proj-a", created.id) is True
        assert store.get_lora_preset("proj-a", created.id) is None

    def test_project_scoping(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_lora_preset("proj-a", LoraPresetCreateRequest(name="A-only"))
        assert store.get_lora_preset("proj-b", created.id) is None
        assert store.list_lora_presets("proj-b") == []

    def test_persistence_survives_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "memory.sqlite"
        store_a = AssetStore(db)
        created = store_a.create_lora_preset("proj-a", LoraPresetCreateRequest(name="Persist"))
        store_b = AssetStore(db)
        assert store_b.get_lora_preset("proj-a", created.id) is not None

    def test_empty_layers(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_lora_preset("proj-a", LoraPresetCreateRequest(name="Empty"))
        assert created.layers == []
        fetched = store.get_lora_preset("proj-a", created.id)
        assert fetched is not None
        assert fetched.layers == []


# ---------------------------------------------------------------------------
# API-level route tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with a project already set up and asset stores wired to tmp_path."""
    import core.main as main_module
    from core.project.manager import ProjectManager, ProjectCreateRequest

    # Redirect projects root to tmp_path so we don't pollute the real projects/.
    monkeypatch.setattr(main_module, "PROJECTS_ROOT", tmp_path / "projects")
    pm = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main_module, "project_manager", pm)
    monkeypatch.setattr(main_module, "_asset_stores", {})

    # Wire other services that depend on project_manager.
    from core.generation.service import GenerationService
    from core.training.service import TrainingService
    monkeypatch.setattr(main_module, "generation_service", GenerationService(pm, main_module.workers_service))
    monkeypatch.setattr(main_module, "training_service", TrainingService(pm))

    # Create one test project.
    from core.project.manager import ProjectCreateRequest as PCR
    pm.create_project(PCR(name="Test Project", type="RPG", synopsis="test"))
    projects = pm.list_projects()
    assert projects, "project list must not be empty after creation"

    return TestClient(main_module.app)


def _project_id(client: TestClient) -> str:
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    projects = resp.json()["data"]["projects"]
    return projects[0]["id"]


class TestCharacterSheetAPI:
    def test_list_empty(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.get(f"/api/v1/projects/{pid}/characters")
        assert resp.status_code == 200
        assert resp.json()["data"]["characters"] == []

    def test_create_get_list(self, client: TestClient) -> None:
        pid = _project_id(client)
        body = {"name": "Kyuoka", "trigger_words": ["kyuoka_"], "visual_anchors": ["blue_hair"]}
        resp = client.post(f"/api/v1/projects/{pid}/characters", json=body)
        assert resp.status_code == 200
        character_id = resp.json()["data"]["character"]["id"]

        resp2 = client.get(f"/api/v1/projects/{pid}/characters/{character_id}")
        assert resp2.status_code == 200
        assert resp2.json()["data"]["character"]["name"] == "Kyuoka"

        resp3 = client.get(f"/api/v1/projects/{pid}/characters")
        assert len(resp3.json()["data"]["characters"]) == 1

    def test_update(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(f"/api/v1/projects/{pid}/characters", json={"name": "Orig"})
        cid = resp.json()["data"]["character"]["id"]
        patch = client.patch(f"/api/v1/projects/{pid}/characters/{cid}", json={"name": "Renamed"})
        assert patch.status_code == 200
        assert patch.json()["data"]["character"]["name"] == "Renamed"

    def test_delete(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(f"/api/v1/projects/{pid}/characters", json={"name": "ToDelete"})
        cid = resp.json()["data"]["character"]["id"]
        del_resp = client.delete(f"/api/v1/projects/{pid}/characters/{cid}")
        assert del_resp.status_code == 200
        assert client.get(f"/api/v1/projects/{pid}/characters/{cid}").status_code == 404

    def test_404_unknown_project(self, client: TestClient) -> None:
        resp = client.get("/api/v1/projects/ghost-project/characters")
        assert resp.status_code == 404

    def test_404_unknown_character(self, client: TestClient) -> None:
        pid = _project_id(client)
        assert client.get(f"/api/v1/projects/{pid}/characters/no-such-id").status_code == 404
        assert client.patch(f"/api/v1/projects/{pid}/characters/no-such-id", json={}).status_code == 404
        assert client.delete(f"/api/v1/projects/{pid}/characters/no-such-id").status_code == 404


class TestDatasetPackAPI:
    def test_create_list_delete(self, client: TestClient) -> None:
        pid = _project_id(client)
        body = {"source": "danbooru", "cleaning_status": "raw", "tags": ["portrait"]}
        resp = client.post(f"/api/v1/projects/{pid}/dataset-packs", json=body)
        assert resp.status_code == 200
        pack_id = resp.json()["data"]["dataset_pack"]["id"]

        assert len(client.get(f"/api/v1/projects/{pid}/dataset-packs").json()["data"]["dataset_packs"]) == 1
        assert client.delete(f"/api/v1/projects/{pid}/dataset-packs/{pack_id}").status_code == 200
        assert len(client.get(f"/api/v1/projects/{pid}/dataset-packs").json()["data"]["dataset_packs"]) == 0

    def test_update(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(f"/api/v1/projects/{pid}/dataset-packs", json={"source": "s", "cleaning_status": "raw"})
        pack_id = resp.json()["data"]["dataset_pack"]["id"]
        patched = client.patch(f"/api/v1/projects/{pid}/dataset-packs/{pack_id}", json={"cleaning_status": "cleaned"})
        assert patched.json()["data"]["dataset_pack"]["cleaning_status"] == "cleaned"

    def test_404_paths(self, client: TestClient) -> None:
        pid = _project_id(client)
        assert client.get(f"/api/v1/projects/{pid}/dataset-packs/missing").status_code == 404
        assert client.get("/api/v1/projects/ghost/dataset-packs").status_code == 404


class TestTrainingRecipeAPI:
    def test_create_list_delete(self, client: TestClient) -> None:
        pid = _project_id(client)
        body = {"base_model": "sd-xl", "rank": 32, "epochs": 10, "optimizer": "AdamW8bit", "caption_strategy": "wd14"}
        resp = client.post(f"/api/v1/projects/{pid}/training-recipes", json=body)
        assert resp.status_code == 200
        recipe_id = resp.json()["data"]["training_recipe"]["id"]

        assert len(client.get(f"/api/v1/projects/{pid}/training-recipes").json()["data"]["training_recipes"]) == 1
        assert client.delete(f"/api/v1/projects/{pid}/training-recipes/{recipe_id}").status_code == 200

    def test_update(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(
            f"/api/v1/projects/{pid}/training-recipes",
            json={"base_model": "m", "rank": 4, "epochs": 5, "optimizer": "adam", "caption_strategy": "blip"},
        )
        rid = resp.json()["data"]["training_recipe"]["id"]
        patched = client.patch(f"/api/v1/projects/{pid}/training-recipes/{rid}", json={"epochs": 20})
        assert patched.json()["data"]["training_recipe"]["epochs"] == 20

    def test_404_paths(self, client: TestClient) -> None:
        pid = _project_id(client)
        assert client.get(f"/api/v1/projects/{pid}/training-recipes/missing").status_code == 404
        assert client.get("/api/v1/projects/ghost/training-recipes").status_code == 404


class TestLoraPresetAPI:
    def test_create_list_delete(self, client: TestClient) -> None:
        pid = _project_id(client)
        body = {
            "name": "Kyuoka Portrait",
            "layers": [
                {"kind": "character", "lora_ref": "kyuoka.safetensors", "weight": 1.0},
                {"kind": "style", "lora_ref": "painterly.safetensors", "weight": 0.7},
            ],
        }
        resp = client.post(f"/api/v1/projects/{pid}/lora-presets", json=body)
        assert resp.status_code == 200
        preset_id = resp.json()["data"]["lora_preset"]["id"]
        assert len(resp.json()["data"]["lora_preset"]["layers"]) == 2

        assert len(client.get(f"/api/v1/projects/{pid}/lora-presets").json()["data"]["lora_presets"]) == 1
        assert client.delete(f"/api/v1/projects/{pid}/lora-presets/{preset_id}").status_code == 200

    def test_update(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(f"/api/v1/projects/{pid}/lora-presets", json={"name": "Orig", "layers": []})
        preset_id = resp.json()["data"]["lora_preset"]["id"]
        patched = client.patch(
            f"/api/v1/projects/{pid}/lora-presets/{preset_id}",
            json={"name": "Updated", "layers": [{"kind": "costume", "lora_ref": "dress.safetensors", "weight": 0.8}]},
        )
        assert patched.json()["data"]["lora_preset"]["name"] == "Updated"
        assert patched.json()["data"]["lora_preset"]["layers"][0]["kind"] == "costume"

    def test_404_paths(self, client: TestClient) -> None:
        pid = _project_id(client)
        assert client.get(f"/api/v1/projects/{pid}/lora-presets/missing").status_code == 404
        assert client.get("/api/v1/projects/ghost/lora-presets").status_code == 404


# ---------------------------------------------------------------------------
# AssetStore unit tests — ImageToVideoRecipe
# ---------------------------------------------------------------------------

class TestImageToVideoRecipeStore:
    def test_create_and_get_roundtrip(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        req = ImageToVideoRecipeCreateRequest(
            name="AnimateDiff Portrait Loop",
            workflow_kind="animatediff",
            frames=24,
            fps=8,
            motion_strength=0.85,
            notes="Preferred for character closeups",
        )
        created = store.create_i2v_recipe("proj-a", req)
        assert created.name == "AnimateDiff Portrait Loop"
        assert created.workflow_kind == "animatediff"
        assert created.frames == 24
        assert created.fps == 8
        assert abs(created.motion_strength - 0.85) < 1e-9
        assert created.notes == "Preferred for character closeups"
        assert created.project_id == "proj-a"

        fetched = store.get_i2v_recipe("proj-a", created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.workflow_kind == "animatediff"

    def test_list(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        store.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="A", workflow_kind="animatediff", frames=16, fps=8, motion_strength=0.7),
        )
        store.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="B", workflow_kind="svd", frames=25, fps=25, motion_strength=0.5),
        )
        items = store.list_i2v_recipes("proj-a")
        assert len(items) == 2

    def test_update(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="Orig", workflow_kind="animatediff", frames=16, fps=8, motion_strength=0.7),
        )
        updated = store.update_i2v_recipe(
            "proj-a", created.id,
            ImageToVideoRecipeUpdateRequest(name="Renamed", frames=32, fps=12, motion_strength=0.9),
        )
        assert updated is not None
        assert updated.name == "Renamed"
        assert updated.frames == 32
        assert updated.fps == 12
        assert abs(updated.motion_strength - 0.9) < 1e-9
        assert updated.workflow_kind == "animatediff"  # unchanged
        assert updated.updated_at >= created.updated_at

    def test_delete(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="Del", workflow_kind="svd", frames=25, fps=25, motion_strength=0.5),
        )
        assert store.delete_i2v_recipe("proj-a", created.id) is True
        assert store.get_i2v_recipe("proj-a", created.id) is None

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        assert store.delete_i2v_recipe("proj-a", "ghost-id") is False

    def test_project_scoping_isolation(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="OnlyA", workflow_kind="animatediff", frames=16, fps=8, motion_strength=0.7),
        )
        assert store.get_i2v_recipe("proj-b", created.id) is None
        assert store.list_i2v_recipes("proj-b") == []

    def test_persistence_survives_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "memory.sqlite"
        store_a = AssetStore(db)
        created = store_a.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="Persist", workflow_kind="animatediff", frames=16, fps=8, motion_strength=0.7),
        )
        store_b = AssetStore(db)
        fetched = store_b.get_i2v_recipe("proj-a", created.id)
        assert fetched is not None
        assert fetched.name == "Persist"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        assert store.get_i2v_recipe("proj-a", "nonexistent") is None

    def test_update_missing_returns_none(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        result = store.update_i2v_recipe("proj-a", "ghost", ImageToVideoRecipeUpdateRequest(name="X"))
        assert result is None

    def test_optional_notes_defaults_to_empty(self, tmp_path: Path) -> None:
        store = AssetStore(tmp_path / "memory.sqlite")
        created = store.create_i2v_recipe(
            "proj-a",
            ImageToVideoRecipeCreateRequest(name="No Notes", workflow_kind="animatediff", frames=16, fps=8, motion_strength=0.7),
        )
        assert created.notes == ""
        fetched = store.get_i2v_recipe("proj-a", created.id)
        assert fetched is not None
        assert fetched.notes == ""


# ---------------------------------------------------------------------------
# API-level route tests — ImageToVideoRecipe
# ---------------------------------------------------------------------------

class TestImageToVideoRecipeAPI:
    def test_list_empty(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.get(f"/api/v1/projects/{pid}/i2v-recipes")
        assert resp.status_code == 200
        assert resp.json()["data"]["i2v_recipes"] == []

    def test_create_get_list(self, client: TestClient) -> None:
        pid = _project_id(client)
        body = {
            "name": "AnimateDiff Portrait",
            "workflow_kind": "animatediff",
            "frames": 24,
            "fps": 8,
            "motion_strength": 0.85,
            "notes": "test note",
        }
        resp = client.post(f"/api/v1/projects/{pid}/i2v-recipes", json=body)
        assert resp.status_code == 200
        recipe_id = resp.json()["data"]["i2v_recipe"]["id"]

        resp2 = client.get(f"/api/v1/projects/{pid}/i2v-recipes/{recipe_id}")
        assert resp2.status_code == 200
        data = resp2.json()["data"]["i2v_recipe"]
        assert data["name"] == "AnimateDiff Portrait"
        assert data["workflow_kind"] == "animatediff"
        assert data["frames"] == 24

        resp3 = client.get(f"/api/v1/projects/{pid}/i2v-recipes")
        assert len(resp3.json()["data"]["i2v_recipes"]) == 1

    def test_update(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(
            f"/api/v1/projects/{pid}/i2v-recipes",
            json={"name": "Orig", "workflow_kind": "animatediff", "frames": 16, "fps": 8, "motion_strength": 0.7},
        )
        rid = resp.json()["data"]["i2v_recipe"]["id"]
        patch = client.patch(
            f"/api/v1/projects/{pid}/i2v-recipes/{rid}",
            json={"name": "Renamed", "frames": 32},
        )
        assert patch.status_code == 200
        assert patch.json()["data"]["i2v_recipe"]["name"] == "Renamed"
        assert patch.json()["data"]["i2v_recipe"]["frames"] == 32

    def test_delete(self, client: TestClient) -> None:
        pid = _project_id(client)
        resp = client.post(
            f"/api/v1/projects/{pid}/i2v-recipes",
            json={"name": "ToDelete", "workflow_kind": "svd", "frames": 25, "fps": 25, "motion_strength": 0.5},
        )
        rid = resp.json()["data"]["i2v_recipe"]["id"]
        del_resp = client.delete(f"/api/v1/projects/{pid}/i2v-recipes/{rid}")
        assert del_resp.status_code == 200
        assert client.get(f"/api/v1/projects/{pid}/i2v-recipes/{rid}").status_code == 404

    def test_404_unknown_project(self, client: TestClient) -> None:
        resp = client.get("/api/v1/projects/ghost-project/i2v-recipes")
        assert resp.status_code == 404

    def test_404_unknown_recipe(self, client: TestClient) -> None:
        pid = _project_id(client)
        assert client.get(f"/api/v1/projects/{pid}/i2v-recipes/no-such-id").status_code == 404
        assert client.patch(f"/api/v1/projects/{pid}/i2v-recipes/no-such-id", json={}).status_code == 404
        assert client.delete(f"/api/v1/projects/{pid}/i2v-recipes/no-such-id").status_code == 404
