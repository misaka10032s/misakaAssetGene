import {
  ConsultantState,
  GenerationJobStatus,
  MessageKey,
  Modality,
  NetworkMode,
  NetworkState,
  PageKey,
  ProviderMode,
  ProviderName,
  ProviderStatus,
  RefineStrategy,
  TrainingEntityKind,
} from "@/types/enums";

export interface AppEnv {
  apiBaseUrl: string;
  appMode: string;
  diagnosticsEnabled: boolean;
  defaultLocale: string;
}

export interface ApiResponse<TData> {
  message: MessageKey;
  data: TData;
}

export interface ApiErrorResponse {
  message: MessageKey;
  detail?: unknown;
}

export interface HealthData {
  status: string;
  repo_root: string;
  environment: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  type: string;
  synopsis: string;
}

export interface ProjectListData {
  projects: ProjectSummary[];
  current_project_id: string | null;
}

export interface CreateProjectPayload {
  name: string;
  type: string;
  synopsis: string;
}

export interface SelectProjectPayload {
  project_id: string;
}

export interface ProjectSchemaData {
  schema: Record<string, unknown>;
}

export interface ProjectTypeData {
  project_types: string[];
}

export interface ClarifyPayload {
  prompt: string;
  modality?: Modality | null;
}

export interface ClarifyResult {
  modality: Modality;
  summary: string;
  questions: string[];
  template_loaded: boolean;
  next_step: string;
  analysis: ConsultantAnalysis | null;
}

export interface ConsultantSession {
  session_id: string;
  project_id: string;
  modality: Modality | null;
  state: ConsultantState;
  checklist_status: Record<string, boolean>;
  slots: Record<string, unknown>;
  plan: ConsultantAnalysis | null;
  last_result: ClarifyResult | null;
  created_at: string;
  updated_at: string;
}

export interface ConsultantSessionData {
  session: ConsultantSession;
  result: ClarifyResult | null;
  missing_slots: string[];
}

export interface ConsultantSessionStartPayload {
  session_id?: string | null;
  modality?: Modality | null;
  prompt: string;
}

export interface ConsultantSessionAdvancePayload {
  session_id: string;
  prompt?: string;
  slots?: Record<string, unknown>;
  accept?: boolean;
}

export interface ConversationEntry {
  id: string;
  role: string;
  content: string;
  created_at: string;
  modality: Modality | null;
  questions: string[];
  analysis: ConsultantAnalysis | null;
}

export interface ConversationHistoryData {
  entries: ConversationEntry[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface SynopsisOptimizePayload {
  project_name: string;
  project_type: string;
  synopsis: string;
}

export interface SynopsisOptimizeResult {
  optimized_synopsis: string;
  strategy: string;
  provider: string | null;
}

export interface LocalLlmStatus {
  server: string;
  base_url: string;
  is_running: boolean;
  managed_by_app: boolean;
  executable_found: boolean;
  executable_path: string | null;
  provider_order: string[];
}

export interface ModelDownloadPayload {
  url: string;
}

export interface ModelDownloadResult {
  filename: string;
  saved_path: string;
  source_url: string;
}

export interface ToolSnapshot {
  name: string;
  version: string;
}

export interface WorkerSnapshot {
  name: string;
  display_name: string;
  repo: string;
  path: string;
  recommended_reference: string;
  installed_reference: string | null;
  health_check: string | null;
  is_installed: boolean;
  is_running: boolean;
  managed_pid: number | null;
  vram_requirement_mb: number;
  runtime_state: string;
  last_job_at: string | null;
  readiness_note: string | null;
}

export interface NetworkTransition {
  mode: NetworkMode;
  from_state: NetworkState | null;
  to_state: NetworkState;
  at: string;
}

export interface NetworkSnapshot {
  mode: NetworkMode;
  state: NetworkState;
  reachable: boolean;
  local_available: boolean;
  summary: string;
  recent_transitions: NetworkTransition[];
}

export interface ProviderSnapshot {
  name: ProviderName;
  mode: ProviderMode;
  status: ProviderStatus;
  configured: boolean;
  base_url: string;
}

export interface IntegrationSnapshot {
  tools: ToolSnapshot[];
  workers: WorkerSnapshot[];
  providers: ProviderSnapshot[];
  registry_categories: string[];
  model_search_paths: string[];
  network: NetworkSnapshot;
}

export interface PageNavigationItem {
  key: PageKey;
  labelKey: string;
}

export interface ConsultantPlanStep {
  title: string;
  detail: string;
  worker: string | null;
}

export interface ConsultantDeliverable {
  modality: Modality;
  asset_type: string;
  title: string;
  variants: string[];
  worker: string | null;
}

export interface ConsultantAnalysis {
  objective: string;
  inferred_modalities: Modality[];
  franchise: string | null;
  characters: string[];
  outfits: string[];
  scenes: string[];
  actions: string[];
  style_keywords: string[];
  matrix_axes: string[];
  required_research: string[];
  search_queries: string[];
  recommended_workers: string[];
  deliverables: ConsultantDeliverable[];
  execution_steps: ConsultantPlanStep[];
  blocking_reasons: string[];
  guidance_path: string[];
  /** True when the consultant detected a training/LoRA intent (spec §5.12.1 / M4.b). */
  is_training_flow: boolean;
  /** Selected or suggested CharacterSheet ID for the training checklist. */
  training_character_sheet_id: string | null;
  /** Selected or suggested DatasetPack ID for the training checklist. */
  training_dataset_pack_id: string | null;
  /** Selected or suggested TrainingRecipe ID for the training checklist. */
  training_recipe_id: string | null;
  /** Selected or suggested LoraPreset ID for the training checklist. */
  training_lora_preset_id: string | null;
  /** Selected or suggested ImageToVideoRecipe ID (optional slot) for the training checklist. */
  training_i2v_recipe_id: string | null;
  /** Suggestion cards for missing training entities; empty in non-training flows. */
  suggestion_cards: TrainingSuggestionCard[];
}

export interface GenerationJob {
  id: string;
  project_id: string;
  title: string;
  modality: Modality;
  asset_type: string;
  status: GenerationJobStatus;
  prompt: string;
  summary: string;
  worker: string | null;
  variants: string[];
  recipe: string | null;
  source_asset_id: string | null;
  mask_asset_id: string | null;
  blocking_reason: string | null;
  last_error: string | null;
  progress: number;
  progress_label: string | null;
  search_queries: string[];
  steps: ConsultantPlanStep[];
  created_at: string;
  updated_at: string;
}

export interface AssetRecord {
  id: string;
  job_id: string | null;
  modality: Modality;
  asset_type: string;
  title: string;
  path: string;
  description: string;
  created_at: string;
}

export interface ProjectWorkspaceData {
  jobs: GenerationJob[];
  assets: AssetRecord[];
  plans: ConsultantPlanRecord[];
}

/** One job that was skipped (blocked) during a batch-execute (spec §5.14). */
export interface SkippedJobInfo {
  job_id: string;
  title: string;
  reason: string;
}

/** Response envelope for POST /jobs/execute-ready (spec §5.14 batch honesty). */
export interface BatchExecuteData {
  workspace: ProjectWorkspaceData;
  executed_count: number;
  skipped: SkippedJobInfo[];
}

export interface JobExecutionPatch {
  worker: string | null;
  recipe: string | null;
  source_asset_id: string | null;
  mask_asset_id: string | null;
}

export interface AssetImportPayload {
  file: File;
  modality: Modality;
  asset_type: string;
  title: string;
  description?: string;
}

export interface WorkerSmokeResult {
  worker_name: string;
  ok: boolean;
  detail: string;
  checked_at: string;
}

export interface ConsultantPlanRecord {
  id: string;
  title: string;
  path: string;
  summary: string;
  prompt: string;
  modalities: Modality[];
  created_at: string;
}

/**
 * Per-worker license report entry (spec §2 / §9.3 — M5.2).
 * Tri-state fields (`boolean | null`) use `null` for "unknown" — never render
 * null as a confident yes/no; it must be visually distinct from false.
 */
export interface LicenseReportEntry {
  worker_name: string;
  display_name: string;
  repo: string;
  recommended_reference: string;
  installed_reference: string | null;
  license: string | null;
  /** true = commercial OK; false = commercial NOT OK; null = unknown */
  commercial: boolean | null;
  /** true = attribution required; false = not required; null = cannot determine */
  attribution: boolean | null;
  /** Short human-readable attribution note (e.g. "Apache-2.0 requires NOTICE file"). */
  attribution_note: string | null;
  /** true = NSFW; false = explicitly not NSFW; null = not specified in registry */
  nsfw: boolean | null;
  job_count: number;
  asset_count: number;
  modalities: string[];
  readiness_note: string | null;
}

/**
 * Project-level license summary for the export-confirm dialog (spec §2 / M5.2).
 * Counts are over the set of workers/models used in the project's jobs.
 */
export interface LicenseReportSummary {
  total_workers: number;
  commercial_ok: number;
  commercial_no: number;
  commercial_unknown: number;
  attribution_required: number;
  attribution_not_required: number;
  attribution_unknown: number;
  nsfw_present: number;
  nsfw_absent: number;
  nsfw_unknown: number;
  /** True when at least one worker is marked NSFW. */
  has_nsfw: boolean;
}

export interface ProjectLicenseReport {
  project_id: string;
  project_name: string;
  generated_at: string | null;
  entries: LicenseReportEntry[];
  /** Project-level summary counts — used by the export-confirm dialog. */
  summary: LicenseReportSummary;
  warnings: string[];
}

export interface ProjectVersionNode {
  id: string;
  title: string;
  node_type: string;
  modality: Modality;
  status: string;
  worker: string | null;
  created_at: string;
}

export interface ProjectVersionEdge {
  source: string;
  target: string;
  relation: string;
}

export interface ProjectVersionGraph {
  nodes: ProjectVersionNode[];
  edges: ProjectVersionEdge[];
}

export interface TrainingJob {
  id: string;
  project_id: string;
  title: string;
  modality: Modality;
  worker: string;
  dataset_path: string;
  status: string;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface TrainingWorkspaceData {
  jobs: TrainingJob[];
}

// ---------------------------------------------------------------------------
// §7.1.1 training entity interfaces (M4.a / M4.c)
// ---------------------------------------------------------------------------

/** One LoRA layer entry inside a LoraPreset stack (spec §7.1.1). */
export interface LoraLayer {
  kind: string;
  lora_ref: string;
  weight: number;
}

/** CharacterSheet entity (spec §7.1.1). */
export interface CharacterSheet {
  id: string;
  project_id: string;
  name: string;
  visual_anchors: string[];
  trigger_words: string[];
  forbidden_features: string[];
  reference_image_refs: string[];
  created_at: string;
  updated_at: string;
}

/** Create payload for CharacterSheet. */
export interface CharacterSheetCreatePayload {
  name: string;
  visual_anchors?: string[];
  trigger_words?: string[];
  forbidden_features?: string[];
  reference_image_refs?: string[];
}

/** Partial-update payload for CharacterSheet. */
export interface CharacterSheetUpdatePayload {
  name?: string;
  visual_anchors?: string[];
  trigger_words?: string[];
  forbidden_features?: string[];
  reference_image_refs?: string[];
}

/** DatasetPack entity (spec §7.1.1). */
export interface DatasetPack {
  id: string;
  project_id: string;
  source: string;
  cleaning_status: string;
  tags: string[];
  license: string;
  split_strategy: string;
  members: string[];
  created_at: string;
  updated_at: string;
}

/** Create payload for DatasetPack. */
export interface DatasetPackCreatePayload {
  source: string;
  cleaning_status: string;
  tags?: string[];
  license?: string;
  split_strategy?: string;
  members?: string[];
}

/** Partial-update payload for DatasetPack. */
export interface DatasetPackUpdatePayload {
  source?: string;
  cleaning_status?: string;
  tags?: string[];
  license?: string;
  split_strategy?: string;
  members?: string[];
}

/** TrainingRecipe entity (spec §7.1.1). */
export interface TrainingRecipe {
  id: string;
  project_id: string;
  base_model: string;
  rank: number;
  epochs: number;
  optimizer: string;
  caption_strategy: string;
  created_at: string;
  updated_at: string;
}

/** Create payload for TrainingRecipe. */
export interface TrainingRecipeCreatePayload {
  base_model: string;
  rank: number;
  epochs: number;
  optimizer: string;
  caption_strategy: string;
}

/** Partial-update payload for TrainingRecipe. */
export interface TrainingRecipeUpdatePayload {
  base_model?: string;
  rank?: number;
  epochs?: number;
  optimizer?: string;
  caption_strategy?: string;
}

/** LoraPreset entity (spec §7.1.1). */
export interface LoraPreset {
  id: string;
  project_id: string;
  name: string;
  layers: LoraLayer[];
  created_at: string;
  updated_at: string;
}

/** Create payload for LoraPreset. */
export interface LoraPresetCreatePayload {
  name: string;
  layers?: LoraLayer[];
}

/** Partial-update payload for LoraPreset. */
export interface LoraPresetUpdatePayload {
  name?: string;
  layers?: LoraLayer[];
}

/** ImageToVideoRecipe entity (spec §7.1.1). */
export interface ImageToVideoRecipe {
  id: string;
  project_id: string;
  name: string;
  workflow_kind: string;
  frames: number;
  fps: number;
  motion_strength: number;
  notes: string;
  created_at: string;
  updated_at: string;
}

/** Create payload for ImageToVideoRecipe. */
export interface ImageToVideoRecipeCreatePayload {
  name: string;
  workflow_kind: string;
  frames: number;
  fps: number;
  motion_strength: number;
  notes?: string;
}

/** Partial-update payload for ImageToVideoRecipe. */
export interface ImageToVideoRecipeUpdatePayload {
  name?: string;
  workflow_kind?: string;
  frames?: number;
  fps?: number;
  motion_strength?: number;
  notes?: string;
}

/** Aggregate snapshot of all five training entities for a project (M4.c). */
export interface TrainingEntitiesSnapshot {
  characters: CharacterSheet[];
  dataset_packs: DatasetPack[];
  training_recipes: TrainingRecipe[];
  lora_presets: LoraPreset[];
  i2v_recipes: ImageToVideoRecipe[];
}

// ---------------------------------------------------------------------------
// Typed response envelopes for training entity endpoints (M4.c review fix).
// These match the EXACT keys the backend emits (verified against core/main.py).
// ---------------------------------------------------------------------------

/** List response for GET /characters — backend emits `{"characters": [...]}`. */
export interface CharacterListResponse {
  characters: CharacterSheet[];
}

/** Single-item response for POST/GET/PATCH /characters — backend emits `{"character": ...}`. */
export interface CharacterSingleResponse {
  character: CharacterSheet;
}

/** List response for GET /dataset-packs — backend emits `{"dataset_packs": [...]}`. */
export interface DatasetPackListResponse {
  dataset_packs: DatasetPack[];
}

/** Single-item response for POST/GET/PATCH /dataset-packs — backend emits `{"dataset_pack": ...}`. */
export interface DatasetPackSingleResponse {
  dataset_pack: DatasetPack;
}

/** List response for GET /training-recipes — backend emits `{"training_recipes": [...]}`. */
export interface TrainingRecipeListResponse {
  training_recipes: TrainingRecipe[];
}

/** Single-item response for POST/GET/PATCH /training-recipes — backend emits `{"training_recipe": ...}`. */
export interface TrainingRecipeSingleResponse {
  training_recipe: TrainingRecipe;
}

/** List response for GET /lora-presets — backend emits `{"lora_presets": [...]}`. */
export interface LoraPresetListResponse {
  lora_presets: LoraPreset[];
}

/** Single-item response for POST/GET/PATCH /lora-presets — backend emits `{"lora_preset": ...}`. */
export interface LoraPresetSingleResponse {
  lora_preset: LoraPreset;
}

/** List response for GET /i2v-recipes — backend emits `{"i2v_recipes": [...]}`. */
export interface I2vRecipeListResponse {
  i2v_recipes: ImageToVideoRecipe[];
}

/** Single-item response for POST/GET/PATCH /i2v-recipes — backend emits `{"i2v_recipe": ...}`. */
export interface I2vRecipeSingleResponse {
  i2v_recipe: ImageToVideoRecipe;
}

// ---------------------------------------------------------------------------
// Suggestion card (spec §4.4 / §5.12.1 / M4.b)
// ---------------------------------------------------------------------------

/**
 * A single suggestion card emitted by the consultant for a missing training
 * entity.  The frontend renders it as a user-clickable create action — the
 * system NEVER auto-creates (spec §4.4).
 */
export interface TrainingSuggestionCard {
  entity_kind: TrainingEntityKind | string;
  action: string;
  prefilled: Record<string, unknown>;
  reason: string;
  existing_id: string | null;
}

// ---------------------------------------------------------------------------
// §8.2 — Version Tree DAG interfaces (M5.6 frontend)
// Field names mirror the Python VersionTreeNode / VersionTreeData /
// VersionDiffData models in core/models/schemas.py exactly.
// ---------------------------------------------------------------------------

/** One node in the version-tree DAG (spec §8.2 / M5.1). */
export interface VersionTreeNode {
  id: string;
  parent_id: string | null;
  asset_type: string;
  modality: Modality;
  title: string;
  status: string;
  created_at: string;
  prompt_hash: string | null;
  refine_strategy: RefineStrategy | null;
  prompt_delta: string | null;
  param_delta: Record<string, unknown>;
  mask_asset_id: string | null;
  backend: string | null;
  is_orphaned: boolean;
}

/**
 * Response envelope for GET /api/v1/projects/{id}/versions/tree.
 * Backend emits `{"message": ..., "data": VersionTreeData}`.
 * The `data` key carries this object directly (no extra wrapper key).
 */
export interface VersionTreeData {
  nodes: VersionTreeNode[];
  cycle_detected: boolean;
  capped: boolean;
  node_cap: number;
}

/**
 * Typed response interface for the version-tree endpoint.
 * Backend route: GET /versions/tree → success_response(key, payload.model_dump())
 * payload.model_dump() serialises VersionTreeData fields directly into `data`.
 */
export interface VersionTreeResponse {
  nodes: VersionTreeNode[];
  cycle_detected: boolean;
  capped: boolean;
  node_cap: number;
}

/** Structured delta between two asset versions (spec §8.2 / M5.1). */
export interface VersionDiffData {
  from_id: string;
  to_id: string;
  prompt_delta: string | null;
  param_delta: Record<string, unknown>;
  mask_diff: { from_mask: string | null; to_mask: string | null } | null;
  recipe_diff: { from: string | null; to: string | null } | null;
  strategy_diff: { from: string | null; to: string | null } | null;
  backend_diff: { from: string | null; to: string | null } | null;
}

/**
 * Typed response interface for the version-diff endpoint.
 * Backend route: GET /versions/diff?from_id=&to_id= → success_response(key, payload.model_dump())
 */
export interface VersionDiffResponse {
  from_id: string;
  to_id: string;
  prompt_delta: string | null;
  param_delta: Record<string, unknown>;
  mask_diff: { from_mask: string | null; to_mask: string | null } | null;
  recipe_diff: { from: string | null; to: string | null } | null;
  strategy_diff: { from: string | null; to: string | null } | null;
  backend_diff: { from: string | null; to: string | null } | null;
}
