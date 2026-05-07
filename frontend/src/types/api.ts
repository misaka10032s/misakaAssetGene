import {
  GenerationJobStatus,
  MessageKey,
  Modality,
  PageKey,
  ProviderMode,
  ProviderName,
  ProviderStatus,
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

export interface NetworkSnapshot {
  mode: string;
  reachable: boolean;
  summary: string;
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

export interface LicenseReportEntry {
  worker_name: string;
  display_name: string;
  repo: string;
  recommended_reference: string;
  installed_reference: string | null;
  license: string | null;
  commercial: string | null;
  job_count: number;
  asset_count: number;
  modalities: string[];
  readiness_note: string | null;
}

export interface ProjectLicenseReport {
  project_id: string;
  project_name: string;
  generated_at: string | null;
  entries: LicenseReportEntry[];
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
