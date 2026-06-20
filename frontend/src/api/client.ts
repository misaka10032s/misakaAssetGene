import { appEnv } from "@/config/env";
import type {
  ApiErrorResponse,
  ApiResponse,
  BatchExecuteData,
  CharacterListResponse,
  CharacterSheet,
  CharacterSheetCreatePayload,
  CharacterSheetUpdatePayload,
  CharacterSingleResponse,
  ClarifyPayload,
  ClarifyResult,
  ConsultantSession,
  ConsultantSessionAdvancePayload,
  ConsultantSessionData,
  ConsultantSessionStartPayload,
  ConversationHistoryData,
  CreateProjectPayload,
  CrossRefListData,
  DatasetPack,
  DatasetPackCreatePayload,
  DatasetPackListResponse,
  DatasetPackSingleResponse,
  DatasetPackUpdatePayload,
  HealthData,
  I2vRecipeListResponse,
  I2vRecipeSingleResponse,
  ImageToVideoRecipe,
  ImageToVideoRecipeCreatePayload,
  ImageToVideoRecipeUpdatePayload,
  RefinePayload,
    IntegrationSnapshot,
    LocalLlmStatus,
    LoraPreset,
    LoraPresetCreatePayload,
    LoraPresetListResponse,
    LoraPresetSingleResponse,
    LoraPresetUpdatePayload,
    MaterializeData,
    MaterializePayload,
    ModelDownloadPayload,
    ModelDownloadResult,
    ProjectImportData,
    ProjectLicenseReport,
    ProjectListData,
    ProjectSchemaData,
    ProjectSummary,
    TrainingEntitiesSnapshot,
    TrainingRecipe,
    TrainingRecipeCreatePayload,
    TrainingRecipeListResponse,
    TrainingRecipeSingleResponse,
    TrainingRecipeUpdatePayload,
    TrainingWorkspaceData,
    ProjectVersionGraph,
    ProjectTypeData,
    ProjectWorkspaceData,
    SelectProjectPayload,
    SynopsisOptimizePayload,
    SynopsisOptimizeResult,
    VersionDiffResponse,
    VersionTreeResponse,
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
   * Executes all ready jobs, or a provided subset.
   * Returns a BatchExecuteData envelope with executed_count + skipped list (spec §5.14).
   */
  executeReadyProjectJobs: (projectId: string, jobIds: string[] = []) =>
    request<BatchExecuteData>(`/api/v1/projects/${projectId}/jobs/execute-ready`, {
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
   * Creates a refine job from an existing image asset (spec §5.11 / §6.2).
   * The backend applies the §6.2 strategy decision tree; providing
   * strategy="inpaint" and mask_asset_id forces the inpaint path.
   * Returns refreshed ProjectWorkspaceData.
   */
  refineAsset: (projectId: string, assetId: string, payload: RefinePayload) =>
    request<ProjectWorkspaceData>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}/refine`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),

  /**
   * Sends a consultant request scoped to a project and persists the result.
   */
  clarifyProject: (projectId: string, payload: ClarifyPayload) =>
    request<ClarifyResult>(`/api/v1/projects/${projectId}/consultant/clarify`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Resumes the latest unfinished consultant session for a project, if any.
   */
  resumeConsultantSession: (projectId: string) =>
    request<{ session: ConsultantSession | null }>(`/api/v1/projects/${projectId}/consultant/session`),
  /**
   * Starts (or resumes by id) a persisted consultant session.
   */
  startConsultantSession: (projectId: string, payload: ConsultantSessionStartPayload) =>
    request<ConsultantSessionData>(`/api/v1/projects/${projectId}/consultant/session`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Advances a consultant session by one state transition.
   */
  advanceConsultantSession: (projectId: string, payload: ConsultantSessionAdvancePayload) =>
    request<ConsultantSessionData>(`/api/v1/projects/${projectId}/consultant/session/advance`, {
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
  // ---------------------------------------------------------------------------
  // §7.1.1 training entity CRUD (M4.c)
  // ---------------------------------------------------------------------------

  /**
   * Lists all character sheets for a project.
   */
  listCharacters: (projectId: string) =>
    request<CharacterListResponse>(`/api/v1/projects/${projectId}/characters`),
  /**
   * Creates a character sheet inside a project.
   */
  createCharacter: (projectId: string, payload: CharacterSheetCreatePayload) =>
    request<CharacterSingleResponse>(`/api/v1/projects/${projectId}/characters`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Fetches a single character sheet by id.
   */
  getCharacter: (projectId: string, id: string) =>
    request<CharacterSingleResponse>(`/api/v1/projects/${projectId}/characters/${id}`),
  /**
   * Partially updates a character sheet.
   */
  updateCharacter: (projectId: string, id: string, payload: CharacterSheetUpdatePayload) =>
    request<CharacterSingleResponse>(`/api/v1/projects/${projectId}/characters/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /**
   * Deletes a character sheet.
   */
  deleteCharacter: (projectId: string, id: string) =>
    request<void>(`/api/v1/projects/${projectId}/characters/${id}`, { method: "DELETE" }),

  /**
   * Lists all dataset packs for a project.
   */
  listDatasetPacks: (projectId: string) =>
    request<DatasetPackListResponse>(`/api/v1/projects/${projectId}/dataset-packs`),
  /**
   * Creates a dataset pack inside a project.
   */
  createDatasetPack: (projectId: string, payload: DatasetPackCreatePayload) =>
    request<DatasetPackSingleResponse>(`/api/v1/projects/${projectId}/dataset-packs`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Fetches a single dataset pack by id.
   */
  getDatasetPack: (projectId: string, id: string) =>
    request<DatasetPackSingleResponse>(`/api/v1/projects/${projectId}/dataset-packs/${id}`),
  /**
   * Partially updates a dataset pack.
   */
  updateDatasetPack: (projectId: string, id: string, payload: DatasetPackUpdatePayload) =>
    request<DatasetPackSingleResponse>(`/api/v1/projects/${projectId}/dataset-packs/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /**
   * Deletes a dataset pack.
   */
  deleteDatasetPack: (projectId: string, id: string) =>
    request<void>(`/api/v1/projects/${projectId}/dataset-packs/${id}`, { method: "DELETE" }),

  /**
   * Lists all training recipes for a project.
   */
  listTrainingRecipes: (projectId: string) =>
    request<TrainingRecipeListResponse>(`/api/v1/projects/${projectId}/training-recipes`),
  /**
   * Creates a training recipe inside a project.
   */
  createTrainingRecipe: (projectId: string, payload: TrainingRecipeCreatePayload) =>
    request<TrainingRecipeSingleResponse>(`/api/v1/projects/${projectId}/training-recipes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Fetches a single training recipe by id.
   */
  getTrainingRecipe: (projectId: string, id: string) =>
    request<TrainingRecipeSingleResponse>(`/api/v1/projects/${projectId}/training-recipes/${id}`),
  /**
   * Partially updates a training recipe.
   */
  updateTrainingRecipe: (projectId: string, id: string, payload: TrainingRecipeUpdatePayload) =>
    request<TrainingRecipeSingleResponse>(`/api/v1/projects/${projectId}/training-recipes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /**
   * Deletes a training recipe.
   */
  deleteTrainingRecipe: (projectId: string, id: string) =>
    request<void>(`/api/v1/projects/${projectId}/training-recipes/${id}`, { method: "DELETE" }),

  /**
   * Lists all LoRA presets for a project.
   */
  listLoraPresets: (projectId: string) =>
    request<LoraPresetListResponse>(`/api/v1/projects/${projectId}/lora-presets`),
  /**
   * Creates a LoRA preset inside a project.
   */
  createLoraPreset: (projectId: string, payload: LoraPresetCreatePayload) =>
    request<LoraPresetSingleResponse>(`/api/v1/projects/${projectId}/lora-presets`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Fetches a single LoRA preset by id.
   */
  getLoraPreset: (projectId: string, id: string) =>
    request<LoraPresetSingleResponse>(`/api/v1/projects/${projectId}/lora-presets/${id}`),
  /**
   * Partially updates a LoRA preset.
   */
  updateLoraPreset: (projectId: string, id: string, payload: LoraPresetUpdatePayload) =>
    request<LoraPresetSingleResponse>(`/api/v1/projects/${projectId}/lora-presets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /**
   * Deletes a LoRA preset.
   */
  deleteLoraPreset: (projectId: string, id: string) =>
    request<void>(`/api/v1/projects/${projectId}/lora-presets/${id}`, { method: "DELETE" }),

  /**
   * Lists all image-to-video recipes for a project.
   */
  listI2vRecipes: (projectId: string) =>
    request<I2vRecipeListResponse>(`/api/v1/projects/${projectId}/i2v-recipes`),
  /**
   * Creates an image-to-video recipe inside a project.
   */
  createI2vRecipe: (projectId: string, payload: ImageToVideoRecipeCreatePayload) =>
    request<I2vRecipeSingleResponse>(`/api/v1/projects/${projectId}/i2v-recipes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /**
   * Fetches a single image-to-video recipe by id.
   */
  getI2vRecipe: (projectId: string, id: string) =>
    request<I2vRecipeSingleResponse>(`/api/v1/projects/${projectId}/i2v-recipes/${id}`),
  /**
   * Partially updates an image-to-video recipe.
   */
  updateI2vRecipe: (projectId: string, id: string, payload: ImageToVideoRecipeUpdatePayload) =>
    request<I2vRecipeSingleResponse>(`/api/v1/projects/${projectId}/i2v-recipes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  /**
   * Deletes an image-to-video recipe.
   */
  deleteI2vRecipe: (projectId: string, id: string) =>
    request<void>(`/api/v1/projects/${projectId}/i2v-recipes/${id}`, { method: "DELETE" }),

  /**
   * Loads all five training entities for a project in a single fan-out.
   * Returns a snapshot keyed by entity kind (M4.c convenience helper).
   */
  async trainingEntities(projectId: string): Promise<TrainingEntitiesSnapshot> {
    const [characters, datasetPacks, trainingRecipes, loraPresets, i2vRecipes] = await Promise.all([
      request<CharacterListResponse>(`/api/v1/projects/${projectId}/characters`),
      request<DatasetPackListResponse>(`/api/v1/projects/${projectId}/dataset-packs`),
      request<TrainingRecipeListResponse>(`/api/v1/projects/${projectId}/training-recipes`),
      request<LoraPresetListResponse>(`/api/v1/projects/${projectId}/lora-presets`),
      request<I2vRecipeListResponse>(`/api/v1/projects/${projectId}/i2v-recipes`),
    ]);
    return {
      characters: characters.characters,
      dataset_packs: datasetPacks.dataset_packs,
      training_recipes: trainingRecipes.training_recipes,
      lora_presets: loraPresets.lora_presets,
      i2v_recipes: i2vRecipes.i2v_recipes,
    };
  },

  /**
   * Returns the Server-Sent Events URL for a training job's live progress
   * (spec §7.3). The frontend opens an EventSource on this URL to receive
   * `event: progress` / `event: done` frames instead of GET-polling
   * GET /api/v1/projects/{id}/training/{job_id}.
   */
  trainingJobStreamUrl: (projectId: string, jobId: string) =>
    `${appEnv.apiBaseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/training/${encodeURIComponent(jobId)}/stream`,

  exportProjectDownloadUrl: (projectId: string, resolveRefs = true) =>
    `${appEnv.apiBaseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/export/download?resolve_refs=${resolveRefs ? "true" : "false"}`,

  /**
   * Returns the URL for serving an asset file from the project directory.
   * Requires the backend to expose GET /api/v1/projects/{id}/assets/{assetId}/file
   * (not yet implemented — this URL will 404 until that endpoint is added).
   * The inpaint editor uses this to load the source image into the canvas.
   */
  assetFileUrl: (projectId: string, assetId: string) =>
    `${appEnv.apiBaseUrl}/api/v1/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}/file`,

  // ---------------------------------------------------------------------------
  // §5.6.2 / §5.6.3 / §5.6.6 / M5.3 — Cross-project reference endpoints
  // ---------------------------------------------------------------------------

  /**
   * Lists all cross-project references for a project with resolved statuses (spec §5.6.2).
   * Backend key: data → CrossRefListData { project_id, refs, cycle_warning }.
   */
  listProjectRefs: (projectId: string) =>
    request<CrossRefListData>(`/api/v1/projects/${encodeURIComponent(projectId)}/refs`),

  /**
   * Lists cross-project references for a single asset (spec §5.6.2).
   * Backend key: data → CrossRefListData { project_id, refs, cycle_warning }.
   */
  listAssetRefs: (projectId: string, assetId: string) =>
    request<CrossRefListData>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/refs/${encodeURIComponent(assetId)}`,
    ),

  /**
   * Materializes cross-project references into local _external/ copies (spec §5.6.6).
   * This is EXPLICIT / OPT-IN — never called automatically.
   * Backend key: data → MaterializeData { project_id, materialized, broken, total }.
   */
  materializeProjectRefs: (projectId: string, payload: MaterializePayload = {}) =>
    request<MaterializeData>(`/api/v1/projects/${encodeURIComponent(projectId)}/refs/materialize`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ---------------------------------------------------------------------------
  // §5.5 / M5.8 — Project import (drag-drop archive)
  // ---------------------------------------------------------------------------

  /**
   * Imports a *.misaka.zip project archive (spec §5.5).
   * Accepts only .zip; backend validates SHA-256 manifest and guards against zip-slip.
   * Backend key: data → ProjectImportData { project_id, project_name, collision_resolved, origin_id }.
   */
  importProject: (file: File) => {
    const formData = new FormData();
    formData.set("file", file);
    return request<ProjectImportData>("/api/v1/projects/import", {
      method: "POST",
      body: formData,
    });
  },

  // ---------------------------------------------------------------------------
  // §8.2 — Version Tree DAG endpoints (M5.6)
  // ---------------------------------------------------------------------------

  /**
   * Loads the version-tree DAG for a project (spec §8.2 / M5.1).
   * Backend key: `data` is `VersionTreeData` (nodes, cycle_detected, capped, node_cap).
   */
  projectVersionTree: (projectId: string) =>
    request<VersionTreeResponse>(`/api/v1/projects/${projectId}/versions/tree`),

  /**
   * Loads the structured diff between two asset versions (spec §8.2 / M5.1).
   * Backend key: `data` is `VersionDiffData`.
   */
  projectVersionDiff: (projectId: string, fromId: string, toId: string) =>
    request<VersionDiffResponse>(
      `/api/v1/projects/${projectId}/versions/diff?from_id=${encodeURIComponent(fromId)}&to_id=${encodeURIComponent(toId)}`,
    ),

  ApiClientError,
};
