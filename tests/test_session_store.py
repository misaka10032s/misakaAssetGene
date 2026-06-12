"""Persistence tests for the SQLite consultant session store (spec §4.1.1)."""

from __future__ import annotations

from pathlib import Path

from core.consultant.session_store import SessionStore
from core.models.schemas import ConsultantState, Modality


def test_create_and_get_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "memory.sqlite")
    session = store.create(session_id="s1", project_id="proj", modality=Modality.MUSIC)
    assert session.state is ConsultantState.INTAKE
    loaded = store.get("s1")
    assert loaded is not None
    assert loaded.project_id == "proj"
    assert loaded.modality is Modality.MUSIC


def test_save_persists_state_and_slots(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "memory.sqlite")
    session = store.create(session_id="s2", project_id="proj", modality=Modality.IMAGE)
    session.state = ConsultantState.CLARIFY
    session.slots = {"usage": "portrait", "resolution": "1024"}
    session.checklist_status = {"usage": True, "resolution": True}
    store.save(session)

    loaded = store.get("s2")
    assert loaded is not None
    assert loaded.state is ConsultantState.CLARIFY
    assert loaded.slots["usage"] == "portrait"
    assert loaded.checklist_status["usage"] is True


def test_persistence_survives_new_store_instance(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite"
    store_a = SessionStore(db_path)
    session = store_a.create(session_id="s3", project_id="proj", modality=Modality.VOICE)
    session.state = ConsultantState.SUMMARY
    session.slots = {"persona": "young hero"}
    store_a.save(session)

    # Simulate an app restart: a brand new store over the same file.
    store_b = SessionStore(db_path)
    resumed = store_b.get("s3")
    assert resumed is not None
    assert resumed.state is ConsultantState.SUMMARY
    assert resumed.slots["persona"] == "young hero"


def test_latest_unfinished_excludes_accepted(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "memory.sqlite")
    done = store.create(session_id="done", project_id="proj", modality=Modality.IMAGE)
    done.state = ConsultantState.ACCEPT
    store.save(done)
    active = store.create(session_id="active", project_id="proj", modality=Modality.IMAGE)
    active.state = ConsultantState.CLARIFY
    store.save(active)

    unfinished = store.latest_unfinished("proj")
    assert unfinished is not None
    assert unfinished.session_id == "active"
