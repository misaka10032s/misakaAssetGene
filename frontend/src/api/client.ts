import { appEnv } from "@/config/env";
import type {
  ApiErrorResponse,
  ApiResponse,
  ClarifyPayload,
  ClarifyResult,
  CreateProjectPayload,
  HealthData,
  IntegrationSnapshot,
  LocalLlmStatus,
  ModelDownloadPayload,
  ModelDownloadResult,
  ProjectListData,
  ProjectSchemaData,
  ProjectSummary,
  ProjectTypeData,
  SelectProjectPayload,
  SynopsisOptimizePayload,
  SynopsisOptimizeResult,
} from "@/types/api";
import { MessageKey } from "@/types/enums";

class ApiClientError extends Error {
  public readonly messageKey: MessageKey;
  public readonly detail?: unknown;

  constructor(messageKey: MessageKey, detail?: unknown) {
    super(messageKey);
    this.messageKey = messageKey;
    this.detail = detail;
  }
}

/**
 * Executes an API request and unwraps the standardized response payload.
 *
 * @param path - Relative API path.
 * @param init - Optional fetch configuration.
 * @returns The unwrapped response data.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (isDevDiagnostics) {
    console.info("[misaka.api] request", {
      method: init?.method ?? "GET",
      url: `${appEnv.apiBaseUrl}${path}`,
    });
  }

  const response = await fetch(`${appEnv.apiBaseUrl}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    if (isDevDiagnostics) {
      console.error("[misaka.api] failed", {
        method: init?.method ?? "GET",
        url: `${appEnv.apiBaseUrl}${path}`,
        status: response.status,
      });
    }
    const errorPayload = (await response.json().catch(() => null)) as ApiErrorResponse | null;
    throw new ApiClientError(errorPayload?.message ?? MessageKey.FAIL_500, errorPayload?.detail);
  }

  if (isDevDiagnostics) {
    console.info("[misaka.api] ok", {
      method: init?.method ?? "GET",
      url: `${appEnv.apiBaseUrl}${path}`,
      status: response.status,
    });
  }

  const payload = (await response.json()) as ApiResponse<T>;
  return payload.data;
}

const isDevDiagnostics = appEnv.diagnosticsEnabled;

export const apiClient = {
  /**
   * Fetches the core API health payload.
   */
  health: () => request<HealthData>("/healthz"),
  /**
   * Loads the allowed project types defined by the backend.
   */
  projectTypes: () => request<ProjectTypeData>("/api/v1/project-types"),
  /**
   * Loads the current project list and selected project.
   */
  listProjects: () => request<ProjectListData>("/api/v1/projects"),
  /**
   * Creates a new project.
   */
  createProject: (payload: CreateProjectPayload) =>
    request<{ project: ProjectSummary }>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Selects the active project.
   */
  selectProject: (payload: SelectProjectPayload) =>
    request<{ project: ProjectSummary }>("/api/v1/projects/select", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Loads the backend project schema document.
   */
  projectSchema: () => request<ProjectSchemaData>("/api/v1/project-schema"),
  /**
   * Requests an optimized synopsis proposal.
   */
  optimizeSynopsis: (payload: SynopsisOptimizePayload) =>
    request<SynopsisOptimizeResult>("/api/v1/projects/synopsis-optimize", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Requests consultant clarification questions for a modality.
   */
  clarify: (payload: ClarifyPayload) =>
    request<ClarifyResult>("/api/v1/consultant/clarify", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Loads the integration snapshot for tools, workers, and providers.
   */
  integration: () => request<IntegrationSnapshot>("/api/v1/integration"),
  /**
   * Loads the local LLM server status.
   */
  localLlmStatus: () => request<LocalLlmStatus>("/api/v1/llm/local"),
  /**
   * Starts the local LLM server.
   */
  startLocalLlm: () => request<LocalLlmStatus>("/api/v1/llm/local/start", { method: "POST" }),
  /**
   * Downloads a model file from a Hugging Face URL.
   */
  downloadLocalModel: (payload: ModelDownloadPayload) =>
    request<ModelDownloadResult>("/api/v1/llm/local/download", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  ApiClientError,
};
