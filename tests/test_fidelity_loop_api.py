"""API-level tests for the fidelity refine-loop routes (spec §5.15 / C-spec.md §5).

Every critic/mask/refine call is FAKED at the ``FidelityService`` injection
points (``critique_fn`` / ``mask_builder_fn`` / ``refine_fn``) — no Ollama,
no ComfyUI. The fake mask/refine callables still create REAL asset files via
``GenerationService.import_asset`` so the subsequent re-critique step's file
read is exercised against real bytes, matching the shape a real refine
round would leave behind.
"""

from __future__ import annotations

import io
import struct
import threading
import zlib
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import core.main as main
from core.consultant.fidelity_service import FidelityLoopConflictError, FidelityService
from core.models.schemas import (
    FidelityCheckResult,
    FidelityLoopData,
    FidelityLoopStartRequest,
    FidelityLoopStatus,
    Modality,
)
from core.project.manager import ProjectManager

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _flat_png(width: int = 64, height: int = 64, rgb: tuple[int, int, int] = (100, 150, 200)) -> bytes:
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
1. **[TestA] 測試服裝**:
    - **身體服裝**：白色連身裙。
    - **生成提示詞**：
        - **ComfyUI**: `white dress, blue ribbon`
"""


def _result(id: str, passed: bool, confidence: float = 0.9, bbox: tuple[int, int, int, int] | None = None) -> FidelityCheckResult:
    return FidelityCheckResult(id=id, passed=passed, confidence=confidence, region_bbox=bbox, note="")


class _FakeIO:
    """Injectable critique/mask/refine fakes. ``critic_sequence`` is consumed
    one entry per ``critique_fn`` call (round 0's baseline call, then one
    more per subsequent refine round's post-refine re-critique)."""

    def __init__(self, project_id: str, critic_sequence: list[list[FidelityCheckResult]]) -> None:
        self.project_id = project_id
        self._critic_sequence = list(critic_sequence)
        self.mask_calls: list[tuple[str, object]] = []
        self.refine_calls: list[tuple[str, object]] = []

    def critique(self, image_bytes: bytes, checks: list, width: int, height: int) -> list[FidelityCheckResult]:
        if not self._critic_sequence:
            raise AssertionError("critique_fn called more times than scripted")
        return self._critic_sequence.pop(0)

    def build_mask(self, project_id: str, target_asset_id: str, plan) -> str:
        self.mask_calls.append((target_asset_id, plan))
        workspace = main.generation_service.import_asset(
            project_id,
            filename=f"fake-mask-{len(self.mask_calls)}.png",
            content=_flat_png(),
            modality=Modality.IMAGE,
            asset_type="mask",
            title="fake-mask",
        )
        return workspace.assets[-1].id

    def refine(self, project_id: str, target_asset_id: str, refine_request) -> tuple[str, str]:
        self.refine_calls.append((target_asset_id, refine_request))
        workspace = main.generation_service.import_asset(
            project_id,
            filename=f"fake-refined-{len(self.refine_calls)}.png",
            content=_flat_png(),
            modality=Modality.IMAGE,
            asset_type="image",
            title="fake-refined",
        )
        return workspace.assets[-1].id, f"fake-job-{len(self.refine_calls)}"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    monkeypatch.setattr(main.fidelity_service, "project_manager", manager)
    return TestClient(main.app)


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "FidelityProj", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


def _create_character_sheet(client: TestClient, project_id: str, tmp_path: Path) -> str:
    source_dir = tmp_path / "character-source"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "setting.md").write_text(SETTING_MD, encoding="utf-8")
    (source_dir / "outfits.md").write_text(OUTFITS_MD, encoding="utf-8")
    resp = client.post(
        f"/api/v1/projects/{project_id}/characters",
        json={"name": "Test Hana", "sheet_source_path": str(source_dir)},
    )
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


def _install_fake_service(monkeypatch: pytest.MonkeyPatch, project_id: str, critic_sequence: list[list[FidelityCheckResult]]) -> _FakeIO:
    fake = _FakeIO(project_id, critic_sequence)
    service = FidelityService(
        main.project_manager,
        main.generation_service,
        main._fidelity_store,
        main._character_sheet_resolver,
        critique_fn=fake.critique,
        mask_builder_fn=fake.build_mask,
        refine_fn=fake.refine,
    )
    monkeypatch.setattr(main, "fidelity_service", service)
    return fake


class TestStartLoop:
    def test_start_reaches_awaiting_user_after_two_fails(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        _install_fake_service(
            monkeypatch,
            project_id,
            critic_sequence=[
                [
                    _result("setting-1", False, confidence=0.9, bbox=(0, 0, 20, 20)),
                    _result("outfits-1", False, confidence=0.8, bbox=(30, 30, 50, 50)),
                ]
            ],
        )

        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "TestA"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["loop"]["status"] == "awaiting_user"
        assert data["loop"]["current_round"] == 0
        assert len(data["rounds"]) == 1
        assert set(data["unresolved_check_ids"]) == {"setting-1", "outfits-1"}
        assert data["next_round_plan"] is not None
        assert set(data["next_round_plan"]["chosen_check_ids"]) <= {"setting-1", "outfits-1"}

    def test_unknown_character_sheet_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        project_id = _create_project(client)
        asset_id = _import_root_asset(client, project_id)
        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": "no-such-sheet", "outfit_variant": "TestA"},
        )
        assert resp.status_code == 400, resp.text

    def test_unknown_outfit_variant_returns_400_with_variant_list(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "NoSuchVariant"},
        )
        assert resp.status_code == 400, resp.text
        detail = str(resp.json()["detail"])
        assert "NoSuchVariant" in detail
        assert "testa" in detail


class TestAdvance:
    def test_advance_runs_one_round_and_persists_lineage(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        fake = _install_fake_service(
            monkeypatch,
            project_id,
            critic_sequence=[
                [
                    _result("setting-1", False, confidence=0.9, bbox=(0, 0, 20, 20)),
                    _result("outfits-1", False, confidence=0.8, bbox=(30, 30, 50, 50)),
                ],
                [
                    _result("setting-1", True, confidence=1.0),
                    _result("outfits-1", True, confidence=1.0),
                ],
            ],
        )

        start = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "TestA"},
        )
        loop_id = start.json()["data"]["loop"]["id"]

        advanced = client.post(f"/api/v1/projects/{project_id}/fidelity-loop/{loop_id}/advance")
        assert advanced.status_code == 200, advanced.text
        data = advanced.json()["data"]
        assert data["loop"]["status"] == "passed"
        assert data["loop"]["current_round"] == 1
        assert len(data["rounds"]) == 2
        round1 = data["rounds"][1]
        assert round1["round_index"] == 1
        assert round1["mask_asset_id"] is not None
        assert round1["refine_job_id"] == "fake-job-1"
        assert round1["asset_id"] != asset_id  # a NEW asset, not the root

        assert len(fake.mask_calls) == 1
        assert len(fake.refine_calls) == 1

    def test_advance_on_unknown_loop_returns_404(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.post(f"/api/v1/projects/{project_id}/fidelity-loop/missing/advance")
        assert resp.status_code == 404

    def test_advance_when_not_awaiting_returns_400(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        _install_fake_service(
            monkeypatch,
            project_id,
            critic_sequence=[[_result("setting-1", True), _result("outfits-1", True)]],
        )
        start = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "TestA"},
        )
        loop_id = start.json()["data"]["loop"]["id"]
        assert start.json()["data"]["loop"]["status"] == "passed"

        resp = client.post(f"/api/v1/projects/{project_id}/fidelity-loop/{loop_id}/advance")
        assert resp.status_code == 400


class TestAdvanceConcurrency:
    """C2-review.md MAJOR #1 — ``advance()``'s prior check-then-act race."""

    def test_concurrent_advance_only_one_round_runs_the_other_conflicts(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        fake = _install_fake_service(
            monkeypatch,
            project_id,
            critic_sequence=[
                [
                    _result("setting-1", False, confidence=0.9, bbox=(0, 0, 20, 20)),
                    _result("outfits-1", False, confidence=0.8, bbox=(30, 30, 50, 50)),
                ],
                [
                    _result("setting-1", True, confidence=1.0),
                    _result("outfits-1", True, confidence=1.0),
                ],
            ],
        )
        start = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "TestA"},
        )
        loop_id = start.json()["data"]["loop"]["id"]

        service = main.fidelity_service
        store = main._fidelity_store(project_id)
        entered = threading.Event()
        release = threading.Event()
        original_claim_round = store.claim_round

        def blocking_claim_round(*args: object, **kwargs: object) -> bool:
            # Block BEFORE the atomic DB claim actually runs, so the loop's
            # DB status is STILL "awaiting_user" while the first advance()
            # holds this window — exercising the exact race the fix targets
            # (both callers observe the same awaiting status before either
            # writes back). The SECOND advance() must then be rejected by
            # the in-process lock (acquired earlier in advance(), before
            # this point), never by falling through to a stale re-read.
            entered.set()
            release.wait(timeout=5)
            return original_claim_round(*args, **kwargs)

        monkeypatch.setattr(store, "claim_round", blocking_claim_round)

        results: dict[str, object] = {}

        def run_first_advance() -> None:
            try:
                results["first"] = service.advance(project_id, loop_id)
            except Exception as error:
                results["first"] = error

        first_thread = threading.Thread(target=run_first_advance)
        first_thread.start()
        assert entered.wait(timeout=5), "first advance() never entered the round body"

        with pytest.raises(FidelityLoopConflictError):
            service.advance(project_id, loop_id)

        release.set()
        first_thread.join(timeout=5)
        assert not first_thread.is_alive()

        assert isinstance(results.get("first"), FidelityLoopData)
        assert results["first"].loop.status is FidelityLoopStatus.PASSED
        # Exactly one round ran end-to-end (mask + refine each called once) —
        # the conflicting second call never entered the round body at all.
        assert len(fake.mask_calls) == 1
        assert len(fake.refine_calls) == 1


class TestBaselineCritiqueGuard:
    """C2-review.md MAJOR #2 — ``_run_baseline_critique`` was unguarded."""

    def test_baseline_critique_exception_marks_loop_failed_with_message(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)

        def raising_critique(image_bytes: bytes, checks: list, width: int, height: int) -> list:
            raise RuntimeError("vlm exploded")

        service = FidelityService(
            main.project_manager,
            main.generation_service,
            main._fidelity_store,
            main._character_sheet_resolver,
            critique_fn=raising_critique,
        )
        monkeypatch.setattr(main, "fidelity_service", service)

        with pytest.raises(RuntimeError, match="vlm exploded"):
            service.start_loop(
                project_id,
                asset_id,
                FidelityLoopStartRequest(character_sheet_id=sheet_id, outfit_variant="TestA"),
            )

        # The loop row was created BEFORE the critique ran, so it survives
        # the exception — recoverably marked FAILED, never stuck forever in
        # PENDING_CRITIQUE (a status advance() never accepts). Filtered by
        # this test's own (uuid-unique) root_asset_id rather than a bare
        # ``list_loops`` count — ``main._fidelity_stores`` is a
        # module-global cache keyed by project_id, and every test in this
        # file creates a project literally named "FidelityProj", so an
        # unrelated earlier test's loops can still be cached under the same
        # key (pre-existing, unrelated to this fix).
        loops = main._fidelity_store(project_id).list_loops(project_id)
        matching = [loop for loop in loops if loop.root_asset_id == asset_id]
        assert len(matching) == 1
        loop_id = matching[0].id
        assert matching[0].status is FidelityLoopStatus.FAILED
        assert matching[0].last_error == "vlm exploded"

        get_resp = client.get(f"/api/v1/projects/{project_id}/fidelity-loop/{loop_id}")
        assert get_resp.status_code == 200, get_resp.text
        loop_data = get_resp.json()["data"]["loop"]
        assert loop_data["status"] == "failed"
        assert loop_data["last_error"] == "vlm exploded"

        advance_resp = client.post(f"/api/v1/projects/{project_id}/fidelity-loop/{loop_id}/advance")
        assert advance_resp.status_code == 400, advance_resp.text


class TestAutoContinue:
    def test_auto_continue_reaches_passed(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        _install_fake_service(
            monkeypatch,
            project_id,
            critic_sequence=[
                [  # round 0: both fail
                    _result("setting-1", False, confidence=0.9, bbox=(0, 0, 20, 20)),
                    _result("outfits-1", False, confidence=0.8, bbox=(30, 30, 50, 50)),
                ],
                [  # round 1: one still fails (pass_count improves 0 -> 1)
                    _result("setting-1", True, confidence=1.0),
                    _result("outfits-1", False, confidence=0.7, bbox=(30, 30, 50, 50)),
                ],
                [  # round 2: all pass
                    _result("setting-1", True, confidence=1.0),
                    _result("outfits-1", True, confidence=1.0),
                ],
            ],
        )

        resp = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "TestA", "auto_continue": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["loop"]["status"] == "passed"
        assert data["loop"]["current_round"] == 2
        assert len(data["rounds"]) == 3


class TestGetLoop:
    def test_get_unknown_loop_returns_404(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.get(f"/api/v1/projects/{project_id}/fidelity-loop/missing")
        assert resp.status_code == 404


class TestStream:
    def test_stream_endpoint_unknown_loop_returns_404(self, client: TestClient) -> None:
        project_id = _create_project(client)
        resp = client.get(f"/api/v1/projects/{project_id}/fidelity-loop/missing/stream")
        assert resp.status_code == 404

    def test_stream_yields_progress_then_done_on_state_change(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project_id = _create_project(client)
        sheet_id = _create_character_sheet(client, project_id, tmp_path)
        asset_id = _import_root_asset(client, project_id)
        _install_fake_service(
            monkeypatch,
            project_id,
            critic_sequence=[
                [
                    _result("setting-1", False, confidence=0.9, bbox=(0, 0, 20, 20)),
                    _result("outfits-1", False, confidence=0.8, bbox=(30, 30, 50, 50)),
                ]
            ],
        )
        start = client.post(
            f"/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop",
            json={"character_sheet_id": sheet_id, "outfit_variant": "TestA"},
        )
        loop_id = start.json()["data"]["loop"]["id"]
        base_loop = main._fidelity_store(project_id).get_loop(project_id, loop_id)
        assert base_loop is not None

        # Script the underlying generator's snapshot sequence directly
        # (same idiom test_training_stream.py uses for stream_job_progress):
        # AWAITING_USER (dup, must be suppressed) -> PASSED (terminal, closes).
        snapshots = [
            base_loop,
            base_loop,
            base_loop.model_copy(update={"status": FidelityLoopStatus.PASSED}),
        ]
        seq = iter(snapshots)
        last = {"loop": snapshots[-1]}

        def fake_get_loop(_pid: str, _lid: str):
            try:
                last["loop"] = next(seq)
            except StopIteration:
                pass
            return last["loop"]

        class _ScriptedStore:
            def get_loop(self, pid, lid):
                return fake_get_loop(pid, lid)

        monkeypatch.setattr(main.fidelity_service, "_fidelity_store_resolver", lambda pid: _ScriptedStore())
        monkeypatch.setattr(
            main.fidelity_service,
            "stream_loop_progress",
            lambda pid, lid, **kw: FidelityService.stream_loop_progress(
                main.fidelity_service, pid, lid, poll_interval_sec=0, sleep=lambda _: None
            ),
        )

        resp = client.get(f"/api/v1/projects/{project_id}/fidelity-loop/{loop_id}/stream")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "event: progress" in body
        assert "event: done" in body
        assert '"status": "passed"' in body
