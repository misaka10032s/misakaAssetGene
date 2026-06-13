import json
import logging
import threading
import time
from pathlib import Path

# Maximum allowed upload size for project zip imports (spec §5.5 streaming guard).
# Must be checked BEFORE reading into memory; this caps the raw compressed bytes.
_UPLOAD_MAX_BYTES: int = 2 * 1024 ** 3  # 2 GiB
_UPLOAD_CHUNK_SIZE: int = 256 * 1024     # 256 KiB per read chunk

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.exceptions import RequestValidationError

from core.config import get_settings
from core.consultant.engine import ConsultantEngine
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
    DatasetPack,
    DatasetPackCreateRequest,
    DatasetPackUpdateRequest,
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
    MessageKey,
    ModelDownloadRequest,
    ModelDownloadResult,
    Modality,
    ProjectLicenseReport,
    ProjectWorkspaceData,
    ProjectListData,
    ProjectVersionGraph,
    ProjectSelectRequest,
    RefineRequest,
    SynopsisOptimizeRequest,
    TrainingJobCreateRequest,
    TrainingJobPollData,
    TrainingRecipe,
    TrainingRecipeCreateRequest,
    TrainingRecipeUpdateRequest,
    TrainingWorkspaceData,
    VersionDiffRequest,
    WorkerSmokeResult,
)
from core.network.service import NetworkStateService
from core.project.export import ProjectExportService
from core.project.portability import ZipImportError, import_project_zip
from core.project.manager import (
    PROJECT_TYPES,
    ProjectConflictError,
    ProjectCreateRequest,
    ProjectManager,
    ProjectNotFoundError,
    ProjectValidationError,
)
from core.reporting.license import LicenseReportService
from core.scheduler.vram import ModelScheduler, SchedulerBudget
from core.training.asset_store import AssetStore
from core.training.executor import SubprocessRunner, TrainingExecutor
from core.training.service import TrainingService

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_ROOT = REPO_ROOT / "projects"
settings = get_settings()
APP_ENV = settings.misaka_env.lower()
IS_DEV = settings.is_dev

logging.basicConfig(
    level=logging.INFO if IS_DEV else logging.WARNING,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("misaka.core")

app = FastAPI(title="MisakaAssetGene Core Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.misaka_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        logger.info("CORS origin regex: %s", settings.misaka_cors_origin_regex)
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
        project, _ = project_manager.get_project(project_id)
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
        project, _ = project_manager.get_project(project_id)
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
    return success_response(MessageKey.SUCCESS_ADD0, session_data.model_dump(mode="json"))


@app.post("/api/v1/projects/{project_id}/consultant/session/advance", response_model=ApiResponse)
def advance_consultant_session(project_id: str, payload: ConsultantSessionAdvanceRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/projects/%s/consultant/session/advance id=%s", project_id, payload.session_id)
    try:
        project, _ = project_manager.get_project(project_id)
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
