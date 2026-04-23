import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from core.config import get_settings
from core.consultant.engine import ConsultantEngine
from core.integration.model_registry import ModelRegistryService
from core.integration.tools import ToolsService
from core.integration.workers import WorkersService
from core.llm.local_manager import LocalLlmManager
from core.llm.router import list_providers
from core.llm.service import optimize_synopsis as llm_optimize_synopsis
from core.models.schemas import (
    ApiErrorResponse,
    ApiResponse,
    ClarifyRequest,
    HealthData,
    IntegrationSnapshot,
    LocalLlmStatus,
    MessageKey,
    ModelDownloadRequest,
    ModelDownloadResult,
    ProjectListData,
    ProjectSelectRequest,
    SynopsisOptimizeRequest,
)
from core.project.manager import (
    PROJECT_TYPES,
    ProjectConflictError,
    ProjectCreateRequest,
    ProjectManager,
    ProjectNotFoundError,
    ProjectValidationError,
)

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


def success_response(message: MessageKey, data: object) -> ApiResponse:
    return ApiResponse(message=message, data=data)


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
        logger.info("Loaded %d worker definitions", len(workers_service.list_workers()))
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
        current_project=project_manager.get_current_project_name(),
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
        logger.info("POST /api/v1/projects/select name=%s", payload.name)
    try:
        project = project_manager.select_project(payload.name)
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
    result = llm_optimize_synopsis(settings, payload.project_name, payload.project_type, payload.synopsis)
    return success_response(MessageKey.SUCCESS_FETCH0, result.model_dump())


@app.post("/api/v1/consultant/clarify", response_model=ApiResponse)
def clarify(payload: ClarifyRequest) -> ApiResponse:
    if IS_DEV:
        logger.info("POST /api/v1/consultant/clarify modality=%s", payload.modality.value)
    return success_response(MessageKey.SUCCESS_FETCH0, consultant_engine.clarify(payload))


@app.get("/api/v1/integration", response_model=ApiResponse)
def integration_snapshot() -> ApiResponse:
    if IS_DEV:
        logger.info("GET /api/v1/integration")
    payload = IntegrationSnapshot(
        tools=tools_service.list_tools(),
        workers=workers_service.list_workers(),
        providers=list_providers(settings),
        registry_categories=model_registry_service.list_categories(),
        model_search_paths=settings.model_search_paths,
    )
    return success_response(MessageKey.SUCCESS_FETCH0, payload.model_dump())


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
    return success_response(MessageKey.SUCCESS_ADD0, result.model_dump())
