import json
import logging
import threading
import time
from pathlib import Path

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
from core.llm.router import list_providers
from core.llm.service import optimize_synopsis as llm_optimize_synopsis
from core.models.schemas import (
    ApiErrorResponse,
    ApiResponse,
    BatchExecuteRequest,
    ClarifyRequest,
    ConversationHistoryData,
    HealthData,
    IntegrationSnapshot,
    JobExecutionPatch,
    LocalLlmStatus,
    MessageKey,
    ModelDownloadRequest,
    ModelDownloadResult,
    Modality,
    ProjectLicenseReport,
    ProjectWorkspaceData,
    ProjectListData,
    ProjectVersionGraph,
    ProjectSelectRequest,
    SynopsisOptimizeRequest,
    TrainingJobCreateRequest,
    TrainingWorkspaceData,
    WorkerSmokeResult,
)
from core.network.service import NetworkStateService
from core.project.export import ProjectExportService
from core.project.manager import (
    PROJECT_TYPES,
    ProjectConflictError,
    ProjectCreateRequest,
    ProjectManager,
    ProjectNotFoundError,
    ProjectValidationError,
)
from core.reporting.license import LicenseReportService
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
consultant_engine = ConsultantEngine()
tools_service = ToolsService(REPO_ROOT / "tools" / "manifest.json")
workers_service = WorkersService(REPO_ROOT / "workers" / "manifest.json")
model_registry_service = ModelRegistryService(REPO_ROOT / "core" / "models" / "registry.json")
local_llm_manager = LocalLlmManager()
generation_service = GenerationService(project_manager, workers_service)
network_state_service = NetworkStateService()
project_export_service = ProjectExportService()
license_report_service = LicenseReportService()
training_service = TrainingService(project_manager)
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
        result = generation_service.execute_ready_jobs(project_id, payload.job_ids)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
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
        payload = IntegrationSnapshot(
            tools=tools_service.list_tools(),
            workers=workers_service.list_workers(refresh=True),
            providers=list_providers(settings),
            registry_categories=model_registry_service.list_categories(),
            model_search_paths=settings.model_search_paths,
            network=network_state_service.snapshot(
                settings.misaka_network_mode,
                [
                    settings.anthropic_api_base_url,
                    settings.openai_api_base_url,
                    settings.gemini_api_base_url,
                ],
            ),
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
    try:
        result = training_service.submit_job(project_id, payload)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump(mode="json"))


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
