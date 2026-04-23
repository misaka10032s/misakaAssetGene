import {
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
  name: string;
  type: string;
  synopsis: string;
}

export interface ProjectListData {
  projects: ProjectSummary[];
  current_project: string | null;
}

export interface CreateProjectPayload {
  name: string;
  type: string;
  synopsis: string;
}

export interface SelectProjectPayload {
  name: string;
}

export interface ProjectSchemaData {
  schema: Record<string, unknown>;
}

export interface ProjectTypeData {
  project_types: string[];
}

export interface ClarifyPayload {
  modality: Modality;
  prompt: string;
}

export interface ClarifyResult {
  modality: Modality;
  summary: string;
  questions: string[];
  template_loaded: boolean;
  next_step: string;
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
  reference: string;
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
}

export interface PageNavigationItem {
  key: PageKey;
  labelKey: string;
}
