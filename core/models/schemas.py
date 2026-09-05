from datetime import datetime
from enum import Enum
from typing import Any, Literal

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
    TRAINING = "training"  # spec §7.1.1 multi-character LoRA / voice-clone workflow


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
    # Spec §5.15 / C-spec.md §5 — per-project default for
    # FidelityLoopStartRequest.auto_continue when the caller omits it.
    # Persisted in project.json; changed via PATCH .../settings. Setting it
    # never itself starts or auto-continues a loop — it only supplies a
    # default the NEXT explicit start request may still override.
    auto_loop_enabled: bool = False


class ProjectSettingsUpdateRequest(BaseModel):
    """``PATCH /api/v1/projects/{project_id}/settings`` body (spec §5).

    Every field is optional (``None`` = "leave unchanged") so this request
    shape can grow additional project settings later without breaking a
    caller that only wants to touch one of them.
    """

    auto_loop_enabled: bool | None = None


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


class TrainingSuggestionCard(BaseModel):
    """Backend data shape for a suggestion card the frontend renders as an inline
    create action (spec §4.4 / M4.b).

    The card describes a proposed entity creation action with prefilled fields.
    The frontend renders it as a clickable UI button; the consultant NEVER
    auto-executes creation (spec §4.4).

    Fields
    ------
    entity_kind   : one of "character_sheet" | "dataset_pack" |
                    "training_recipe" | "lora_preset" | "i2v_recipe"
    action        : always "create" in the current phase
    prefilled     : dict of fields the UI should pre-populate in the creation form
    reason        : one-line explanation of why this entity is needed now
    existing_id   : if a matching entity already exists in the project,
                    its id is surfaced here so the user can pick it instead
    """

    entity_kind: str  # "character_sheet" | "dataset_pack" | "training_recipe" | "lora_preset" | "i2v_recipe"
    action: str = "create"
    prefilled: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    existing_id: str | None = None


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
    # Training-flow extensions (spec §7.1.1 / M4.b) — populated when
    # is_training_flow=True; empty lists otherwise to keep backward compatibility.
    is_training_flow: bool = False
    training_character_sheet_id: str | None = None  # selected or asked-for entity id
    training_dataset_pack_id: str | None = None
    training_recipe_id: str | None = None
    training_lora_preset_id: str | None = None
    training_i2v_recipe_id: str | None = None
    suggestion_cards: list[TrainingSuggestionCard] = Field(default_factory=list)


class ClarifyResult(BaseModel):
    modality: Modality
    summary: str
    questions: list[str] = Field(default_factory=list)
    template_loaded: bool
    next_step: str
    analysis: ConsultantAnalysis | None = None
    # Spec §5.15 / C-spec.md §4.3 — attached at the route layer (core/main.py),
    # never inside the planner: unlike TrainingSuggestionCard (keyed off the
    # session's modality/prompt text), this card's condition is a PROJECT-level
    # fact (an IMAGE asset + a CharacterSheet with sheet_source_path) that the
    # stateless planner has no access to. Forward-referenced (class defined
    # later in this file) + rebuilt at module bottom, same idiom as
    # ConversationEntry.analysis above.
    fidelity_suggestion_cards: list["FidelitySuggestionCard"] = Field(default_factory=list)


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


class RefinePromptMode(str, Enum):  # noqa: UP042 -- matches every sibling enum in this file (Modality, GenerationRecipe, RefineStrategy, ...); not switching to enum.StrEnum for one class only.
    """How a refine's ``instruction`` combines with the parent version's own
    effective prompt (spec §5.11 lineage / BP-REFINE-1).

    ``append`` (default) carries the parent's effective prompt forward and
    appends the instruction, so an element established in an earlier round
    (e.g. "twin daggers") is not silently dropped when a later round's mask
    happens to cover the same region and its instruction does not repeat it
    (measured 2026-09-05, misakaAssetGene-gen-test-260904/H-report.md).
    ``replace`` is the pre-existing behaviour: the instruction alone becomes
    the whole prompt, an explicit opt-in for when the caller really does want
    a from-scratch prompt. A future ``remove:<tags>`` mode is out of scope.
    """

    APPEND = "append"
    REPLACE = "replace"


class MaskRegion(BaseModel):
    """A single mask-paint region in SOURCE-IMAGE pixel coordinates (BP-EDITOR-2).

    ``bbox`` is ``[x0, y0, x1, y1]``, half-open like a Python slice
    (``x1 > x0`` and ``y1 > y0`` — enforced in ``core.editor.mask``, not
    here, so the route can report ``clamped`` instead of a bare 422).
    A bbox that extends past the source image bounds is clamped to fit,
    never rejected.
    """

    bbox: list[int] = Field(min_length=4, max_length=4)
    dilate: int = Field(default=0, ge=0, le=256)
    feather: int = Field(default=0, ge=0, le=256)


class MaskFromRegionsRequest(BaseModel):
    """``POST .../assets/{asset_id}/mask`` body (BP-EDITOR-2).

    Produces a white-on-black mask PNG (LoadImageMask channel=red
    convention) the same size as the source asset: the union of
    ``regions`` (each optionally dilated/feathered) minus the union of
    ``subtract`` regions.

    ``regions``/``subtract`` are capped at 32 entries each — a bound on the
    request shape (never the source image), so a single request cannot pin
    the core API's CPU/memory rasterizing an unbounded region list
    (``core/editor/mask.py``'s own ``MAX_MASK_PIXELS`` bounds the OTHER half
    of that same risk, the source image's pixel count).
    """

    regions: list[MaskRegion] = Field(min_length=1, max_length=32)
    subtract: list[MaskRegion] = Field(default_factory=list, max_length=32)
    name: str | None = None


class MaskFromRegionsResponse(BaseModel):
    """Result of a bbox-region mask build (BP-EDITOR-2)."""

    mask_asset_id: str
    width: int
    height: int
    coverage_ratio: float
    clamped: bool = False


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
    # The prompt / negative prompt that ACTUALLY produced this asset (spec
    # §5.11 / BP-REFINE-1) — set for txt2img jobs and refine children alike.
    # Absent on assets persisted before this field existed; callers fall back
    # to the originating job's ``prompt`` / ``params.negative`` (see
    # GenerationService._resolve_effective_prompt/_negative), never a migration.
    effective_prompt: str | None = None
    effective_negative: str | None = None
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
    # Tunable sampler/recipe params (checkpoint/steps/cfg/width/height/sampler/
    # scheduler/seed — same vocabulary as GenerationJob.params / RefineRequest.
    # params, spec §6.2). Merged into the job's existing params on update, not
    # a wholesale replace, so patching one key never drops the others.
    params: dict[str, Any] = Field(default_factory=dict)


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
    # How ``instruction`` combines with the parent's effective prompt
    # (BP-REFINE-1). Defaults to ``append`` so an earlier round's element is
    # not silently dropped by a later round's mask (spec §5.11 lineage).
    prompt_mode: RefinePromptMode = RefinePromptMode.APPEND


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
    """Per-worker license report entry (spec §2 / §9.3 — M5.2).

    Fields
    ------
    worker_name         : manifest key for this worker
    display_name        : human-readable name
    repo                : upstream git repository URL
    recommended_reference / installed_reference : version strings
    license             : SPDX id or raw license name; None = unknown
    commercial          : True = confirmed commercial-use allowed;
                          False = confirmed commercial-use NOT allowed;
                          None = registry has no commercial field for this model
                          (truthful-delivery: never guess)
    attribution         : True = attribution required by this license;
                          False = no attribution required;
                          None = cannot be determined from available data
    attribution_note    : short human-readable note on attribution requirement
                          (e.g. "Apache-2.0 requires NOTICE file preservation")
    nsfw                : True = model registry marks this worker/model as NSFW;
                          False = explicitly not NSFW;
                          None = not specified in registry (unknown)
    job_count           : number of jobs using this worker in the project
    asset_count         : number of accepted assets produced by this worker
    modalities          : list of modality strings this worker was used for
    readiness_note      : live readiness note from worker snapshot
    """

    worker_name: str
    display_name: str
    repo: str
    recommended_reference: str
    installed_reference: str | None = None
    license: str | None = None
    commercial: bool | None = None
    attribution: bool | None = None
    attribution_note: str | None = None
    nsfw: bool | None = None
    job_count: int = 0
    asset_count: int = 0
    modalities: list[str] = Field(default_factory=list)
    readiness_note: str | None = None


class LicenseReportSummary(BaseModel):
    """Concise machine- and human-readable summary for the export-confirm dialog
    (spec §2 / M5.2 export-summary data deliverable).

    Counts apply to the set of workers/models that appear in the project's jobs.
    ``unknown_*`` counts track entries where the field is None so the UI can
    surface them as requiring manual review before a commercial release.
    """

    total_workers: int = 0
    commercial_ok: int = 0
    commercial_no: int = 0
    commercial_unknown: int = 0
    attribution_required: int = 0
    attribution_not_required: int = 0
    attribution_unknown: int = 0
    nsfw_present: int = 0
    nsfw_absent: int = 0
    nsfw_unknown: int = 0
    has_nsfw: bool = False


class ProjectLicenseReport(BaseModel):
    project_id: str
    project_name: str
    generated_at: datetime | None = None
    entries: list[LicenseReportEntry] = Field(default_factory=list)
    summary: LicenseReportSummary = Field(default_factory=LicenseReportSummary)
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


# ---------------------------------------------------------------------------
# M5.1 — Version Tree DAG (spec §8.2 git-log-style nodes)
# ---------------------------------------------------------------------------

class VersionTreeNode(BaseModel):
    """One node in the version-tree DAG (spec §8.2 / M5.1).

    Fields
    ------
    id              : asset id (``asset:<uuid>``) or job id (``job:<uuid>``)
    parent_id       : parent asset id, or None for root versions
    asset_type      : e.g. "image", "music", "voice"
    modality        : Modality enum value
    title           : human-readable title
    status          : asset_type string (for assets) or job status string (for jobs)
    created_at      : creation timestamp
    prompt_hash     : sha256 of the generation prompt (may be None)
    refine_strategy : strategy used when this version was refined from its parent
    prompt_delta    : prompt fragment that changed versus the parent
    param_delta     : parameter diff dict versus the parent
    mask_asset_id   : mask used for inpaint refine (if any)
    backend         : worker / backend name used to produce this version
    is_orphaned     : True when parent_id is set but the parent node is missing
    """

    id: str
    parent_id: str | None = None
    asset_type: str
    modality: Modality
    title: str
    status: str
    created_at: datetime
    prompt_hash: str | None = None
    refine_strategy: RefineStrategy | None = None
    prompt_delta: str | None = None
    param_delta: dict[str, Any] = Field(default_factory=dict)
    mask_asset_id: str | None = None
    backend: str | None = None
    is_orphaned: bool = False


class VersionTreeData(BaseModel):
    """Response envelope for the version-tree endpoint (spec §8.2 / M5.1).

    ``nodes`` are sorted by ``created_at`` (oldest first).
    ``cycle_detected`` is set to True when a parent_version_id chain contains
    a cycle — in that case the affected nodes are still included but the cycle
    edge is not followed (no infinite loop).
    ``node_cap`` documents the maximum number of nodes returned; if the actual
    count reached the cap, ``capped`` is True.
    """

    nodes: list[VersionTreeNode] = Field(default_factory=list)
    cycle_detected: bool = False
    capped: bool = False
    node_cap: int = 2000


class VersionDiffRequest(BaseModel):
    """Request body for the version diff endpoint (spec §8.2 / M5.1)."""

    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)


class VersionDiffData(BaseModel):
    """Structured delta between two asset versions (spec §8.2 / M5.1).

    All fields are ``None`` when the two versions share the same value.

    Fields
    ------
    from_id / to_id : asset ids being compared
    prompt_delta    : prompt text that changed (from ``to`` node's prompt_delta
                      relative to its parent, or a synthetic diff)
    param_delta     : parameter dict diff (keys present in ``to`` but absent or
                      different in ``from``)
    mask_diff       : None unless one or both versions used a mask; records
                      ``from_mask`` and ``to_mask`` asset ids
    recipe_diff     : None unless the refine recipe / strategy changed
    strategy_diff   : None unless the refine strategy changed
    backend_diff    : None unless the backend changed between versions
    """

    from_id: str
    to_id: str
    prompt_delta: str | None = None
    param_delta: dict[str, Any] = Field(default_factory=dict)
    mask_diff: dict[str, str | None] | None = None
    recipe_diff: dict[str, str | None] | None = None
    strategy_diff: dict[str, str | None] | None = None
    backend_diff: dict[str, str | None] | None = None


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
    # spec §7.3 resume: caller-supplied path to a previously discovered
    # kohya_ss saved-state dir (see TrainingJob.resume_checkpoint_path below).
    # TrainingService.submit_job validates this is confined under the
    # project's own <project_dir>/models output directory before it is ever
    # stored on the job or spliced into a subprocess argv (security — this
    # value comes from a client).
    resume_checkpoint_path: str | None = None


class TrainingJob(BaseModel):
    id: str
    project_id: str
    title: str
    modality: Modality
    worker: str
    dataset_path: str
    status: TrainingJobStatus
    note: str | None = None
    # Executor fields (spec §7.3 / M4.d) — populated by TrainingExecutor.
    progress: int = 0                   # 0–100 polled from runner output
    progress_label: str | None = None   # human-readable progress description
    exit_code: int | None = None        # subprocess exit code on completion/failure
    stderr_tail: str | None = None      # last ~20 lines of stderr on failure
    # spec §7.3 resume: set by the executor on failure when a kohya_ss state
    # directory is found in the output_dir.  Pass to build_lora_command(
    # resume_checkpoint_path=...) on the next submit to append --resume <dir>.
    # GPT-SoVITS resume is still deferred / out-of-scope.
    resume_checkpoint_path: str | None = None
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
    # Spec §5.15 / §7.1.1 — optional path to a character reference folder
    # (containing setting.md + outfits.md) used by the fidelity checklist
    # parser (core/consultant/fidelity.py). Always read live, never cached
    # into SQLite beyond this path string. Optional + defaulted to None so
    # existing rows created before this field existed still load.
    sheet_source_path: str | None = None
    created_at: datetime
    updated_at: datetime


class CharacterSheetCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    visual_anchors: list[str] = Field(default_factory=list)
    trigger_words: list[str] = Field(default_factory=list)
    forbidden_features: list[str] = Field(default_factory=list)
    reference_image_refs: list[str] = Field(default_factory=list)
    sheet_source_path: str | None = None


class CharacterSheetUpdateRequest(BaseModel):
    name: str | None = None
    visual_anchors: list[str] | None = None
    trigger_words: list[str] | None = None
    forbidden_features: list[str] | None = None
    reference_image_refs: list[str] | None = None
    sheet_source_path: str | None = None


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


class TrainingJobPollData(BaseModel):
    """Response envelope for polling a single training job (spec §7.3)."""

    job: TrainingJob


# ---------------------------------------------------------------------------
# M5.3 — Cross-project reference schemas (§5.6.2 / §5.6.5 / §16 Q4)
# ---------------------------------------------------------------------------

class CrossRefStatus(str, Enum):
    """Mirror of RefStatus from cross_project.py, exported as a Pydantic-friendly enum."""

    LIVE = "live"
    OUTDATED = "outdated"
    EXTERNAL = "external"
    BROKEN = "broken"


class CrossRefEntry(BaseModel):
    """One resolved cross-project reference entry (§5.6.2 / §5.6.3).

    Fields
    ------
    ref         : raw @ref string
    status      : live / outdated / external / broken
    path        : resolved file path (str for JSON serialisability), or None
    hash        : sha256 of the resolved file, or None
    origin_hash : sha256 recorded in origins.json, or None
    message     : human-readable resolver note
    """

    ref: str
    status: CrossRefStatus
    path: str | None = None
    hash: str | None = None
    origin_hash: str | None = None
    message: str = ""


class CrossRefListData(BaseModel):
    """Response envelope for listing all cross-project refs in a project."""

    project_id: str
    refs: list[CrossRefEntry] = Field(default_factory=list)
    cycle_warning: list[list[str]] = Field(default_factory=list)


class MaterializeRequest(BaseModel):
    """Request body for the materialization endpoint (§16 Q4).

    Parameters
    ----------
    refs:
        Optional list of specific @ref strings to materialize.
        If omitted, all refs found in the project are materialized.
    """

    refs: list[str] | None = None


class MaterializeResultEntry(BaseModel):
    """One result entry from the materialization operation (§16 Q4).

    Fields
    ------
    ref         : the @ref that was processed
    status      : "materialized" | "already_external" | "broken"
    local_path  : path of the materialized file, or None
    provenance  : provenance dict recorded in origins.json
    message     : human-readable note
    """

    ref: str
    status: str
    local_path: str | None = None
    provenance: dict[str, Any] | None = None
    message: str = ""


class MaterializeData(BaseModel):
    """Response envelope for the materialize endpoint (§16 Q4)."""

    project_id: str
    materialized: list[MaterializeResultEntry] = Field(default_factory=list)
    broken: list[MaterializeResultEntry] = Field(default_factory=list)
    total: int = 0


class BodyRegion(str, Enum):
    """Spec §5.15 / §2.1 — coarse body-region hint for a fidelity check.

    Used both to bucket a derived checklist item (parser,
    core/consultant/fidelity.py) and to sanity-check a VLM critic's returned
    ``region_bbox`` against the expected region (core/llm/vision.py §3.4
    anti-hallucination gate 3).
    """

    HEAD = "head"
    FACE = "face"
    TORSO = "torso"
    WAIST = "waist"
    LEGS = "legs"
    BACKGROUND = "background"


class FidelityCheck(BaseModel):
    """Spec §5.15 / §2.1 — one derived checklist item to critique against a render.

    Derived by ``core.consultant.fidelity.parse_character_checklist`` from a
    character's ``setting.md`` (source="setting") or the selected outfit's
    section of ``outfits.md`` (source="outfits"). ``pass_criteria`` is the
    original SSOT bullet text verbatim — never summarized — so a human can
    always trace a check back to its source sentence.
    """

    id: str
    label_zh: str
    pass_criteria: str
    region_hint: BodyRegion
    fix_tags: list[str] = Field(default_factory=list)
    source: Literal["setting", "outfits"]


class FidelityCheckResult(BaseModel):
    """Spec §5.15 / §3.2 — one VLM critic verdict for a single ``FidelityCheck``.

    ``region_bbox`` is ``(x0, y0, x1, y1)`` in original-image pixel
    coordinates (already rescaled from any downsampled critic input, spec
    §3.3). Anti-hallucination gates (§3.4) may downgrade a raw ``passed=False``
    verdict to ``True`` when the critic could not localize the failure
    convincingly — that downgrade happens in ``core.llm.vision``, not here;
    this model only carries the final, already-gated verdict.
    """

    id: str
    passed: bool
    confidence: float = Field(ge=0, le=1)
    region_bbox: tuple[int, int, int, int] | None = None
    note: str = ""


# ---------------------------------------------------------------------------
# §5.15 / C-spec.md §4-6 — Fidelity refine LOOP (Brief 2): controller state,
# persistence records, and API request/response shapes.
# ---------------------------------------------------------------------------

class FidelityLoopStatus(str, Enum):  # noqa: UP042 -- matches every sibling enum in this file (see RefinePromptMode above); not switching to enum.StrEnum for one class only.
    """Spec §4.1 loop-controller state machine.

    ``FAILED`` is reserved for an unexpected execution error (mask build /
    refine dispatch raising) — never returned by the pure planner in
    ``core.consultant.fidelity_loop``, only set by ``FidelityService`` when a
    round's I/O step raises.
    """

    PENDING_CRITIQUE = "pending_critique"
    CRITIQUING = "critiquing"
    AWAITING_USER = "awaiting_user"
    BUILDING_MASK = "building_mask"
    REFINING = "refining"
    PASSED = "passed"
    STOPPED_MAX_ROUNDS = "stopped_max_rounds"
    STOPPED_REGRESSION_RECOVERED = "stopped_regression_recovered"
    FAILED = "failed"


class FidelityLoopStartRequest(BaseModel):
    """``POST .../assets/{asset_id}/fidelity-loop`` body (spec §5)."""

    character_sheet_id: str = Field(min_length=1)
    outfit_variant: str = Field(min_length=1)
    max_rounds: int = Field(default=4, ge=1, le=8)
    # Brief 3 (spec §5.15 / C-spec.md §5): ``None`` (the field omitted) means
    # "use this project's ``ProjectSummary.auto_loop_enabled`` setting" —
    # resolved at the route layer (core/main.py:start_fidelity_loop) BEFORE
    # this request reaches FidelityService, so the service/store always see
    # a concrete bool. An explicit True/False in the request always wins.
    auto_continue: bool | None = None


class FidelityRoundPlan(BaseModel):
    """Per-round decision record (spec §4.2) — the controller's own audit
    trail: which checks it targeted, how it built the mask, and what refine
    request it assembled. Computed by ``core.consultant.fidelity_loop.plan_round``
    (pure), never persisted as its own SQLite row (spec §5's schema has no
    plan column) — it is deterministically re-derivable from a round's
    already-persisted ``critic_json``, so it is surfaced live in API
    responses (``FidelityLoopData.next_round_plan``) instead of duplicating
    storage.
    """

    round_index: int
    target_asset_id: str
    chosen_check_ids: list[str] = Field(default_factory=list)
    reasserted_check_ids: list[str] = Field(default_factory=list)
    mask_regions: list[MaskRegion] = Field(default_factory=list)
    mask_subtract: list[MaskRegion] = Field(default_factory=list)
    instruction: str = ""
    instruction_tags: list[str] = Field(default_factory=list)
    strategy: RefineStrategy | None = None
    reason: str = ""


class FidelityLoop(BaseModel):
    """Spec §5 ``fidelity_loops`` row."""

    id: str
    project_id: str
    root_asset_id: str
    character_sheet_id: str
    outfit_variant: str
    status: FidelityLoopStatus
    current_round: int = 0
    max_rounds: int = 4
    best_asset_id: str
    best_pass_count: int = 0
    auto_continue: bool = False
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class FidelityLoopRound(BaseModel):
    """Spec §5 ``fidelity_loop_rounds`` row.

    ``critic_results`` is the parsed form of the persisted ``critic_json``
    column (kept as a convenience field on the API-facing model; the store
    round-trips it through ``critic_json`` on disk, spec §6 "判官 JSON 存
    fidelity_loop_rounds.critic_json").
    """

    id: str
    loop_id: str
    round_index: int
    asset_id: str
    critic_results: list[FidelityCheckResult] = Field(default_factory=list)
    pass_count: int
    fail_count: int
    mask_asset_id: str | None = None
    refine_job_id: str | None = None
    created_at: datetime


class FidelityLoopData(BaseModel):
    """Response envelope for start / advance / get (spec §5)."""

    loop: FidelityLoop
    rounds: list[FidelityLoopRound] = Field(default_factory=list)
    unresolved_check_ids: list[str] = Field(default_factory=list)
    next_round_plan: FidelityRoundPlan | None = None


class FidelitySuggestionCard(BaseModel):
    """Backend data shape for a suggestion card proposing to start a
    character-fidelity refine loop (spec §5.15 / C-spec.md §4.3, mirrors
    ``TrainingSuggestionCard`` — spec §4.4 / §5.12.1).

    Built by ``core.consultant.fidelity_suggestion.build_fidelity_suggestion_cards``
    and attached to ``ClarifyResult.fidelity_suggestion_cards`` at the route
    layer (``core/main.py``) whenever the project already has an IMAGE asset
    AND at least one ``CharacterSheet`` with ``sheet_source_path`` set — a
    project-level fact, independent of the current session's modality/state
    (unlike ``TrainingSuggestionCard``, which is keyed off the session's
    prompt text). The frontend renders it as a clickable button that opens
    the ``FidelityLoopStartRequest`` form pre-filled with these fields; the
    consultant NEVER auto-starts the loop (spec §4.4 — no auto-exec).

    Fields
    ------
    action                 : always "start_fidelity_loop" in the current phase
    asset_id               : prefilled root IMAGE asset id
    character_sheet_id     : prefilled CharacterSheet id
    outfit_variant_choices : outfit variants parsed live from the sheet's
                              outfits.md (spec §2.1 list_outfit_variants);
                              empty if the folder could not be parsed
    auto_continue          : prefilled from the project's
                              ``auto_loop_enabled`` setting (spec §5's
                              PATCH .../settings)
    """

    action: str = "start_fidelity_loop"
    asset_id: str
    character_sheet_id: str
    outfit_variant_choices: list[str] = Field(default_factory=list)
    auto_continue: bool = False
    title: str = "檢查角色一致性"
    description: str = "對這張立繪跑角色一致性自動精修迴圈：VLM 逐項比對角色 SSOT，局部遮罩重繪不一致之處，最多數輪直到全過。"
    reason: str = ""


ConversationEntry.model_rebuild()
ProjectWorkspaceData.model_rebuild()
ClarifyResult.model_rebuild()
