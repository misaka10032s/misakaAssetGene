from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from core.network.state import NetworkMode
from core.scheduler.vram import RuntimeState


class MessageKey(str, Enum):
    SUCCESS_ADD0 = "message.success.add0"
    SUCCESS_FETCH0 = "message.success.fetch0"
    SUCCESS_SWITCH0 = "message.success.switch0"
    FAIL_400 = "message.fail.400"
    FAIL_401 = "message.fail.401"
    FAIL_404 = "message.fail.404"
    FAIL_409 = "message.fail.409"
    FAIL_500 = "message.fail.500"


class ProjectTypeSuggestion(str, Enum):
    RPG = "RPG"
    FPS = "FPS"
    PUZZLE = "Puzzle"
    VN = "VN"
    ANIME = "Anime"
    PLATFORMER = "Platformer"
    OTHER = "Other"


class Modality(str, Enum):
    TEXT = "text"
    MUSIC = "music"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"


class ProviderName(str, Enum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


class ProviderMode(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class ProviderStatus(str, Enum):
    READY = "ready"
    CONFIGURED = "configured"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class ApiResponse(BaseModel):
    message: MessageKey
    data: Any | None = None


class ApiErrorResponse(BaseModel):
    message: MessageKey
    detail: Any | None = None


class HealthData(BaseModel):
    status: str
    repo_root: str
    environment: str


class ClarifyRequest(BaseModel):
    modality: Modality | None = None
    prompt: str = Field(min_length=1)
    project_name: str = ""
    project_type: str = ""
    project_synopsis: str = ""


class ToolSnapshot(BaseModel):
    name: str
    version: str


class WorkerSnapshot(BaseModel):
    name: str
    display_name: str
    repo: str
    path: str
    recommended_reference: str
    installed_reference: str | None = None
    health_check: str | None = None
    is_installed: bool
    is_running: bool
    managed_pid: int | None = None
    vram_requirement_mb: int = 0
    runtime_state: RuntimeState = RuntimeState.COLD
    last_job_at: datetime | None = None
    readiness_note: str | None = None


class NetworkSnapshot(BaseModel):
    mode: NetworkMode
    reachable: bool
    summary: str


class ProviderSnapshot(BaseModel):
    name: ProviderName
    mode: ProviderMode
    status: ProviderStatus
    configured: bool
    base_url: str


class IntegrationSnapshot(BaseModel):
    tools: list[ToolSnapshot]
    workers: list[WorkerSnapshot]
    providers: list[ProviderSnapshot]
    registry_categories: list[str]
    model_search_paths: list[str]
    network: NetworkSnapshot


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    synopsis: str = ""


class ProjectSelectRequest(BaseModel):
    project_id: str = Field(min_length=1)


class ProjectSummary(BaseModel):
    id: str
    name: str
    type: str
    synopsis: str = ""


class ProjectListData(BaseModel):
    projects: list[ProjectSummary]
    current_project_id: str | None = None


class ConversationEntry(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
    modality: Modality | None = None
    questions: list[str] = Field(default_factory=list)
    analysis: "ConsultantAnalysis | None" = None


class ConversationHistoryData(BaseModel):
    entries: list[ConversationEntry]
    total: int
    offset: int
    limit: int
    has_more: bool


class SynopsisOptimizeRequest(BaseModel):
    project_name: str = Field(min_length=1)
    project_type: str = Field(min_length=1)
    synopsis: str = Field(min_length=1)


class SynopsisOptimizeResult(BaseModel):
    optimized_synopsis: str
    strategy: str
    provider: ProviderName | None = None


class LocalLlmStatus(BaseModel):
    server: str
    base_url: str
    is_running: bool
    managed_by_app: bool
    executable_found: bool
    executable_path: str | None = None
    provider_order: list[str]


class ModelDownloadRequest(BaseModel):
    url: str = Field(min_length=1)


class ModelDownloadResult(BaseModel):
    filename: str
    saved_path: str
    source_url: str


class ConsultantPlanStep(BaseModel):
    title: str
    detail: str
    worker: str | None = None


class ConsultantDeliverable(BaseModel):
    modality: Modality
    asset_type: str
    title: str
    variants: list[str] = Field(default_factory=list)
    worker: str | None = None


class ConsultantAnalysis(BaseModel):
    objective: str
    inferred_modalities: list[Modality] = Field(default_factory=list)
    franchise: str | None = None
    characters: list[str] = Field(default_factory=list)
    outfits: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    matrix_axes: list[str] = Field(default_factory=list)
    required_research: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    recommended_workers: list[str] = Field(default_factory=list)
    deliverables: list[ConsultantDeliverable] = Field(default_factory=list)
    execution_steps: list[ConsultantPlanStep] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    guidance_path: list[str] = Field(default_factory=list)


class ClarifyResult(BaseModel):
    modality: Modality
    summary: str
    questions: list[str] = Field(default_factory=list)
    template_loaded: bool
    next_step: str
    analysis: ConsultantAnalysis | None = None


class GenerationJobStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    BLOCKED = "blocked"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerationRecipe(str, Enum):
    AUTO = "auto"
    TXT2IMG = "txt2img"
    IMG2IMG = "img2img"
    INPAINT = "inpaint"


class GenerationJob(BaseModel):
    id: str
    project_id: str
    title: str
    modality: Modality
    asset_type: str
    status: GenerationJobStatus
    prompt: str
    summary: str
    worker: str | None = None
    variants: list[str] = Field(default_factory=list)
    recipe: GenerationRecipe | None = None
    source_asset_id: str | None = None
    mask_asset_id: str | None = None
    blocking_reason: str | None = None
    last_error: str | None = None
    progress: int = 0
    progress_label: str | None = None
    search_queries: list[str] = Field(default_factory=list)
    steps: list[ConsultantPlanStep] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AssetRecord(BaseModel):
    id: str
    job_id: str | None = None
    modality: Modality
    asset_type: str
    title: str
    path: str
    description: str = ""
    created_at: datetime


class ProjectWorkspaceData(BaseModel):
    jobs: list[GenerationJob] = Field(default_factory=list)
    assets: list[AssetRecord] = Field(default_factory=list)
    plans: list["ConsultantPlanRecord"] = Field(default_factory=list)


class JobExecutionPatch(BaseModel):
    worker: str | None = None
    recipe: GenerationRecipe | None = None
    source_asset_id: str | None = None
    mask_asset_id: str | None = None


class BatchExecuteRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class WorkerSmokeResult(BaseModel):
    worker_name: str
    ok: bool
    detail: str
    checked_at: datetime


class ConsultantPlanRecord(BaseModel):
    id: str
    title: str
    path: str
    summary: str
    prompt: str
    modalities: list[Modality] = Field(default_factory=list)
    created_at: datetime


class LicenseReportEntry(BaseModel):
    worker_name: str
    display_name: str
    repo: str
    recommended_reference: str
    installed_reference: str | None = None
    license: str | None = None
    commercial: str | None = None
    job_count: int = 0
    asset_count: int = 0
    modalities: list[str] = Field(default_factory=list)
    readiness_note: str | None = None


class ProjectLicenseReport(BaseModel):
    project_id: str
    project_name: str
    generated_at: datetime | None = None
    entries: list[LicenseReportEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProjectVersionNode(BaseModel):
    id: str
    title: str
    node_type: str
    modality: Modality
    status: str
    worker: str | None = None
    created_at: datetime


class ProjectVersionEdge(BaseModel):
    source: str
    target: str
    relation: str


class ProjectVersionGraph(BaseModel):
    nodes: list[ProjectVersionNode] = Field(default_factory=list)
    edges: list[ProjectVersionEdge] = Field(default_factory=list)


class TrainingJobStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TrainingJobCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    modality: Modality
    dataset_path: str = Field(min_length=1)
    worker: str | None = None


class TrainingJob(BaseModel):
    id: str
    project_id: str
    title: str
    modality: Modality
    worker: str
    dataset_path: str
    status: TrainingJobStatus
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class TrainingWorkspaceData(BaseModel):
    jobs: list[TrainingJob] = Field(default_factory=list)


ConversationEntry.model_rebuild()
ProjectWorkspaceData.model_rebuild()
