import { appEnv } from "@/config/env";
import type {
  ApiErrorResponse,
  ApiResponse,
  ClarifyPayload,
  ClarifyResult,
  ConversationHistoryData,
  CreateProjectPayload,
  HealthData,
    IntegrationSnapshot,
    LocalLlmStatus,
    ModelDownloadPayload,
    ModelDownloadResult,
    ProjectLicenseReport,
    ProjectListData,
    ProjectSchemaData,
    ProjectSummary,
    TrainingWorkspaceData,
    ProjectVersionGraph,
    ProjectTypeData,
    ProjectWorkspaceData,
    SelectProjectPayload,
    SynopsisOptimizePayload,
    SynopsisOptimizeResult,
    WorkerSmokeResult,
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

const devStartupRetryAttempts = 25;
const devStartupRetryDelayMs = 1000;

function isSafeRetryableRequest(init?: RequestInit): boolean {
  const method = (init?.method ?? "GET").toUpperCase();
  return method === "GET";
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
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

  const url = `${appEnv.apiBaseUrl}${path}`;
  const maxAttempts = isDevDiagnostics && isSafeRetryableRequest(init) ? devStartupRetryAttempts : 1;
  let response: Response | null = null;
  let lastError: unknown;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const useFormData = init?.body instanceof FormData;
      response = await fetch(url, {
        headers: {
          ...(useFormData ? {} : { "Content-Type": "application/json" }),
          ...(init?.headers ?? {}),
        },
        ...init,
      });
      break;
    } catch (error) {
      lastError = error;
      if (attempt >= maxAttempts) {
        throw error;
      }
      if (isDevDiagnostics) {
        console.warn("[misaka.api] retrying startup request", {
          method: init?.method ?? "GET",
          url,
          attempt,
          maxAttempts,
        });
      }
      await sleep(devStartupRetryDelayMs);
    }
  }

  if (response === null) {
    throw lastError instanceof Error ? lastError : new Error(`Request failed: ${url}`);
  }

  if (!response.ok) {
    if (isDevDiagnostics) {
      console.error("[misaka.api] failed", {
        method: init?.method ?? "GET",
        url,
        status: response.status,
      });
    }
    const errorPayload = (await response.json().catch(() => null)) as ApiErrorResponse | null;
    throw new ApiClientError(errorPayload?.message ?? MessageKey.FAIL_500, errorPayload?.detail);
  }

  if (isDevDiagnostics) {
    console.info("[misaka.api] ok", {
      method: init?.method ?? "GET",
      url,
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
   * Loads a single project by id.
   */
  getProject: (projectId: string) => request<{ project: ProjectSummary }>(`/api/v1/projects/${projectId}`),
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
   * Loads persisted consultant conversation entries for a project.
   */
  projectConversation: (projectId: string) => request<ConversationHistoryData>(`/api/v1/projects/${projectId}/conversation`),
  /**
   * Loads a paginated slice of project conversation history.
   */
  projectConversationPage: (projectId: string, offset = 0, limit = 40) =>
    request<ConversationHistoryData>(`/api/v1/projects/${projectId}/conversation?offset=${offset}&limit=${limit}`),
  /**
   * Loads project jobs and assets.
   */
  projectWorkspace: (projectId: string) => request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/workspace`),
  /**
   * Loads the version graph for a project.
   */
  projectVersionGraph: (projectId: string) => request<ProjectVersionGraph>(`/api/v1/projects/${projectId}/versions`),
  /**
   * Loads the project license report.
   */
  projectLicenseReport: (projectId: string) => request<ProjectLicenseReport>(`/api/v1/projects/${projectId}/license-report`),
  /**
   * Loads project training jobs.
   */
  projectTrainingWorkspace: (projectId: string) => request<TrainingWorkspaceData>(`/api/v1/projects/${projectId}/training`),
  /**
   * Creates a training job scaffold.
   */
  createProjectTrainingJob: (projectId: string, payload: { title: string; modality: string; dataset_path: string; worker?: string | null }) =>
    request<TrainingWorkspaceData>(`/api/v1/projects/${projectId}/training`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Executes a single project job and returns refreshed workspace data.
   */
  executeProjectJob: (projectId: string, jobId: string) =>
    request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/jobs/${jobId}/execute`, {
      method: "POST",
    }),
  /**
   * Executes all ready jobs, or a provided subset, and returns the refreshed workspace.
   */
  executeReadyProjectJobs: (projectId: string, jobIds: string[] = []) =>
    request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/jobs/execute-ready`, {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  /**
   * Updates execution settings for a workspace job.
   */
  updateProjectJob: (projectId: string, jobId: string, payload: { worker: string | null; recipe: string | null; source_asset_id: string | null; mask_asset_id: string | null }) =>
    request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/jobs/${jobId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /**
   * Imports a project asset file and returns the refreshed workspace.
   */
  importProjectAsset: (
    projectId: string,
    payload: { file: File; modality: string; asset_type: string; title: string; description?: string },
  ) => {
    const formData = new FormData();
    formData.set("file", payload.file);
    formData.set("modality", payload.modality);
    formData.set("asset_type", payload.asset_type);
    formData.set("title", payload.title);
    formData.set("description", payload.description ?? "");
    return request<ProjectWorkspaceData>(`/api/v1/projects/${projectId}/assets/import`, {
      method: "POST",
      body: formData,
    });
  },
  /**
   * Sends a consultant request scoped to a project and persists the result.
   */
  clarifyProject: (projectId: string, payload: ClarifyPayload) =>
    request<ClarifyResult>(`/api/v1/projects/${projectId}/consultant/clarify`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Loads the integration snapshot for tools, workers, and providers.
   */
  integration: () => request<IntegrationSnapshot>("/api/v1/integration"),
  /**
   * Clones or syncs a worker repository to the recommended revision.
   */
  installWorker: (workerName: string) => request<void>(`/api/v1/workers/${workerName}/install`, { method: "POST" }),
  /**
   * Starts a worker server.
   */
  startWorker: (workerName: string) => request<void>(`/api/v1/workers/${workerName}/start`, { method: "POST" }),
  /**
   * Stops a worker server.
   */
  stopWorker: (workerName: string) => request<void>(`/api/v1/workers/${workerName}/stop`, { method: "POST" }),
  /**
   * Runs a worker smoke test.
   */
  smokeWorker: (workerName: string) => request<WorkerSmokeResult>(`/api/v1/workers/${workerName}/smoke`, { method: "POST" }),
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
  exportProjectDownloadUrl: (projectId: string, resolveRefs = true) =>
    `${appEnv.apiBaseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/export/download?resolve_refs=${resolveRefs ? "true" : "false"}`,
  ApiClientError,
};
