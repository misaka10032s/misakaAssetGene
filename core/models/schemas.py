from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from core.network.state import NetworkMode, NetworkState
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


class NetworkTransition(BaseModel):
    mode: NetworkMode
    from_state: NetworkState | None = None
    to_state: NetworkState
    at: datetime


class NetworkSnapshot(BaseModel):
    mode: NetworkMode
    state: NetworkState
    reachable: bool
    local_available: bool = False
    summary: str
    recent_transitions: list[NetworkTransition] = Field(default_factory=list)


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


class ConsultantState(str, Enum):
    """Explicit consultant dialog lifecycle states (spec §4.1)."""

    INTAKE = "intake"
    CLARIFY = "clarify"
    SUMMARY = "summary"
    GENERATE = "generate"
    REFINE = "refine"
    ACCEPT = "accept"


class ConsultantSession(BaseModel):
    """Server-side persisted consultant session (spec §4.1.1)."""

    session_id: str
    project_id: str
    modality: Modality | None = None
    state: ConsultantState = ConsultantState.INTAKE
    checklist_status: dict[str, bool] = Field(default_factory=dict)
    slots: dict[str, Any] = Field(default_factory=dict)
    plan: ConsultantAnalysis | None = None
    last_result: ClarifyResult | None = None
    created_at: datetime
    updated_at: datetime


class ConsultantSessionStartRequest(BaseModel):
    session_id: str | None = None
    modality: Modality | None = None
    prompt: str = Field(min_length=1)


class ConsultantSessionAdvanceRequest(BaseModel):
    session_id: str = Field(min_length=1)
    prompt: str = ""
    slots: dict[str, Any] = Field(default_factory=dict)
    accept: bool = False


class ConsultantSessionData(BaseModel):
    session: ConsultantSession
    result: ClarifyResult | None = None
    missing_slots: list[str] = Field(default_factory=list)


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


class RefineStrategy(str, Enum):
    """Image/video refine strategy ladder (spec §6.2 decision tree).

    Ordered from the cheapest, least destructive fix to the most expensive
    full regeneration. The planner always selects the *minimal but sufficient*
    rung for a given refine request.
    """

    METADATA_ONLY = "metadata_only"
    PARAM_RETUNE = "param_retune"
    IMG2IMG = "img2img"
    INPAINT = "inpaint"
    FULL_REGEN = "full_regen"


class PromptDecompositionPass(str, Enum):
    """Multi-stage refine passes for image generation (spec §5.11)."""

    BASE_COMPOSITION = "base_composition"
    CHARACTER_DETAIL = "character_detail"
    PROP_ACCESSORY = "prop_accessory"
    FINAL_POLISH = "final_polish"


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
    # Refine lineage (spec §5.11 / §8.1): the parent asset version this job
    # refines, plus the strategy chosen by the §6.2 decision tree.
    parent_asset_id: str | None = None
    refine_strategy: RefineStrategy | None = None
    # Tunable sampler/recipe params threaded into the adapter (spec §6.2).
    params: dict[str, Any] = Field(default_factory=dict)
    # Per-refine record of why this method was chosen and what changed
    # relative to the parent version (spec §5.11 / §6.2 last paragraph).
    refine_reason: str | None = None
    prompt_delta: str | None = None
    param_delta: dict[str, Any] = Field(default_factory=dict)
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
    # Refine lineage (spec §5.11 / §8.1 versions.parent_version_id): null = root.
    parent_version_id: str | None = None
    refine_strategy: RefineStrategy | None = None
    mask_asset_id: str | None = None
    prompt_delta: str | None = None
    param_delta: dict[str, Any] = Field(default_factory=dict)
    prompt_hash: str | None = None
    backend: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # Metadata fields (spec §6.2 metadata-only tier). All optional / backward-compatible.
    tags: list[str] = Field(default_factory=list)
    user_note: str | None = None
    is_favorite: bool = False
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


class SkippedJobInfo(BaseModel):
    """One entry per job silently skipped during a batch-execute (spec §5.14)."""

    job_id: str
    title: str
    reason: str


class BatchExecuteData(BaseModel):
    """Response envelope for execute-ready (spec §5.14 batch honesty).

    ``workspace`` carries the refreshed jobs/assets/plans identical to the
    existing ProjectWorkspaceData.  ``executed_count`` and ``skipped`` surface
    any jobs that were not attempted, so the frontend can show a truthful
    summary (e.g. "executed 2, skipped 1: reason…") instead of silently
    swallowing blocked jobs.
    """

    workspace: ProjectWorkspaceData
    executed_count: int = 0
    skipped: list[SkippedJobInfo] = Field(default_factory=list)


class RefineRequest(BaseModel):
    """Request to refine an existing image asset version (spec §5.11 / §6.2).

    ``strategy`` may be omitted to let the §6.2 decision tree auto-select the
    minimal sufficient rung from the natural-language ``instruction`` and the
    optional explicit ``params`` / ``mask_asset_id`` signals.
    """

    instruction: str = Field(min_length=1)
    strategy: RefineStrategy | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    mask_asset_id: str | None = None
    title: str | None = None


class PromptDecompositionStep(BaseModel):
    """One stage of a multi-stage refine prompt (spec §5.11)."""

    stage: PromptDecompositionPass
    prompt: str


class RefinePlan(BaseModel):
    """Resolved refine plan emitted by the §6.2 decision tree."""

    strategy: RefineStrategy
    recipe: GenerationRecipe | None = None
    reason: str
    params: dict[str, Any] = Field(default_factory=dict)
    param_delta: dict[str, Any] = Field(default_factory=dict)
    prompt_delta: str
    decomposition: list[PromptDecompositionStep] = Field(default_factory=list)
    requires_mask: bool = False


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


# ---------------------------------------------------------------------------
# §7.1.1 — Character sheet / dataset pack / training recipe / LoRA preset
# ---------------------------------------------------------------------------

class CharacterSheet(BaseModel):
    """Spec §7.1.1 — character identity record for multi-character LoRA workflows."""

    id: str
    project_id: str
    name: str  # character name
    visual_anchors: list[str] = Field(default_factory=list)  # visual anchors
    trigger_words: list[str] = Field(default_factory=list)   # trigger words
    forbidden_features: list[str] = Field(default_factory=list)  # forbidden features
    reference_image_refs: list[str] = Field(default_factory=list)  # reference images (relative paths)
    created_at: datetime
    updated_at: datetime


class CharacterSheetCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    visual_anchors: list[str] = Field(default_factory=list)
    trigger_words: list[str] = Field(default_factory=list)
    forbidden_features: list[str] = Field(default_factory=list)
    reference_image_refs: list[str] = Field(default_factory=list)


class CharacterSheetUpdateRequest(BaseModel):
    name: str | None = None
    visual_anchors: list[str] | None = None
    trigger_words: list[str] | None = None
    forbidden_features: list[str] | None = None
    reference_image_refs: list[str] | None = None


class DatasetPack(BaseModel):
    """Spec §7.1.1 — dataset collection record for LoRA training."""

    id: str
    project_id: str
    source: str  # collection source
    cleaning_status: str  # cleaning status (e.g. raw / cleaned / tagged)
    tags: list[str] = Field(default_factory=list)
    license: str = ""  # license
    split_strategy: str = ""  # split strategy
    members: list[str] = Field(default_factory=list)  # file refs / member list
    created_at: datetime
    updated_at: datetime


class DatasetPackCreateRequest(BaseModel):
    source: str = Field(min_length=1)
    cleaning_status: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    license: str = ""
    split_strategy: str = ""
    members: list[str] = Field(default_factory=list)


class DatasetPackUpdateRequest(BaseModel):
    source: str | None = None
    cleaning_status: str | None = None
    tags: list[str] | None = None
    license: str | None = None
    split_strategy: str | None = None
    members: list[str] | None = None


class TrainingRecipe(BaseModel):
    """Spec §7.1.1 — hyperparameter recipe for LoRA / fine-tune runs."""

    id: str
    project_id: str
    base_model: str  # base model
    rank: int  # LoRA rank
    epochs: int
    optimizer: str  # e.g. AdamW8bit
    caption_strategy: str  # e.g. wd14 / blip / manual
    created_at: datetime
    updated_at: datetime


class TrainingRecipeCreateRequest(BaseModel):
    base_model: str = Field(min_length=1)
    rank: int = Field(ge=1)
    epochs: int = Field(ge=1)
    optimizer: str = Field(min_length=1)
    caption_strategy: str = Field(min_length=1)


class TrainingRecipeUpdateRequest(BaseModel):
    base_model: str | None = None
    rank: int | None = Field(default=None, ge=1)
    epochs: int | None = Field(default=None, ge=1)
    optimizer: str | None = None
    caption_strategy: str | None = None


class LoraLayer(BaseModel):
    """A single LoRA layer in a LoraPreset stack."""

    kind: str  # e.g. character / costume / style
    lora_ref: str  # path or identifier
    weight: float = 1.0


class LoraPreset(BaseModel):
    """Spec §7.1.1 — named stack of LoRA layers (character/costume/style combos)."""

    id: str
    project_id: str
    name: str
    layers: list[LoraLayer] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LoraPresetCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    layers: list[LoraLayer] = Field(default_factory=list)


class LoraPresetUpdateRequest(BaseModel):
    name: str | None = None
    layers: list[LoraLayer] | None = None


# ---------------------------------------------------------------------------
# §7.1.1 — ImageToVideoRecipe (fifth entity: image-to-video reusable recipe)
# ---------------------------------------------------------------------------

class ImageToVideoRecipe(BaseModel):
    """Spec §7.1.1 — reusable recipe template for image-to-video workflows.

    The source accepted-image is supplied at apply-time, NOT stored here —
    this is a reusable template, consistent with TrainingRecipe not binding a dataset.
    Targets ComfyUI AnimateDiff / SVD workflows (spec §6.1 / §7.1.1 line 963).
    """

    id: str
    project_id: str
    name: str                    # human-readable recipe name
    workflow_kind: str           # e.g. animatediff / svd / image-to-video
    frames: int                  # total frames to generate
    fps: int                     # output frames per second
    motion_strength: float       # motion module strength (0.0–1.0 typical range)
    notes: str = ""              # optional usage notes
    created_at: datetime
    updated_at: datetime


class ImageToVideoRecipeCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    workflow_kind: str = Field(min_length=1)
    frames: int = Field(ge=1)
    fps: int = Field(ge=1)
    motion_strength: float = Field(ge=0.0)
    notes: str = ""


class ImageToVideoRecipeUpdateRequest(BaseModel):
    name: str | None = None
    workflow_kind: str | None = None
    frames: int | None = Field(default=None, ge=1)
    fps: int | None = Field(default=None, ge=1)
    motion_strength: float | None = Field(default=None, ge=0.0)
    notes: str | None = None


ConversationEntry.model_rebuild()
ProjectWorkspaceData.model_rebuild()
