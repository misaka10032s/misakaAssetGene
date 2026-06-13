"""SQLite-backed CRUD store for §7.1.1 training-asset entities.

Covers five entities:
  - CharacterSheet      (table: character_sheets)
  - DatasetPack         (table: dataset_packs)
  - TrainingRecipe      (table: training_recipes)
  - LoraPreset          (table: lora_presets)
  - ImageToVideoRecipe  (table: i2v_recipes)

All tables share the same project-scoped design and timestamp conventions
used by the consultant SessionStore.  The store opens (or creates) a single
``memory.sqlite`` file and initialises all five tables on first access.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.models.schemas import (
    CharacterSheet,
    CharacterSheetCreateRequest,
    CharacterSheetUpdateRequest,
    DatasetPack,
    DatasetPackCreateRequest,
    DatasetPackUpdateRequest,
    ImageToVideoRecipe,
    ImageToVideoRecipeCreateRequest,
    ImageToVideoRecipeUpdateRequest,
    LoraLayer,
    LoraPreset,
    LoraPresetCreateRequest,
    LoraPresetUpdateRequest,
    TrainingRecipe,
    TrainingRecipeCreateRequest,
    TrainingRecipeUpdateRequest,
)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS character_sheets (
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
CREATE INDEX IF NOT EXISTS idx_cs_project ON character_sheets(project_id);

CREATE TABLE IF NOT EXISTS dataset_packs (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    source           TEXT NOT NULL,
    cleaning_status  TEXT NOT NULL,
    tags             TEXT NOT NULL DEFAULT '[]',
    license          TEXT NOT NULL DEFAULT '',
    split_strategy   TEXT NOT NULL DEFAULT '',
    members          TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dp_project ON dataset_packs(project_id);

CREATE TABLE IF NOT EXISTS training_recipes (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    base_model       TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    epochs           INTEGER NOT NULL,
    optimizer        TEXT NOT NULL,
    caption_strategy TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tr_project ON training_recipes(project_id);

CREATE TABLE IF NOT EXISTS lora_presets (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name       TEXT NOT NULL,
    layers     TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lp_project ON lora_presets(project_id);

CREATE TABLE IF NOT EXISTS i2v_recipes (
    id               TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    name             TEXT NOT NULL,
    workflow_kind    TEXT NOT NULL,
    frames           INTEGER NOT NULL,
    fps              INTEGER NOT NULL,
    motion_strength  REAL NOT NULL,
    notes            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_i2v_project ON i2v_recipes(project_id);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# AssetStore
# ---------------------------------------------------------------------------

class AssetStore:
    """Repository for all five §7.1.1 entities, backed by ``memory.sqlite``."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # CharacterSheet CRUD
    # ------------------------------------------------------------------

    def create_character_sheet(
        self, project_id: str, req: CharacterSheetCreateRequest
    ) -> CharacterSheet:
        now = _now()
        record = CharacterSheet(
            id=_new_id(),
            project_id=project_id,
            name=req.name.strip(),
            visual_anchors=req.visual_anchors,
            trigger_words=req.trigger_words,
            forbidden_features=req.forbidden_features,
            reference_image_refs=req.reference_image_refs,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO character_sheets "
                "(id, project_id, name, visual_anchors, trigger_words, "
                " forbidden_features, reference_image_refs, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record.id, record.project_id, record.name,
                    json.dumps(record.visual_anchors, ensure_ascii=False),
                    json.dumps(record.trigger_words, ensure_ascii=False),
                    json.dumps(record.forbidden_features, ensure_ascii=False),
                    json.dumps(record.reference_image_refs, ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_character_sheet(self, project_id: str, record_id: str) -> CharacterSheet | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM character_sheets WHERE id=? AND project_id=?",
                (record_id, project_id),
            ).fetchone()
        return self._row_to_character_sheet(row) if row else None

    def list_character_sheets(self, project_id: str) -> list[CharacterSheet]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM character_sheets WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self._row_to_character_sheet(r) for r in rows]

    def update_character_sheet(
        self, project_id: str, record_id: str, req: CharacterSheetUpdateRequest
    ) -> CharacterSheet | None:
        existing = self.get_character_sheet(project_id, record_id)
        if existing is None:
            return None
        now = _now()
        updated = CharacterSheet(
            id=existing.id,
            project_id=existing.project_id,
            name=req.name.strip() if req.name is not None else existing.name,
            visual_anchors=req.visual_anchors if req.visual_anchors is not None else existing.visual_anchors,
            trigger_words=req.trigger_words if req.trigger_words is not None else existing.trigger_words,
            forbidden_features=req.forbidden_features if req.forbidden_features is not None else existing.forbidden_features,
            reference_image_refs=req.reference_image_refs if req.reference_image_refs is not None else existing.reference_image_refs,
            created_at=existing.created_at,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE character_sheets SET name=?, visual_anchors=?, trigger_words=?, "
                "forbidden_features=?, reference_image_refs=?, updated_at=? WHERE id=? AND project_id=?",
                (
                    updated.name,
                    json.dumps(updated.visual_anchors, ensure_ascii=False),
                    json.dumps(updated.trigger_words, ensure_ascii=False),
                    json.dumps(updated.forbidden_features, ensure_ascii=False),
                    json.dumps(updated.reference_image_refs, ensure_ascii=False),
                    updated.updated_at.isoformat(),
                    updated.id, updated.project_id,
                ),
            )
        return updated

    def delete_character_sheet(self, project_id: str, record_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM character_sheets WHERE id=? AND project_id=?",
                (record_id, project_id),
            )
        return cursor.rowcount > 0

    def _row_to_character_sheet(self, row: sqlite3.Row) -> CharacterSheet:
        return CharacterSheet(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            visual_anchors=json.loads(row["visual_anchors"] or "[]"),
            trigger_words=json.loads(row["trigger_words"] or "[]"),
            forbidden_features=json.loads(row["forbidden_features"] or "[]"),
            reference_image_refs=json.loads(row["reference_image_refs"] or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # DatasetPack CRUD
    # ------------------------------------------------------------------

    def create_dataset_pack(
        self, project_id: str, req: DatasetPackCreateRequest
    ) -> DatasetPack:
        now = _now()
        record = DatasetPack(
            id=_new_id(),
            project_id=project_id,
            source=req.source.strip(),
            cleaning_status=req.cleaning_status.strip(),
            tags=req.tags,
            license=req.license,
            split_strategy=req.split_strategy,
            members=req.members,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO dataset_packs "
                "(id, project_id, source, cleaning_status, tags, license, split_strategy, members, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id, record.project_id, record.source, record.cleaning_status,
                    json.dumps(record.tags, ensure_ascii=False),
                    record.license, record.split_strategy,
                    json.dumps(record.members, ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_dataset_pack(self, project_id: str, record_id: str) -> DatasetPack | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM dataset_packs WHERE id=? AND project_id=?",
                (record_id, project_id),
            ).fetchone()
        return self._row_to_dataset_pack(row) if row else None

    def list_dataset_packs(self, project_id: str) -> list[DatasetPack]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dataset_packs WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self._row_to_dataset_pack(r) for r in rows]

    def update_dataset_pack(
        self, project_id: str, record_id: str, req: DatasetPackUpdateRequest
    ) -> DatasetPack | None:
        existing = self.get_dataset_pack(project_id, record_id)
        if existing is None:
            return None
        now = _now()
        updated = DatasetPack(
            id=existing.id,
            project_id=existing.project_id,
            source=req.source.strip() if req.source is not None else existing.source,
            cleaning_status=req.cleaning_status.strip() if req.cleaning_status is not None else existing.cleaning_status,
            tags=req.tags if req.tags is not None else existing.tags,
            license=req.license if req.license is not None else existing.license,
            split_strategy=req.split_strategy if req.split_strategy is not None else existing.split_strategy,
            members=req.members if req.members is not None else existing.members,
            created_at=existing.created_at,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE dataset_packs SET source=?, cleaning_status=?, tags=?, license=?, "
                "split_strategy=?, members=?, updated_at=? WHERE id=? AND project_id=?",
                (
                    updated.source, updated.cleaning_status,
                    json.dumps(updated.tags, ensure_ascii=False),
                    updated.license, updated.split_strategy,
                    json.dumps(updated.members, ensure_ascii=False),
                    updated.updated_at.isoformat(),
                    updated.id, updated.project_id,
                ),
            )
        return updated

    def delete_dataset_pack(self, project_id: str, record_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM dataset_packs WHERE id=? AND project_id=?",
                (record_id, project_id),
            )
        return cursor.rowcount > 0

    def _row_to_dataset_pack(self, row: sqlite3.Row) -> DatasetPack:
        return DatasetPack(
            id=row["id"],
            project_id=row["project_id"],
            source=row["source"],
            cleaning_status=row["cleaning_status"],
            tags=json.loads(row["tags"] or "[]"),
            license=row["license"] or "",
            split_strategy=row["split_strategy"] or "",
            members=json.loads(row["members"] or "[]"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # TrainingRecipe CRUD
    # ------------------------------------------------------------------

    def create_training_recipe(
        self, project_id: str, req: TrainingRecipeCreateRequest
    ) -> TrainingRecipe:
        now = _now()
        record = TrainingRecipe(
            id=_new_id(),
            project_id=project_id,
            base_model=req.base_model.strip(),
            rank=req.rank,
            epochs=req.epochs,
            optimizer=req.optimizer.strip(),
            caption_strategy=req.caption_strategy.strip(),
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO training_recipes "
                "(id, project_id, base_model, rank, epochs, optimizer, caption_strategy, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record.id, record.project_id, record.base_model,
                    record.rank, record.epochs, record.optimizer, record.caption_strategy,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_training_recipe(self, project_id: str, record_id: str) -> TrainingRecipe | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM training_recipes WHERE id=? AND project_id=?",
                (record_id, project_id),
            ).fetchone()
        return self._row_to_training_recipe(row) if row else None

    def list_training_recipes(self, project_id: str) -> list[TrainingRecipe]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM training_recipes WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self._row_to_training_recipe(r) for r in rows]

    def update_training_recipe(
        self, project_id: str, record_id: str, req: TrainingRecipeUpdateRequest
    ) -> TrainingRecipe | None:
        existing = self.get_training_recipe(project_id, record_id)
        if existing is None:
            return None
        now = _now()
        updated = TrainingRecipe(
            id=existing.id,
            project_id=existing.project_id,
            base_model=req.base_model.strip() if req.base_model is not None else existing.base_model,
            rank=req.rank if req.rank is not None else existing.rank,
            epochs=req.epochs if req.epochs is not None else existing.epochs,
            optimizer=req.optimizer.strip() if req.optimizer is not None else existing.optimizer,
            caption_strategy=req.caption_strategy.strip() if req.caption_strategy is not None else existing.caption_strategy,
            created_at=existing.created_at,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE training_recipes SET base_model=?, rank=?, epochs=?, optimizer=?, "
                "caption_strategy=?, updated_at=? WHERE id=? AND project_id=?",
                (
                    updated.base_model, updated.rank, updated.epochs,
                    updated.optimizer, updated.caption_strategy,
                    updated.updated_at.isoformat(),
                    updated.id, updated.project_id,
                ),
            )
        return updated

    def delete_training_recipe(self, project_id: str, record_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM training_recipes WHERE id=? AND project_id=?",
                (record_id, project_id),
            )
        return cursor.rowcount > 0

    def _row_to_training_recipe(self, row: sqlite3.Row) -> TrainingRecipe:
        return TrainingRecipe(
            id=row["id"],
            project_id=row["project_id"],
            base_model=row["base_model"],
            rank=row["rank"],
            epochs=row["epochs"],
            optimizer=row["optimizer"],
            caption_strategy=row["caption_strategy"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # LoraPreset CRUD
    # ------------------------------------------------------------------

    def create_lora_preset(
        self, project_id: str, req: LoraPresetCreateRequest
    ) -> LoraPreset:
        now = _now()
        record = LoraPreset(
            id=_new_id(),
            project_id=project_id,
            name=req.name.strip(),
            layers=req.layers,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO lora_presets (id, project_id, name, layers, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    record.id, record.project_id, record.name,
                    json.dumps([layer.model_dump() for layer in record.layers], ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_lora_preset(self, project_id: str, record_id: str) -> LoraPreset | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lora_presets WHERE id=? AND project_id=?",
                (record_id, project_id),
            ).fetchone()
        return self._row_to_lora_preset(row) if row else None

    def list_lora_presets(self, project_id: str) -> list[LoraPreset]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lora_presets WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self._row_to_lora_preset(r) for r in rows]

    def update_lora_preset(
        self, project_id: str, record_id: str, req: LoraPresetUpdateRequest
    ) -> LoraPreset | None:
        existing = self.get_lora_preset(project_id, record_id)
        if existing is None:
            return None
        now = _now()
        updated = LoraPreset(
            id=existing.id,
            project_id=existing.project_id,
            name=req.name.strip() if req.name is not None else existing.name,
            layers=req.layers if req.layers is not None else existing.layers,
            created_at=existing.created_at,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE lora_presets SET name=?, layers=?, updated_at=? WHERE id=? AND project_id=?",
                (
                    updated.name,
                    json.dumps([layer.model_dump() for layer in updated.layers], ensure_ascii=False),
                    updated.updated_at.isoformat(),
                    updated.id, updated.project_id,
                ),
            )
        return updated

    def delete_lora_preset(self, project_id: str, record_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM lora_presets WHERE id=? AND project_id=?",
                (record_id, project_id),
            )
        return cursor.rowcount > 0

    def _row_to_lora_preset(self, row: sqlite3.Row) -> LoraPreset:
        raw_layers = json.loads(row["layers"] or "[]")
        layers = [LoraLayer(**item) for item in raw_layers]
        return LoraPreset(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            layers=layers,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ------------------------------------------------------------------
    # ImageToVideoRecipe CRUD
    # ------------------------------------------------------------------

    def create_i2v_recipe(
        self, project_id: str, req: ImageToVideoRecipeCreateRequest
    ) -> ImageToVideoRecipe:
        now = _now()
        record = ImageToVideoRecipe(
            id=_new_id(),
            project_id=project_id,
            name=req.name.strip(),
            workflow_kind=req.workflow_kind.strip(),
            frames=req.frames,
            fps=req.fps,
            motion_strength=req.motion_strength,
            notes=req.notes,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO i2v_recipes "
                "(id, project_id, name, workflow_kind, frames, fps, motion_strength, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id, record.project_id, record.name, record.workflow_kind,
                    record.frames, record.fps, record.motion_strength, record.notes,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def get_i2v_recipe(self, project_id: str, record_id: str) -> ImageToVideoRecipe | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM i2v_recipes WHERE id=? AND project_id=?",
                (record_id, project_id),
            ).fetchone()
        return self._row_to_i2v_recipe(row) if row else None

    def list_i2v_recipes(self, project_id: str) -> list[ImageToVideoRecipe]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM i2v_recipes WHERE project_id=? ORDER BY created_at",
                (project_id,),
            ).fetchall()
        return [self._row_to_i2v_recipe(r) for r in rows]

    def update_i2v_recipe(
        self, project_id: str, record_id: str, req: ImageToVideoRecipeUpdateRequest
    ) -> ImageToVideoRecipe | None:
        existing = self.get_i2v_recipe(project_id, record_id)
        if existing is None:
            return None
        now = _now()
        updated = ImageToVideoRecipe(
            id=existing.id,
            project_id=existing.project_id,
            name=req.name.strip() if req.name is not None else existing.name,
            workflow_kind=req.workflow_kind.strip() if req.workflow_kind is not None else existing.workflow_kind,
            frames=req.frames if req.frames is not None else existing.frames,
            fps=req.fps if req.fps is not None else existing.fps,
            motion_strength=req.motion_strength if req.motion_strength is not None else existing.motion_strength,
            notes=req.notes if req.notes is not None else existing.notes,
            created_at=existing.created_at,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE i2v_recipes SET name=?, workflow_kind=?, frames=?, fps=?, "
                "motion_strength=?, notes=?, updated_at=? WHERE id=? AND project_id=?",
                (
                    updated.name, updated.workflow_kind, updated.frames, updated.fps,
                    updated.motion_strength, updated.notes,
                    updated.updated_at.isoformat(),
                    updated.id, updated.project_id,
                ),
            )
        return updated

    def delete_i2v_recipe(self, project_id: str, record_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM i2v_recipes WHERE id=? AND project_id=?",
                (record_id, project_id),
            )
        return cursor.rowcount > 0

    def _row_to_i2v_recipe(self, row: sqlite3.Row) -> ImageToVideoRecipe:
        return ImageToVideoRecipe(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            workflow_kind=row["workflow_kind"],
            frames=row["frames"],
            fps=row["fps"],
            motion_strength=row["motion_strength"],
            notes=row["notes"] or "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
