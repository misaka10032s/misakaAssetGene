import json
import logging
import mimetypes
import threading
import time
from collections.abc import Iterator
from pathlib import Path

# Maximum allowed upload size for project zip imports (spec §5.5 streaming guard).
# Must be checked BEFORE reading into memory; this caps the raw compressed bytes.
_UPLOAD_MAX_BYTES: int = 2 * 1024 ** 3  # 2 GiB
_UPLOAD_CHUNK_SIZE: int = 256 * 1024     # 256 KiB per read chunk

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.exceptions import RequestValidationError

from core.config import get_settings
from core.logging_redaction import install_redaction_filter
from core.consultant.engine import ConsultantEngine
from core.consultant.fidelity import load_character_sources, list_outfit_variants
from core.consultant.fidelity_service import FidelityLoopConflictError, FidelityService
from core.consultant.fidelity_store import FidelityStore
from core.consultant.fidelity_suggestion import build_fidelity_suggestion_cards
from core.editor.mask import ImageHeaderError, MaskRegionError, build_mask_png, read_image_size
from core.generation.service import GenerationService
from core.integration.model_registry import ModelRegistryService
from core.integration.tools import ToolsService
from core.integration.workers import WorkersService
from core.llm.local_manager import LocalLlmManager
from core.llm.router import gate_providers, list_providers
from core.llm.service import optimize_synopsis as llm_optimize_synopsis
from core.models.schemas import (
    ApiErrorResponse,
    ApiResponse,
    BatchExecuteData,
    BatchExecuteRequest,
    CharacterSheet,
    CharacterSheetCreateRequest,
    CharacterSheetUpdateRequest,
    ClarifyRequest,
    ConsultantSessionAdvanceRequest,
    ConsultantSessionStartRequest,
    ConversationHistoryData,
    CrossRefListData,
    CrossRefEntry,
    CrossRefStatus,
    DatasetPack,
    DatasetPackCreateRequest,
    DatasetPackUpdateRequest,
    FidelityLoopStartRequest,
    FidelityLoopStatus,
    HealthData,
    ImageToVideoRecipe,
    ImageToVideoRecipeCreateRequest,
    ImageToVideoRecipeUpdateRequest,
    IntegrationSnapshot,
    JobExecutionPatch,
    LocalLlmStatus,
    LoraPreset,
    LoraPresetCreateRequest,
    LoraPresetUpdateRequest,
    MaskFromRegionsRequest,
    MaskFromRegionsResponse,
    MaterializeData,
    MaterializeRequest,
    MaterializeResultEntry,
    MessageKey,
    ModelDownloadRequest,
    ModelDownloadResult,
    Modality,
    ClarifyResult,
    ProjectLicenseReport,
    ProjectSettingsUpdateRequest,
    ProjectWorkspaceData,
    ProjectListData,
    ProjectVersionGraph,
    ProjectSelectRequest,
    RefineRequest,
    SynopsisOptimizeRequest,
    TrainingJobCreateRequest,
    TrainingJobPollData,
    TrainingJobStatus,
    TrainingRecipe,
    TrainingRecipeCreateRequest,
    TrainingRecipeUpdateRequest,
    TrainingWorkspaceData,
    VersionDiffRequest,
    WorkerSmokeResult,
)
from core.network.origin_guard import OriginGuardMiddleware, resolve_allowed_origins
from core.network.service import NetworkStateService
from core.network.state import NetworkState
from core.project.cross_project import (
    RefStatus,
    collect_project_refs,
    detect_cycles,
    materialize_project_refs,
    materialize_reference,
    parse_reference,
    resolve_reference,
)
from core.project.export import ProjectExportService
from core.project.portability import ZipImportError, import_project_zip
from core.project.manager import (
    PROJECT_TYPES,
    ProjectConflictError,
    ProjectCreateRequest,
    ProjectManager,
    ProjectNotFoundError,
    ProjectValidationError,
    validate_project_id,
)
from core.reporting.license import LicenseReportService
from core.scheduler.vram import ModelScheduler, SchedulerBudget
from core.training.asset_store import AssetStore
from core.training.executor import SubprocessRunner, TrainingExecutor
from core.training.service import TrainingService, TrainingValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = REPO_ROOT / "projects"
settings = get_settings()
APP_ENV = settings.misaka_env.lower()
IS_DEV = settings.is_dev

# Configure logging first so that root handlers exist before we install
# the redaction filter on them.  The filter must be on each HANDLER (not the
# root logger itself) so that records propagated from child loggers like
# misaka.core.anything are redacted before they reach the output stream.
# A filter on a logger only runs for records emitted directly to that logger;
# propagated records skip the logger's .filters and go straight to handlers.
logging.basicConfig(
    level=logging.INFO if IS_DEV else logging.WARNING,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)

# Install AFTER basicConfig so root handlers already exist.
# Controlled by MISAKA_LOG_REDACT (default ON).
install_redaction_filter()
logger = logging.getLogger("misaka.core")

def enforce_valid_project_id(request: Request) -> None:
    """Route-layer guard: reject malformed ``project_id`` path params uniformly.

    Registered as an app-level dependency so every route carrying a
    ``{project_id}`` path parameter is validated against the
    ``^[a-z0-9_-]+$`` whitelist BEFORE its handler runs — closing all
    get_project-derived path traversal in one place (security). Routes without
    a ``project_id`` path param are unaffected (the param is simply absent).
    """
    project_id = request.path_params.get("project_id")
    if project_id is None:
        return
    try:
        validate_project_id(project_id)
    except ProjectValidationError as error:
        # 404 (not 422) so a probe cannot distinguish "malformed" from
        # "absent" — consistent with the not-found contract for these routes.
        raise HTTPException(status_code=404, detail=str(error)) from error


app = FastAPI(
    title="MisakaAssetGene Core Service",
    version="0.1.0",
    dependencies=[Depends(enforce_valid_project_id)],
)
# 待回答 #47 — CORS and the Origin/Host guard below now share ONE allow-list
# source (`resolve_allowed_origins`) so they can never drift apart. This
# replaces the previous `allow_origin_regex` (any port on localhost/127.0.0.1)
# with the exact loopback+Tauri origin set — never `allow_origins=["*"]` on a
# server that accepts writes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_allowed_origins(settings),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Server-side guard: CORS alone is a browser-enforced convention (a
# non-browser or rebinding-style client can ignore it entirely), so
# state-changing requests are also checked here regardless of what sent them.
app.add_middleware(OriginGuardMiddleware, settings=settings)

project_manager = ProjectManager(PROJECTS_ROOT)


def _resolve_project_dir_for_sessions(project_id: str) -> Path:
    _, project_dir = project_manager.get_project(project_id)
    return project_dir


consultant_engine = ConsultantEngine(sessions_path_resolver=_resolve_project_dir_for_sessions)
tools_service = ToolsService(REPO_ROOT / "tools" / "manifest.json")
workers_service = WorkersService(REPO_ROOT / "workers" / "manifest.json")
model_registry_service = ModelRegistryService(REPO_ROOT / "core" / "models" / "registry.json")
local_llm_manager = LocalLlmManager()
_vram_scheduler = ModelScheduler(
    SchedulerBudget(
        vram_budget_mb=int(settings.misaka_vram_budget_mb) if hasattr(settings, "misaka_vram_budget_mb") else 12000,
        ram_budget_mb=int(settings.misaka_ram_budget_mb) if hasattr(settings, "misaka_ram_budget_mb") else 32000,
    )
)

# M4.d — wire the scheduler into GenerationService so it can gate generation
# dispatch while the exclusive training lock is held (spec §7.3).
generation_service = GenerationService(project_manager, workers_service, scheduler=_vram_scheduler)
network_state_service = NetworkStateService()
project_export_service = ProjectExportService()
license_report_service = LicenseReportService()
training_service = TrainingService(project_manager)


# §7.1.1 asset stores — one per project, keyed by project_id; opened lazily.
_asset_stores: dict[str, AssetStore] = {}


def _asset_store(project_id: str) -> AssetStore:
    """Return (and cache) the AssetStore for a project's memory.sqlite."""
    if project_id not in _asset_stores:
        _, project_dir = project_manager.get_project(project_id)
        _asset_stores[project_id] = AssetStore(project_dir / "memory.sqlite")
    return _asset_stores[project_id]


def _project_dir(project_id: str) -> Path:
    """Return the project directory path."""
    _, project_dir = project_manager.get_project(project_id)
    return project_dir


# Fidelity-loop stores — one per project, same memory.sqlite AssetStore uses,
# opened lazily (spec §5, mirrors _asset_store above).
_fidelity_stores: dict[str, FidelityStore] = {}


def _fidelity_store(project_id: str) -> FidelityStore:
    if project_id not in _fidelity_stores:
        _, project_dir = project_manager.get_project(project_id)
        _fidelity_stores[project_id] = FidelityStore(project_dir / "memory.sqlite")
    return _fidelity_stores[project_id]


def _character_sheet_resolver(project_id: str, character_sheet_id: str) -> CharacterSheet | None:
    return _asset_store(project_id).get_character_sheet(project_id, character_sheet_id)


def _outfit_variant_choices(character_sheet: CharacterSheet) -> list[str]:
    """Real I/O resolver for ``FidelitySuggestionCard.outfit_variant_choices``
    (spec §5.15 / C-spec.md §4.3) — reads the sheet's ``outfits.md`` live
    (spec §2.2: never cached), same source ``core.consultant.fidelity``
    already reads for the checklist itself. A malformed/missing
    ``sheet_source_path`` must never break the whole consultant clarify
    response just to surface a suggestion card, so any parse failure here
    is logged and degrades to an empty choice list (the card still appears;
    the frontend's outfit dropdown is simply empty until the sheet is
    fixed)."""
    if not character_sheet.sheet_source_path:
        return []
    try:
        _, outfits_text = load_character_sources(character_sheet.sheet_source_path)
        return list_outfit_variants(outfits_text)
    except (OSError, ValueError) as error:
        logger.warning(
            "fidelity suggestion card: could not read outfit variants for character_sheet=%s: %s",
            character_sheet.id, error,
        )
        return []


def _attach_fidelity_suggestion_cards(project_dir: Path, project_id: str, project_auto_loop_enabled: bool, result: ClarifyResult) -> ClarifyResult:
    """Attach ``FidelitySuggestionCard``(s) to a consultant response
    (spec §5.15 / C-spec.md §4.3). Computed at the route layer, never inside
    the stateless planner — the emission condition (an IMAGE asset + a
    CharacterSheet with sheet_source_path) is project state the planner has
    no access to. A no-op (returns ``result`` unchanged) when the condition
    is not met, so every existing caller/test keeps getting an empty list
    (the pydantic field default) exactly as before this feature existed."""
    assets = generation_service._read_assets(project_dir)
    character_sheets = _asset_store(project_id).list_character_sheets(project_id)
    cards = build_fidelity_suggestion_cards(
        assets,
        character_sheets,
        outfit_variant_resolver=_outfit_variant_choices,
        auto_continue_default=project_auto_loop_enabled,
    )
    if not cards:
        return result
    return result.model_copy(update={"fidelity_suggestion_cards": cards})


def _current_network_state() -> NetworkState:
    """Mirror the /api/v1/integration route's network snapshot construction
    (see below) so the fidelity-loop VLM critic gates cloud providers the
    same way every other LLM call in this repo does."""
    return network_state_service.snapshot(
        settings.misaka_network_mode,
        [
            settings.anthropic_api_base_url,
            settings.openai_api_base_url,
            settings.gemini_api_base_url,
        ],
        local_urls=[f"{settings.misaka_ollama_base_url.rstrip('/')}/api/tags"],
    ).state


fidelity_service = FidelityService(
    project_manager,
    generation_service,
    _fidelity_store,
    _character_sheet_resolver,
    settings=settings,
    network_state_provider=_current_network_state,
)


# Per-project job read/write callables — take project_id so the executor never
# binds to the first project's jobs.json (MAJOR 2 fix).
def _read_training_jobs(project_id: str) -> list:  # type: ignore[return-type]
    _, project_dir = project_manager.get_project(project_id)
    return training_service._read_jobs(project_dir)


def _write_training_jobs(project_id: str, jobs: list) -> None:  # type: ignore[return-type]
    _, project_dir = project_manager.get_project(project_id)
    training_service._write_jobs(project_dir, jobs)


# One shared executor for all projects — serialises training globally (FIFO).
# The executor is project-aware: read_jobs/write_jobs both receive project_id
# so jobs from different projects are persisted to their own jobs.json files.
# REAL-RUN NOTE: The executor is wired with SubprocessRunner but has not been
# verified against a live kohya_ss / GPT-SoVITS installation.  See RESEARCH_LOG §10.
_training_executor: TrainingExecutor | None = None


def _get_or_create_executor() -> TrainingExecutor:
    """Return the singleton executor, creating it on first call."""
    global _training_executor
    if _training_executor is None:
        _training_executor = TrainingExecutor(
            read_jobs=_read_training_jobs,
            write_jobs=_write_training_jobs,
            scheduler=_vram_scheduler,
            runner=SubprocessRunner(),
            asset_store_resolver=_asset_store,
            project_dir_resolver=_project_dir,
            workers_service=workers_service,
        )
        training_service.set_executor(_training_executor)
    return _training_executor


integration_snapshot_cache: tuple[float, IntegrationSnapshot] | None = None
integration_snapshot_lock = threading.Lock()


def success_response(message: MessageKey, data: object) -> ApiResponse:
    return ApiResponse(message=message, data=data)


def invalidate_integration_snapshot_cache() -> None:
    global integration_snapshot_cache
    with integration_snapshot_lock:
        integration_snapshot_cache = None


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: object, exc: RequestValidationError) -> JSONResponse:
    payload = ApiErrorResponse(message=MessageKey.FAIL_400, detail=exc.errors())
    return JSONResponse(status_code=400, content=jsonable_encoder(payload))


@app.exception_handler(HTTPException)
async def http_exception_handler(_: object, exc: HTTPException) -> JSONResponse:
    message = {
        400: MessageKey.FAIL_400,
        401: MessageKey.FAIL_401,
        403: MessageKey.FAIL_403,
        404: MessageKey.FAIL_404,
        409: MessageKey.FAIL_409,
    }.get(exc.status_code, MessageKey.FAIL_500)
    payload = ApiErrorResponse(message=message, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(payload))


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: object, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    payload = ApiErrorResponse(message=MessageKey.FAIL_500, detail={"reason": type(exc).__name__})
    return JSONResponse(status_code=500, content=jsonable_encoder(payload))


@app.on_event("startup")
def on_startup() -> None:
    llm_status = local_llm_manager.status(settings)
    if IS_DEV:
        logger.info("Core service booting from %s", REPO_ROOT)
        logger.info("Projects root: %s", PROJECTS_ROOT)
        logger.info("Loaded %d tool definitions", len(tools_service.list_tools()))
        logger.info("Loaded %d worker definitions", workers_service.worker_definition_count())
        logger.info("Loaded registry categories: %s", ", ".join(model_registry_service.list_categories()))
        logger.info("CORS / Origin guard allow-list: %s", ", ".join(resolve_allowed_origins(settings)))
        logger.info("Local LLM running: %s", llm_status["is_running"])


@app.get("/healthz", response_model=ApiResponse)
def healthz() -> ApiResponse:
    if IS_DEV:
        logger.info("GET /healthz")
    return success_response(
        MessageKey.SUCCESS_FETCH0,
        HealthData(status="Core online", repo_root=str(REPO_ROOT), environment=APP_ENV).model_dump(),
    )


@app.get("/api/v1/project-types", response_model=ApiResponse)
def list_project_types() -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/project-types")
    return success_response(MessageKey.SUCCESS_FETCH0, {"project_types": PROJECT_TYPES})


@app.get("/api/v1/projects", response_model=ApiResponse)
def list_projects() -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects")
    payload = ProjectListData(
        projects=project_manager.list_projects(),
        current_project_id=project_manager.get_current_project_id(),
    )
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump())


@app.post("/api/v1/projects", response_model=ApiResponse)
def create_project(payload: ProjectCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects name=%s type=%s", payload.name, payload.type)
    try:
        project = project_manager.create_project(payload)
    except ProjectValidationError as error:
        if IS_DEV:
            logger.warning("Project creation rejected: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ProjectConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if IS_DEV:
        logger.info("Project created successfully: %s", project.name)
    return success_response(MessageKey.SUCCESS_ADD0, {"project": project.model_dump()})


@app.post("/api/v1/projects/select", response_model=ApiResponse)
def select_project(payload: ProjectSelectRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/select id=%s", payload.project_id)
    try:
        project = project_manager.select_project(payload.project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_SWITCH0, {"project": project.model_dump()})


@app.get("/api/v1/projects/{project_id}", response_model=ApiResponse)
def get_project(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s", project_id)
    try:
        project, _ = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, {"project": project.model_dump()})


@app.patch("/api/v1/projects/{project_id}/settings", response_model=ApiResponse)
def update_project_settings(project_id: str, payload: ProjectSettingsUpdateRequest) -> ApiResponse:
    """Spec §5.15 / C-spec.md §5 — currently the single setting
    ``auto_loop_enabled`` (the default for an omitted
    ``FidelityLoopStartRequest.auto_continue``); shaped to grow additional
    project settings later without a new route."""
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/settings auto_loop_enabled=%s", project_id, payload.auto_loop_enabled)
    try:
        project = project_manager.update_settings(project_id, auto_loop_enabled=payload.auto_loop_enabled)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_SWITCH0, {"project": project.model_dump()})


@app.get("/api/v1/project-schema", response_model=ApiResponse)
def get_project_schema() -> ApiResponse:
    schema_path = REPO_ROOT / "core" / "project" / "project.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return success_response(MessageKey.SUCCESS_FETCH0, {"schema": schema})


@app.post("/api/v1/projects/synopsis-optimize", response_model=ApiResponse)
def optimize_project_synopsis(payload: SynopsisOptimizeRequest) -> ApiResponse:
    if IS_DEV:
        logger.info(
            "POST /api/v1/projects/synopsis-optimize name=%s type=%s",
            payload.project_name,
            payload.project_type,
        )
    try:
        result = llm_optimize_synopsis(settings, payload.project_name, payload.project_type, payload.synopsis)
    except HTTPException as error:
        raise error
    return success_response(MessageKey.SUCCESS_FETCH0, result.model_dump())


@app.post("/api/v1/consultant/clarify", response_model=ApiResponse)
def clarify(payload: ClarifyRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/consultant/clarify modality=%s", payload.modality.value if payload.modality else "auto")
    return success_response(MessageKey.SUCCESS_FETCH0, consultant_engine.clarify(payload).model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/conversation", response_model=ApiResponse)
def list_project_conversation(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=40, ge=1, le=200),
) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/conversation offset=%s limit=%s", project_id, offset, limit)
    try:
        entries = project_manager.list_conversation_entries(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    total = len(entries)
    end_index = max(total - offset, 0)
    start_index = max(end_index - limit, 0)
    paged_entries = entries[start_index:end_index]
    payload = ConversationHistoryData(
        entries=paged_entries,
        total=total,
        offset=offset,
        limit=limit,
        has_more=start_index > 0,
    )
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/consultant/clarify", response_model=ApiResponse)
def clarify_project(project_id: str, payload: ClarifyRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/consultant/clarify modality=%s", project_id, payload.modality.value if payload.modality else "auto")
    try:
        project, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    result = consultant_engine.clarify(
        ClarifyRequest(
            prompt=payload.prompt,
            modality=payload.modality,
            project_name=project.name,
            project_type=project.type,
            project_synopsis=project.synopsis,
        )
    )
    result = _attach_fidelity_suggestion_cards(project_dir, project_id, project.auto_loop_enabled, result)
    project_manager.append_conversation_entries(
        project_id,
        [
            project_manager.build_conversation_entry(
                role="user",
                content=payload.prompt,
                modality=result.modality.value,
            ),
            project_manager.build_conversation_entry(
                role="assistant",
                content=result.summary,
                modality=result.modality.value,
                questions=[str(question) for question in result.questions],
                analysis=result.analysis,
            ),
        ],
    )
    generation_service.record_plan(project_id, payload.prompt, result)
    return success_response(MessageKey.SUCCESS_FETCH0, result.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/consultant/session", response_model=ApiResponse)
def resume_consultant_session(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/consultant/session", project_id)
    try:
        project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session = consultant_engine.resume_session(project_id)
    return success_response(
        MessageKey.SUCCESS_FETCH0,
        {"session": session.model_dump(mode="json") if session else None},
    )


@app.post("/api/v1/projects/{project_id}/consultant/session", response_model=ApiResponse)
def start_consultant_session(project_id: str, payload: ConsultantSessionStartRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/consultant/session", project_id)
    try:
        project, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    session_data = consultant_engine.start_session(
        project_id,
        ClarifyRequest(
            prompt=payload.prompt,
            modality=payload.modality,
            project_name=project.name,
            project_type=project.type,
            project_synopsis=project.synopsis,
        ),
        session_id=payload.session_id,
    )
    if session_data.result is not None:
        session_data = session_data.model_copy(
            update={
                "result": _attach_fidelity_suggestion_cards(
                    project_dir, project_id, project.auto_loop_enabled, session_data.result
                )
            }
        )
    return success_response(MessageKey.SUCCESS_ADD0, session_data.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/consultant/session/advance", response_model=ApiResponse)
def advance_consultant_session(project_id: str, payload: ConsultantSessionAdvanceRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/consultant/session/advance id=%s", project_id, payload.session_id)
    try:
        project, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        session_data = consultant_engine.advance_session(
            project_id,
            payload.session_id,
            ClarifyRequest(
                prompt=payload.prompt or "(continue)",
                modality=None,
                project_name=project.name,
                project_type=project.type,
                project_synopsis=project.synopsis,
            ),
            slots=payload.slots,
            accept=payload.accept,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if session_data.result is not None:
        session_data = session_data.model_copy(
            update={
                "result": _attach_fidelity_suggestion_cards(
                    project_dir, project_id, project.auto_loop_enabled, session_data.result
                )
            }
        )
    return success_response(MessageKey.SUCCESS_SWITCH0, session_data.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/workspace", response_model=ApiResponse)
def project_workspace(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/workspace", project_id)
    try:
        payload = generation_service.list_workspace(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/jobs/{job_id}/execute", response_model=ApiResponse)
def execute_project_job(project_id: str, job_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/jobs/%s/execute", project_id, job_id)
    try:
        payload = generation_service.execute_job(project_id, job_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_SWITCH0, payload.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/jobs/execute-ready", response_model=ApiResponse)
def execute_ready_project_jobs(project_id: str, payload: BatchExecuteRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/jobs/execute-ready count=%s", project_id, len(payload.job_ids))
    try:
        result: BatchExecuteData = generation_service.execute_ready_jobs(project_id, payload.job_ids)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if IS_DEV:
        logger.info(
            "execute-ready: executed=%d skipped=%d",
            result.executed_count,
            len(result.skipped),
        )
    return success_response(MessageKey.SUCCESS_SWITCH0, result.model_dump(mode="json"))


@app.patch("/api/v1/projects/{project_id}/jobs/{job_id}", response_model=ApiResponse)
def update_project_job(project_id: str, job_id: str, payload: JobExecutionPatch) -> ApiResponse:
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/jobs/%s", project_id, job_id)
    try:
        result = generation_service.update_job(project_id, job_id, payload)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_SWITCH0, result.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/assets/import", response_model=ApiResponse)
async def import_project_asset(
    project_id: str,
    file: UploadFile = File(...),
    modality: str = Form(...),
    asset_type: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/assets/import", project_id)
    try:
        payload = generation_service.import_asset(
            project_id,
            filename=file.filename or "upload.bin",
            content=await file.read(),
            modality=Modality(modality),
            asset_type=asset_type,
            title=title,
            description=description,
        )
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_ADD0, payload.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/assets/{asset_id}/mask", response_model=ApiResponse)
def create_asset_mask_from_regions(
    project_id: str, asset_id: str, payload: MaskFromRegionsRequest
) -> ApiResponse:
    """Build a bbox-region mask PNG from an existing image asset (BP-EDITOR-2).

    White-on-black output, same polarity the manual mask-painting editor
    already produces (LoadImageMask channel=red — BP-EDITOR-1,
    comfyui.py:316-319): union of ``regions`` (each optionally
    dilated/feathered) minus the union of ``subtract`` regions, same
    width/height as the source asset. Stored as a new mask AssetRecord via
    the SAME ``import_asset`` path the manual editor's upload uses.

    Security: reuses the identical resolve-then-contain guard as
    GET .../assets/{asset_id}/file (M5.3/M5.9) before reading source bytes.
    """
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/assets/%s/mask", project_id, asset_id)
    try:
        _, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    assets = generation_service._read_assets(project_dir)
    source_asset = next((a for a in assets if a.id == asset_id), None)
    if source_asset is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    if source_asset.modality != Modality.IMAGE:
        raise HTTPException(
            status_code=400,
            detail=f"Source asset must be an image, got modality={source_asset.modality.value!r}",
        )

    # Path-containment guard identical to GET .../assets/{asset_id}/file.
    assets_root = (project_dir / "assets").resolve()
    file_path = (project_dir / source_asset.path).resolve()
    try:
        file_path.relative_to(assets_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="Asset file path is outside the permitted directory.")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Asset file not found on disk: {source_asset.path}")

    try:
        source_bytes = file_path.read_bytes()
        width, height = read_image_size(source_bytes)
        result = build_mask_png(width, height, payload)
    except (ImageHeaderError, MaskRegionError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    mask_title = payload.name or f"mask-{asset_id}"
    workspace = generation_service.import_asset(
        project_id,
        filename=f"{mask_title}.png",
        content=result.png_bytes,
        modality=Modality.IMAGE,
        asset_type="mask",
        title=mask_title,
        description=f"Auto-generated mask from {len(payload.regions)} region(s) for asset {asset_id}",
    )
    mask_asset_id = workspace.assets[-1].id
    response = MaskFromRegionsResponse(
        mask_asset_id=mask_asset_id,
        width=result.width,
        height=result.height,
        coverage_ratio=result.coverage_ratio,
        clamped=result.clamped,
    )
    return success_response(MessageKey.SUCCESS_ADD0, response.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/assets/{asset_id}/refine", response_model=ApiResponse)
def refine_project_asset(project_id: str, asset_id: str, payload: RefineRequest) -> ApiResponse:
    """Create a refine job from an existing image version (spec §5.11 / §6.2)."""
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/assets/%s/refine", project_id, asset_id)
    try:
        result = generation_service.refine_asset(project_id, asset_id, payload)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# §5.15 / C-spec.md §4-5 — Character fidelity refine LOOP (Brief 2)
# ---------------------------------------------------------------------------

@app.post("/api/v1/projects/{project_id}/assets/{asset_id}/fidelity-loop", response_model=ApiResponse)
def start_fidelity_loop(project_id: str, asset_id: str, payload: FidelityLoopStartRequest) -> ApiResponse:
    """Start a character-fidelity refine loop against ``asset_id`` as the
    root version (spec §4.1/§5). Runs round 0 (baseline critique, no
    mask/refine) synchronously before returning — starting the loop IS the
    round-0 "click" (spec §4.3)."""
    if IS_DEV:
        logger.info(
            "POST /api/v1/projects/%s/assets/%s/fidelity-loop character_sheet_id=%s outfit_variant=%s",
            project_id, asset_id, payload.character_sheet_id, payload.outfit_variant,
        )
    try:
        project, _ = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    # Brief 3 (spec §5.15 / C-spec.md §5): an omitted auto_continue defaults
    # to this project's auto_loop_enabled setting; resolved to a concrete
    # bool HERE, before FidelityService ever sees the request, so an
    # explicit True/False in the request always wins over the project
    # default and the service/store never have to know about the default.
    resolved_payload = payload if payload.auto_continue is not None else payload.model_copy(
        update={"auto_continue": project.auto_loop_enabled}
    )
    try:
        result = fidelity_service.start_loop(project_id, asset_id, resolved_payload)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/fidelity-loop/{loop_id}/advance", response_model=ApiResponse)
def advance_fidelity_loop(project_id: str, loop_id: str) -> ApiResponse:
    """Run exactly one refine round of an AWAITING_USER /
    STOPPED_REGRESSION_RECOVERED loop (spec §4.1/§5)."""
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/fidelity-loop/%s/advance", project_id, loop_id)
    try:
        result = fidelity_service.advance(project_id, loop_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FidelityLoopConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/fidelity-loop/{loop_id}", response_model=ApiResponse)
def get_fidelity_loop(project_id: str, loop_id: str) -> ApiResponse:
    """Current state of a fidelity loop, including a preview of the next
    round's plan when one is pending (spec §5)."""
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/fidelity-loop/%s", project_id, loop_id)
    try:
        result = fidelity_service.get(project_id, loop_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, result.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/fidelity-loop/{loop_id}/stream")
def stream_fidelity_loop(project_id: str, loop_id: str) -> StreamingResponse:
    """Server-Sent Events stream of a fidelity loop's progress (spec §4.4,
    mirrors ``stream_training_job`` below). Each frame's ``event`` name is
    ``done`` once the loop reaches a terminal status (PASSED /
    STOPPED_MAX_ROUNDS / STOPPED_UNVERIFIED / FAILED), ``progress``
    otherwise."""
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/fidelity-loop/%s/stream", project_id, loop_id)
    try:
        project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if _fidelity_store(project_id).get_loop(project_id, loop_id) is None:
        raise HTTPException(status_code=404, detail=f"Fidelity loop not found: {loop_id}")

    def event_source() -> "Iterator[str]":
        for loop in fidelity_service.stream_loop_progress(project_id, loop_id):
            envelope = loop.model_dump(mode="json")
            is_terminal = loop.status in {
                FidelityLoopStatus.PASSED,
                FidelityLoopStatus.STOPPED_MAX_ROUNDS,
                FidelityLoopStatus.STOPPED_UNVERIFIED,
                FidelityLoopStatus.FAILED,
            }
            event_name = "done" if is_terminal else "progress"
            yield f"event: {event_name}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/projects/{project_id}/assets/{asset_id}/file")
def get_project_asset_file(project_id: str, asset_id: str) -> FileResponse:
    """Serve the raw file bytes for a project asset (M5.9).

    Security: the resolved file path is verified to be strictly inside the
    project's assets/ directory before any bytes are served.  A path that
    escapes the root (via '..' or symlink) returns 404, never the file.
    """
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/assets/%s/file", project_id, asset_id)
    # Step 1: resolve project; 404 if unknown.
    try:
        _, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    # Step 2: read assets index and locate the requested asset.
    assets = generation_service._read_assets(project_dir)
    asset = next((a for a in assets if a.id == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    # Step 3: build file path and apply path-containment guard.
    # asset.path is relative to project_dir (e.g. "assets/images/foo.png").
    # We only permit files that reside inside project_dir/assets/ to prevent
    # directory-traversal attacks and symlink escapes.
    assets_root = (project_dir / "assets").resolve()
    file_path = (project_dir / asset.path).resolve()
    try:
        file_path.relative_to(assets_root)
    except ValueError:
        # Resolved path escapes the assets root — refuse to serve.
        raise HTTPException(status_code=404, detail="Asset file path is outside the permitted directory.")

    # Step 4: 404 if the file does not exist on disk.
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Asset file not found on disk: {asset.path}")

    # Step 5: infer media_type from extension; fall back to octet-stream.
    guessed, _ = mimetypes.guess_type(file_path.name)
    media_type = guessed or "application/octet-stream"

    # Return as inline so browsers render images directly (not force-download).
    # Use FileResponse(filename=..., content_disposition_type="inline") rather than a
    # hand-built header string: Starlette encodes response headers as latin-1, so a CJK
    # filename in a raw f-string header raises UnicodeEncodeError → 500.  Passing
    # `filename` lets Starlette apply RFC 5987 percent-encoding automatically
    # (filename*=UTF-8''<quoted>) whenever the name contains non-ASCII characters.
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
        content_disposition_type="inline",
    )


@app.get("/api/v1/integration", response_model=ApiResponse)
def integration_snapshot() -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/integration")
    global integration_snapshot_cache
    with integration_snapshot_lock:
        if integration_snapshot_cache is not None:
            cached_at, cached_payload = integration_snapshot_cache
            if time.monotonic() - cached_at <= 3.0:
                return success_response(MessageKey.SUCCESS_FETCH0, cached_payload.model_dump())
        network_snapshot = network_state_service.snapshot(
            settings.misaka_network_mode,
            [
                settings.anthropic_api_base_url,
                settings.openai_api_base_url,
                settings.gemini_api_base_url,
            ],
            local_urls=[f"{settings.misaka_ollama_base_url.rstrip('/')}/api/tags"],
        )
        payload = IntegrationSnapshot(
            tools=tools_service.list_tools(),
            workers=workers_service.list_workers(refresh=True),
            providers=gate_providers(list_providers(settings), network_snapshot.state),
            registry_categories=model_registry_service.list_categories(),
            model_search_paths=settings.model_search_paths,
            network=network_snapshot,
        )
        integration_snapshot_cache = (time.monotonic(), payload)
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump())


@app.get("/api/v1/projects/{project_id}/versions", response_model=ApiResponse)
def project_versions(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/versions", project_id)
    try:
        payload = generation_service.build_version_graph(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/versions/tree", response_model=ApiResponse)
def project_versions_tree(project_id: str) -> ApiResponse:
    """Version-tree DAG for a project (spec §8.2 / M5.1).

    Returns all asset versions as a parent-child DAG suitable for rendering a
    git-log-style branching graph.  Cycle detection and orphan handling are
    applied server-side (see VersionTreeData envelope fields).
    """
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/versions/tree", project_id)
    try:
        payload = generation_service.build_version_tree(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/versions/diff", response_model=ApiResponse)
def project_versions_diff(
    project_id: str,
    from_id: str = Query(..., description="Source version asset id"),
    to_id: str = Query(..., description="Target version asset id"),
) -> ApiResponse:
    """Structured delta between two asset versions (spec §8.2 / M5.1).

    Returns prompt delta, parameter diff, mask/source difference, recipe
    difference and backend difference.  Both ``from_id`` and ``to_id`` must
    exist in the project.
    """
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/versions/diff from=%s to=%s", project_id, from_id, to_id)
    try:
        payload = generation_service.diff_versions(project_id, from_id, to_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/license-report", response_model=ApiResponse)
def project_license_report(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/license-report", project_id)
    try:
        project, project_dir = project_manager.get_project(project_id)
        workspace = generation_service.list_workspace(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    payload = license_report_service.generate_report(
        project_summary={**project.model_dump(), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        jobs=workspace.jobs,
        assets=workspace.assets,
        workers_service=workers_service,
        registry_path=REPO_ROOT / "core" / "models" / "registry.json",
    )
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/export/download")
def export_project_download(project_id: str, resolve_refs: bool = Query(default=True)) -> FileResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/export/download resolve_refs=%s", project_id, resolve_refs)
    try:
        project, project_dir = project_manager.get_project(project_id)
        workspace = generation_service.list_workspace(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    license_report = license_report_service.generate_report(
        project_summary={**project.model_dump(), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        jobs=workspace.jobs,
        assets=workspace.assets,
        workers_service=workers_service,
        registry_path=REPO_ROOT / "core" / "models" / "registry.json",
    )
    export_path = project_export_service.export_project(
        project_dir=project_dir,
        project_summary=project.model_dump(),
        jobs=[job.model_dump(mode="json") for job in workspace.jobs],
        assets=[asset.model_dump(mode="json") for asset in workspace.assets],
        plans=[plan.model_dump(mode="json") for plan in workspace.plans],
        license_report=license_report.model_dump(mode="json"),
        resolve_refs=resolve_refs,
    )
    return FileResponse(path=export_path, media_type="application/zip", filename=export_path.name)


@app.get("/api/v1/projects/{project_id}/training", response_model=ApiResponse)
def project_training_workspace(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/training", project_id)
    try:
        payload = training_service.list_jobs(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/training", response_model=ApiResponse)
def create_project_training_job(project_id: str, payload: TrainingJobCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/training worker=%s", project_id, payload.worker or "auto")
    # Ensure the executor is initialised for this project before submitting.
    try:
        _get_or_create_executor()
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    try:
        result = training_service.submit_job(project_id, payload)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TrainingValidationError as error:
        if IS_DEV:
            logger.warning("Training job submission rejected: %s", error)
        raise HTTPException(status_code=400, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/training/{job_id}", response_model=ApiResponse)
def poll_training_job(project_id: str, job_id: str) -> ApiResponse:
    """Poll the status of a single training job (spec §7.3 progress)."""
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/training/%s", project_id, job_id)
    try:
        job = training_service.poll_job(project_id, job_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if job is None:
        raise HTTPException(status_code=404, detail=f"Training job not found: {job_id}")
    return success_response(MessageKey.SUCCESS_FETCH0, TrainingJobPollData(job=job).model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/training/{job_id}/stream")
def stream_training_job(project_id: str, job_id: str) -> StreamingResponse:
    """Server-Sent Events stream of a training job's progress (spec §7.3).

    Replaces client-side GET polling: the executor persists incremental status
    to the per-project job store and this endpoint pushes one ``data:`` frame
    per observable change (status / progress / label). The stream closes once
    the job reaches a terminal status (completed / failed).

    Each frame is ``event: progress`` with a JSON body matching the poll
    endpoint's ``TrainingJobPollData`` shape, so the frontend can reuse the
    same parsing. A terminal frame is tagged ``event: done``.

    REAL-RUN NOTE: the push path is contract/unit-tested with a fake job store
    (see tests/test_training_stream.py). End-to-end verification against a live
    kohya_ss / GPT-SoVITS GPU training run is DEFERRED to the user.
    """
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/training/%s/stream", project_id, job_id)
    # Validate project up-front so an unknown project fails with 404 before the
    # streaming body opens (a 404 mid-stream cannot be expressed in SSE).
    try:
        project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if training_service.poll_job(project_id, job_id) is None:
        raise HTTPException(status_code=404, detail=f"Training job not found: {job_id}")

    def event_source() -> "Iterator[str]":
        for job in training_service.stream_job_progress(project_id, job_id):
            envelope = TrainingJobPollData(job=job).model_dump(mode="json")
            event_name = "done" if job.status in {TrainingJobStatus.COMPLETED, TrainingJobStatus.FAILED} else "progress"
            yield f"event: {event_name}\ndata: {json.dumps(envelope, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/projects/{project_id}/training/{job_id}/cancel", response_model=ApiResponse)
def cancel_training_job(project_id: str, job_id: str) -> ApiResponse:
    """Cancel a queued or running training job (spec §7.3 interrupt)."""
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/training/%s/cancel", project_id, job_id)
    try:
        cancelled = training_service.cancel_job(project_id, job_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} could not be cancelled (not found or not in a cancellable state).",
        )
    return success_response(MessageKey.SUCCESS_SWITCH0, {"job_id": job_id, "cancelled": True})


# ---------------------------------------------------------------------------
# §7.1.1 — CharacterSheet routes  (/api/v1/projects/{project_id}/characters)
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/characters", response_model=ApiResponse)
def list_character_sheets(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/characters", project_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    items = store.list_character_sheets(project_id)
    return success_response(MessageKey.SUCCESS_FETCH0, {"characters": [item.model_dump(mode="json") for item in items]})


@app.post("/api/v1/projects/{project_id}/characters", response_model=ApiResponse)
def create_character_sheet(project_id: str, payload: CharacterSheetCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/characters name=%s", project_id, payload.name)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.create_character_sheet(project_id, payload)
    return success_response(MessageKey.SUCCESS_ADD0, {"character": item.model_dump(mode="json")})


@app.get("/api/v1/projects/{project_id}/characters/{character_id}", response_model=ApiResponse)
def get_character_sheet(project_id: str, character_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/characters/%s", project_id, character_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.get_character_sheet(project_id, character_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Character sheet not found: {character_id}")
    return success_response(MessageKey.SUCCESS_FETCH0, {"character": item.model_dump(mode="json")})


@app.patch("/api/v1/projects/{project_id}/characters/{character_id}", response_model=ApiResponse)
def update_character_sheet(project_id: str, character_id: str, payload: CharacterSheetUpdateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/characters/%s", project_id, character_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.update_character_sheet(project_id, character_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Character sheet not found: {character_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"character": item.model_dump(mode="json")})


@app.delete("/api/v1/projects/{project_id}/characters/{character_id}", response_model=ApiResponse)
def delete_character_sheet(project_id: str, character_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("DELETE /api/v1/projects/%s/characters/%s", project_id, character_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    deleted = store.delete_character_sheet(project_id, character_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Character sheet not found: {character_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"deleted": character_id})


# ---------------------------------------------------------------------------
# §7.1.1 — DatasetPack routes  (/api/v1/projects/{project_id}/dataset-packs)
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/dataset-packs", response_model=ApiResponse)
def list_dataset_packs(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/dataset-packs", project_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    items = store.list_dataset_packs(project_id)
    return success_response(MessageKey.SUCCESS_FETCH0, {"dataset_packs": [item.model_dump(mode="json") for item in items]})


@app.post("/api/v1/projects/{project_id}/dataset-packs", response_model=ApiResponse)
def create_dataset_pack(project_id: str, payload: DatasetPackCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/dataset-packs source=%s", project_id, payload.source)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.create_dataset_pack(project_id, payload)
    return success_response(MessageKey.SUCCESS_ADD0, {"dataset_pack": item.model_dump(mode="json")})


@app.get("/api/v1/projects/{project_id}/dataset-packs/{pack_id}", response_model=ApiResponse)
def get_dataset_pack(project_id: str, pack_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/dataset-packs/%s", project_id, pack_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.get_dataset_pack(project_id, pack_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Dataset pack not found: {pack_id}")
    return success_response(MessageKey.SUCCESS_FETCH0, {"dataset_pack": item.model_dump(mode="json")})


@app.patch("/api/v1/projects/{project_id}/dataset-packs/{pack_id}", response_model=ApiResponse)
def update_dataset_pack(project_id: str, pack_id: str, payload: DatasetPackUpdateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/dataset-packs/%s", project_id, pack_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.update_dataset_pack(project_id, pack_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Dataset pack not found: {pack_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"dataset_pack": item.model_dump(mode="json")})


@app.delete("/api/v1/projects/{project_id}/dataset-packs/{pack_id}", response_model=ApiResponse)
def delete_dataset_pack(project_id: str, pack_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("DELETE /api/v1/projects/%s/dataset-packs/%s", project_id, pack_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    deleted = store.delete_dataset_pack(project_id, pack_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Dataset pack not found: {pack_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"deleted": pack_id})


# ---------------------------------------------------------------------------
# §7.1.1 — TrainingRecipe routes  (/api/v1/projects/{project_id}/training-recipes)
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/training-recipes", response_model=ApiResponse)
def list_training_recipes(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/training-recipes", project_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    items = store.list_training_recipes(project_id)
    return success_response(MessageKey.SUCCESS_FETCH0, {"training_recipes": [item.model_dump(mode="json") for item in items]})


@app.post("/api/v1/projects/{project_id}/training-recipes", response_model=ApiResponse)
def create_training_recipe(project_id: str, payload: TrainingRecipeCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/training-recipes base_model=%s", project_id, payload.base_model)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.create_training_recipe(project_id, payload)
    return success_response(MessageKey.SUCCESS_ADD0, {"training_recipe": item.model_dump(mode="json")})


@app.get("/api/v1/projects/{project_id}/training-recipes/{recipe_id}", response_model=ApiResponse)
def get_training_recipe(project_id: str, recipe_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/training-recipes/%s", project_id, recipe_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.get_training_recipe(project_id, recipe_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Training recipe not found: {recipe_id}")
    return success_response(MessageKey.SUCCESS_FETCH0, {"training_recipe": item.model_dump(mode="json")})


@app.patch("/api/v1/projects/{project_id}/training-recipes/{recipe_id}", response_model=ApiResponse)
def update_training_recipe(project_id: str, recipe_id: str, payload: TrainingRecipeUpdateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/training-recipes/%s", project_id, recipe_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.update_training_recipe(project_id, recipe_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Training recipe not found: {recipe_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"training_recipe": item.model_dump(mode="json")})


@app.delete("/api/v1/projects/{project_id}/training-recipes/{recipe_id}", response_model=ApiResponse)
def delete_training_recipe(project_id: str, recipe_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("DELETE /api/v1/projects/%s/training-recipes/%s", project_id, recipe_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    deleted = store.delete_training_recipe(project_id, recipe_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Training recipe not found: {recipe_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"deleted": recipe_id})


# ---------------------------------------------------------------------------
# §7.1.1 — LoraPreset routes  (/api/v1/projects/{project_id}/lora-presets)
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/lora-presets", response_model=ApiResponse)
def list_lora_presets(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/lora-presets", project_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    items = store.list_lora_presets(project_id)
    return success_response(MessageKey.SUCCESS_FETCH0, {"lora_presets": [item.model_dump(mode="json") for item in items]})


@app.post("/api/v1/projects/{project_id}/lora-presets", response_model=ApiResponse)
def create_lora_preset(project_id: str, payload: LoraPresetCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/lora-presets name=%s", project_id, payload.name)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.create_lora_preset(project_id, payload)
    return success_response(MessageKey.SUCCESS_ADD0, {"lora_preset": item.model_dump(mode="json")})


@app.get("/api/v1/projects/{project_id}/lora-presets/{preset_id}", response_model=ApiResponse)
def get_lora_preset(project_id: str, preset_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/lora-presets/%s", project_id, preset_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.get_lora_preset(project_id, preset_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"LoRA preset not found: {preset_id}")
    return success_response(MessageKey.SUCCESS_FETCH0, {"lora_preset": item.model_dump(mode="json")})


@app.patch("/api/v1/projects/{project_id}/lora-presets/{preset_id}", response_model=ApiResponse)
def update_lora_preset(project_id: str, preset_id: str, payload: LoraPresetUpdateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/lora-presets/%s", project_id, preset_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.update_lora_preset(project_id, preset_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail=f"LoRA preset not found: {preset_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"lora_preset": item.model_dump(mode="json")})


@app.delete("/api/v1/projects/{project_id}/lora-presets/{preset_id}", response_model=ApiResponse)
def delete_lora_preset(project_id: str, preset_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("DELETE /api/v1/projects/%s/lora-presets/%s", project_id, preset_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    deleted = store.delete_lora_preset(project_id, preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"LoRA preset not found: {preset_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"deleted": preset_id})


# ---------------------------------------------------------------------------
# §7.1.1 — ImageToVideoRecipe routes  (/api/v1/projects/{project_id}/i2v-recipes)
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/i2v-recipes", response_model=ApiResponse)
def list_i2v_recipes(project_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/i2v-recipes", project_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    items = store.list_i2v_recipes(project_id)
    return success_response(MessageKey.SUCCESS_FETCH0, {"i2v_recipes": [item.model_dump(mode="json") for item in items]})


@app.post("/api/v1/projects/{project_id}/i2v-recipes", response_model=ApiResponse)
def create_i2v_recipe(project_id: str, payload: ImageToVideoRecipeCreateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/i2v-recipes name=%s", project_id, payload.name)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.create_i2v_recipe(project_id, payload)
    return success_response(MessageKey.SUCCESS_ADD0, {"i2v_recipe": item.model_dump(mode="json")})


@app.get("/api/v1/projects/{project_id}/i2v-recipes/{recipe_id}", response_model=ApiResponse)
def get_i2v_recipe(project_id: str, recipe_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/i2v-recipes/%s", project_id, recipe_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.get_i2v_recipe(project_id, recipe_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Image-to-video recipe not found: {recipe_id}")
    return success_response(MessageKey.SUCCESS_FETCH0, {"i2v_recipe": item.model_dump(mode="json")})


@app.patch("/api/v1/projects/{project_id}/i2v-recipes/{recipe_id}", response_model=ApiResponse)
def update_i2v_recipe(project_id: str, recipe_id: str, payload: ImageToVideoRecipeUpdateRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("PATCH /api/v1/projects/%s/i2v-recipes/%s", project_id, recipe_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    item = store.update_i2v_recipe(project_id, recipe_id, payload)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Image-to-video recipe not found: {recipe_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"i2v_recipe": item.model_dump(mode="json")})


@app.delete("/api/v1/projects/{project_id}/i2v-recipes/{recipe_id}", response_model=ApiResponse)
def delete_i2v_recipe(project_id: str, recipe_id: str) -> ApiResponse:
    if IS_DEV:
        logger.info("DELETE /api/v1/projects/%s/i2v-recipes/%s", project_id, recipe_id)
    try:
        store = _asset_store(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    deleted = store.delete_i2v_recipe(project_id, recipe_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Image-to-video recipe not found: {recipe_id}")
    return success_response(MessageKey.SUCCESS_SWITCH0, {"deleted": recipe_id})


@app.post("/api/v1/workers/{worker_name}/install", response_model=ApiResponse)
def install_worker(worker_name: str) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/workers/%s/install", worker_name)
    try:
        payload = workers_service.install_worker(worker_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    invalidate_integration_snapshot_cache()
    return success_response(MessageKey.SUCCESS_ADD0, payload.model_dump())


@app.post("/api/v1/workers/{worker_name}/start", response_model=ApiResponse)
def start_worker(worker_name: str) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/workers/%s/start", worker_name)
    try:
        payload = workers_service.start_worker(worker_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    invalidate_integration_snapshot_cache()
    return success_response(MessageKey.SUCCESS_SWITCH0, payload.model_dump())


@app.post("/api/v1/workers/{worker_name}/stop", response_model=ApiResponse)
def stop_worker(worker_name: str) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/workers/%s/stop", worker_name)
    try:
        payload = workers_service.stop_worker(worker_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    invalidate_integration_snapshot_cache()
    return success_response(MessageKey.SUCCESS_SWITCH0, payload.model_dump())


@app.post("/api/v1/workers/{worker_name}/smoke", response_model=ApiResponse)
def smoke_worker(worker_name: str) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/workers/%s/smoke", worker_name)
    try:
        payload = workers_service.smoke_test_worker(worker_name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    invalidate_integration_snapshot_cache()
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.get("/api/v1/llm/local", response_model=ApiResponse)
def local_llm_status() -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/llm/local")
    payload = LocalLlmStatus(**local_llm_manager.status(settings))
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump())


@app.post("/api/v1/llm/local/start", response_model=ApiResponse)
def start_local_llm() -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/llm/local/start")
    try:
        payload = LocalLlmStatus(**local_llm_manager.start(settings))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    invalidate_integration_snapshot_cache()
    return success_response(MessageKey.SUCCESS_SWITCH0, payload.model_dump())


@app.post("/api/v1/llm/local/download", response_model=ApiResponse)
def download_local_llm_model(payload: ModelDownloadRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/llm/local/download")
    try:
        result = ModelDownloadResult(**local_llm_manager.download_model(settings, payload.url))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    invalidate_integration_snapshot_cache()
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump())


# ---------------------------------------------------------------------------
# M5.3 — Cross-project reference routes (§5.6.2 / §5.6.5 / §16 Q4)
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/refs", response_model=ApiResponse)
def list_project_refs(project_id: str) -> ApiResponse:
    """List all cross-project references in a project with their resolved statuses (§5.6.2 / §5.6.3).

    Also runs cycle detection (§5.6.5) and includes any cycle warnings in the response.
    The frontend can use this data to render ref-status badges.
    """
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/refs", project_id)
    try:
        _, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    refs = collect_project_refs(project_dir)
    resolved: list[CrossRefEntry] = []
    for ref in refs:
        result = resolve_reference(ref, project_dir, PROJECTS_ROOT)
        resolved.append(CrossRefEntry(
            ref=ref,
            status=CrossRefStatus(result["status"].value if hasattr(result["status"], "value") else result["status"]),
            path=str(result["path"]) if result["path"] else None,
            hash=result.get("hash"),
            origin_hash=result.get("origin_hash"),
            message=result.get("message", ""),
        ))

    # Cycle detection (§5.6.5) — warning only, never an error
    cycles = detect_cycles(project_id, PROJECTS_ROOT)

    payload = CrossRefListData(
        project_id=project_id,
        refs=resolved,
        cycle_warning=cycles,
    )
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.get("/api/v1/projects/{project_id}/refs/{asset_id}", response_model=ApiResponse)
def list_asset_refs(project_id: str, asset_id: str) -> ApiResponse:
    """List cross-project references for a specific asset (by asset id).

    Scans the asset's dependencies field in assets/index.json.
    Returns resolved statuses for each ref found on that asset.
    """
    if IS_DEV:
        logger.info("GET /api/v1/projects/%s/refs/%s", project_id, asset_id)
    try:
        _, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    import json as _json
    index_path = project_dir / "assets" / "index.json"
    deps: list[str] = []
    if index_path.is_file():
        try:
            data = _json.loads(index_path.read_text(encoding="utf-8"))
            for asset in data.get("assets", []):
                if asset.get("id") == asset_id:
                    deps = [d for d in asset.get("dependencies", []) if d.startswith("@")]
                    break
        except (Exception,):
            pass

    resolved: list[CrossRefEntry] = []
    for ref in deps:
        result = resolve_reference(ref, project_dir, PROJECTS_ROOT)
        resolved.append(CrossRefEntry(
            ref=ref,
            status=CrossRefStatus(result["status"].value if hasattr(result["status"], "value") else result["status"]),
            path=str(result["path"]) if result["path"] else None,
            hash=result.get("hash"),
            origin_hash=result.get("origin_hash"),
            message=result.get("message", ""),
        ))

    payload = CrossRefListData(project_id=project_id, refs=resolved)
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/refs/materialize", response_model=ApiResponse)
def materialize_project_references(project_id: str, payload: MaterializeRequest) -> ApiResponse:
    """Materialize cross-project references into local asset copies (§16 Q4).

    This is an EXPLICIT / OPT-IN operation — never triggered automatically.
    For each resolved ref, copies the referenced asset file into _external/,
    updates origins.json with provenance metadata, and records the original
    ref for audit.

    Broken refs are reported in the response rather than causing a 4xx error,
    so bulk materialization can continue past individual failures.
    """
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/refs/materialize", project_id)
    try:
        _, project_dir = project_manager.get_project(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    result = materialize_project_refs(
        project_dir,
        PROJECTS_ROOT,
        refs=payload.refs,
    )

    materialized = [
        MaterializeResultEntry(
            ref=r["ref"],
            status=r["status"],
            local_path=str(r["local_path"]) if r.get("local_path") else None,
            provenance=r.get("provenance"),
            message=r.get("message", ""),
        )
        for r in result["materialized"]
    ]
    broken = [
        MaterializeResultEntry(
            ref=r["ref"],
            status=r["status"],
            local_path=None,
            provenance=None,
            message=r.get("message", ""),
        )
        for r in result["broken"]
    ]

    data = MaterializeData(
        project_id=project_id,
        materialized=materialized,
        broken=broken,
        total=result["total"],
    )
    return success_response(MessageKey.SUCCESS_ADD0, data.model_dump(mode="json"))


@app.post("/api/v1/projects/import", response_model=ApiResponse)
async def import_project(
    file: UploadFile = File(...),
) -> ApiResponse:
    """Import a project from a *.misaka.zip archive (spec §5.5).

    The uploaded zip must contain export.manifest.json produced by the export
    endpoint. Zip-slip, manifest schema, and size sanity checks are enforced.
    If a project with the same id or name already exists, the import is assigned
    a new id and the original id is recorded as origin_id in project.json.
    """
    if IS_DEV:
        logger.info("POST /api/v1/projects/import filename=%s", file.filename)

    # Validate filename extension early — reject anything that is not a .zip.
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=400,
            detail="Only .zip files are accepted for project import.",
        )

    import tempfile

    # Stream upload to a temp file in chunks; enforce a raw-size cap to prevent
    # memory exhaustion before the uncompressed-size guard in import_project_zip.
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            received_bytes = 0
            while True:
                chunk = await file.read(_UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                received_bytes += len(chunk)
                if received_bytes > _UPLOAD_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Upload exceeds maximum allowed size of "
                            f"{_UPLOAD_MAX_BYTES // (1024 ** 2):,} MiB."
                        ),
                    )
                tmp.write(chunk)

        try:
            result = import_project_zip(tmp_path, PROJECTS_ROOT)
        except ZipImportError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    if IS_DEV:
        logger.info(
            "Project import complete: id=%s collision_resolved=%s",
            result["project_id"],
            result["collision_resolved"],
        )

    return success_response(MessageKey.SUCCESS_ADD0, result)
