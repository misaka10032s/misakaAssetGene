import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import type {
  AssetRecord,
  BatchExecuteData,
  CharacterSheet,
  CharacterSheetCreatePayload,
  CharacterSheetUpdatePayload,
  ClarifyPayload,
  ClarifyResult,
  ConsultantPlanRecord,
  ConsultantSession,
  ConsultantSessionAdvancePayload,
  ConsultantSessionStartPayload,
  ConversationEntry,
  CreateProjectPayload,
  DatasetPack,
  DatasetPackCreatePayload,
  DatasetPackUpdatePayload,
  GenerationJob,
  ImageToVideoRecipe,
  ImageToVideoRecipeCreatePayload,
  ImageToVideoRecipeUpdatePayload,
  IntegrationSnapshot,
  LocalLlmStatus,
  LoraPreset,
  LoraPresetCreatePayload,
  LoraPresetUpdatePayload,
  ModelDownloadResult,
  ProjectLicenseReport,
  ProjectSummary,
  ProjectVersionGraph,
  SkippedJobInfo,
  SynopsisOptimizeResult,
  TrainingEntitiesSnapshot,
  TrainingJob,
  TrainingRecipe,
  TrainingRecipeCreatePayload,
  TrainingRecipeUpdatePayload,
  VersionDiffResponse,
  VersionTreeResponse,
  WorkerSmokeResult,
} from "@/types/api";
import { MessageKey, NetworkMode, NetworkState, NetworkStatus, NetworkTone } from "@/types/enums";

const isDevDiagnostics = appEnv.diagnosticsEnabled;
const PROJECT_DRAFT_STORAGE_KEY = "misaka.projectDraft";
const STUDIO_DRAFT_STORAGE_KEY = "misaka.studioDrafts";
const CONVERSATION_PAGE_SIZE = 30;
const INTEGRATION_REFRESH_INTERVAL_MS = 3_000;
const LOCAL_LLM_REFRESH_INTERVAL_MS = 3_000;

function readStoredJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") {
    return fallback;
  }
  const rawValue = window.localStorage.getItem(key);
  if (!rawValue) {
    return fallback;
  }
  try {
    return JSON.parse(rawValue) as T;
  } catch {
    return fallback;
  }
}

function writeStoredJson(key: string, value: unknown): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(key, JSON.stringify(value));
}

export const useAppStore = defineStore("app", () => {
  const projects = ref<ProjectSummary[]>([]);
  const currentProjectId = ref<string | null>(null);
  const projectTypes = ref<string[]>([]);
  const networkStatus = ref<NetworkStatus>(NetworkStatus.BOOTSTRAPPING);
  const consultantResponse = ref<ClarifyResult | null>(null);
  const consultantSessions = ref<Record<string, ConsultantSession>>({});
  const synopsisSuggestion = ref<SynopsisOptimizeResult | null>(null);
  const integration = ref<IntegrationSnapshot>({
    tools: [],
    workers: [],
    providers: [],
    registry_categories: [],
    model_search_paths: [],
    network: {
      mode: NetworkMode.AUTO,
      state: NetworkState.OFFLINE,
      reachable: false,
      local_available: false,
      summary: "",
      recent_transitions: [],
    },
  });
  const localLlmStatus = ref<LocalLlmStatus | null>(null);
  const lastDownloadedModel = ref<ModelDownloadResult | null>(null);
  const projectSchema = ref<string>("");
  const lastMessageKey = ref<MessageKey | null>(null);
  const errorMessageKey = ref<MessageKey | null>(null);
  const projectDraft = ref<CreateProjectPayload>(
    readStoredJson<CreateProjectPayload>(PROJECT_DRAFT_STORAGE_KEY, {
      name: "",
      type: "RPG",
      synopsis: "",
    }),
  );
  const studioDrafts = ref<Record<string, ClarifyPayload>>(
    readStoredJson<Record<string, ClarifyPayload>>(STUDIO_DRAFT_STORAGE_KEY, {}),
  );
  const projectConversations = ref<Record<string, ConversationEntry[]>>({});
  const projectConversationTotals = ref<Record<string, number>>({});
  const projectConversationOffsets = ref<Record<string, number>>({});
  const projectPlans = ref<Record<string, ConsultantPlanRecord[]>>({});
  const projectJobs = ref<Record<string, GenerationJob[]>>({});
  const projectAssets = ref<Record<string, AssetRecord[]>>({});
  const projectLicenseReports = ref<Record<string, ProjectLicenseReport>>({});
  const projectVersionGraphs = ref<Record<string, ProjectVersionGraph>>({});
  /** Per-project version-tree DAG data (spec §8.2 / M5.6). Key = project_id. */
  const projectVersionTrees = ref<Record<string, VersionTreeResponse>>({});
  const projectTrainingJobs = ref<Record<string, TrainingJob[]>>({});
  const assetDrawerOpen = ref<boolean>(false);
  const workerSmokeResults = ref<Record<string, WorkerSmokeResult>>({});
  /** Per-project training entity snapshots (spec §7.1.1 / M4.c). */
  const projectTrainingEntities = ref<Record<string, TrainingEntitiesSnapshot>>({});
  /** Last batch-execute summary — exposed so the UI can show honest skip counts (spec §5.14). */
  const lastBatchResult = ref<{ executedCount: number; skipped: SkippedJobInfo[] } | null>(null);
  let integrationRequest: Promise<IntegrationSnapshot> | null = null;
  let integrationLoadedAt = 0;
  let localLlmRequest: Promise<LocalLlmStatus> | null = null;
  let localLlmLoadedAt = 0;

  watch(
    projectDraft,
    (value) => {
      writeStoredJson(PROJECT_DRAFT_STORAGE_KEY, value);
    },
    { deep: true },
  );
  watch(
    studioDrafts,
    (value) => {
      writeStoredJson(STUDIO_DRAFT_STORAGE_KEY, value);
    },
    { deep: true },
  );

  const currentProject = computed<ProjectSummary | null>(
    () => projects.value.find((project) => project.id === currentProjectId.value) ?? null,
  );
  const currentProjectName = computed<string | null>(() => currentProject.value?.name ?? null);
  const networkTone = computed<NetworkTone>(() => {
    if (networkStatus.value === NetworkStatus.CORE_ONLINE) {
      return NetworkTone.SUCCESS;
    }
    if (networkStatus.value === NetworkStatus.CORE_OFFLINE) {
      return NetworkTone.WARNING;
    }
    return NetworkTone.NEUTRAL;
  });
  // Effective offline three-state (spec §11.5), distinct from core API health.
  const networkState = computed<NetworkState>(() => integration.value.network.state);
  const networkStateTone = computed<NetworkTone>(() => {
    if (networkState.value === NetworkState.ONLINE) {
      return NetworkTone.SUCCESS;
    }
    if (networkState.value === NetworkState.DEGRADED) {
      return NetworkTone.WARNING;
    }
    return NetworkTone.NEUTRAL;
  });

  async function bootstrap(): Promise<void> {
    try {
      if (isDevDiagnostics) {
        console.info("[misaka.app] bootstrap start");
      }
      const [health, projectTypeData, projectsData, schemaData, integrationData, localLlmData] = await Promise.all([
        apiClient.health(),
        apiClient.projectTypes(),
        apiClient.listProjects(),
        apiClient.projectSchema(),
        fetchIntegrationSnapshot(),
        fetchLocalLlmStatus(),
      ]);

      networkStatus.value = health.status === "Core online" ? NetworkStatus.CORE_ONLINE : NetworkStatus.CORE_OFFLINE;
      projectTypes.value = projectTypeData.project_types;
      projects.value = projectsData.projects;
      currentProjectId.value = projectsData.current_project_id;
      projectSchema.value = JSON.stringify(schemaData.schema, null, 2);
      integration.value = integrationData;
      localLlmStatus.value = localLlmData;
      errorMessageKey.value = null;

      if (currentProjectId.value) {
        await Promise.all([loadProjectConversation(currentProjectId.value, true), loadProjectWorkspace(currentProjectId.value)]);
      }

      if (isDevDiagnostics) {
        console.info("[misaka.app] bootstrap complete", {
          networkStatus: networkStatus.value,
          projectCount: projects.value.length,
        });
      }
    } catch (error) {
      networkStatus.value = NetworkStatus.CORE_OFFLINE;
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      if (isDevDiagnostics) {
        console.warn("[misaka.app] bootstrap failed", error);
      }
    }
  }

  async function loadProjects(): Promise<void> {
    const response = await apiClient.listProjects();
    projects.value = response.projects;
    currentProjectId.value = response.current_project_id;
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    if (isDevDiagnostics) {
      console.info("[misaka.app] projects loaded", { count: projects.value.length });
    }
  }

  async function createProject(payload: CreateProjectPayload): Promise<ProjectSummary> {
    try {
      const response = await apiClient.createProject(payload);
      projectDraft.value = {
        name: "",
        type: payload.type,
        synopsis: "",
      };
      synopsisSuggestion.value = null;
      lastMessageKey.value = MessageKey.SUCCESS_ADD0;
      await loadProjects();
      return response.project;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  async function selectProject(projectId: string): Promise<void> {
    await apiClient.selectProject({ project_id: projectId });
    currentProjectId.value = projectId;
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await Promise.all([loadProjects(), loadProjectConversation(projectId, true), loadProjectWorkspace(projectId)]);
  }

  async function loadProject(projectId: string): Promise<ProjectSummary> {
    const response = await apiClient.getProject(projectId);
    const existingIndex = projects.value.findIndex((project) => project.id === response.project.id);
    if (existingIndex >= 0) {
      projects.value[existingIndex] = response.project;
    } else {
      projects.value = [...projects.value, response.project];
    }
    return response.project;
  }

  async function loadProjectConversation(projectId: string, reset = false): Promise<void> {
    const currentOffset = reset ? 0 : projectConversationOffsets.value[projectId] ?? 0;
    const response = await apiClient.projectConversationPage(projectId, currentOffset, CONVERSATION_PAGE_SIZE);
    const existingEntries = reset ? [] : projectConversations.value[projectId] ?? [];
    const mergedEntries = [...response.entries, ...existingEntries];
    const dedupedEntries = mergedEntries.filter(
      (entry, index, collection) => collection.findIndex((candidate) => candidate.id === entry.id) === index,
    );
    projectConversations.value = {
      ...projectConversations.value,
      [projectId]: dedupedEntries,
    };
    projectConversationTotals.value = {
      ...projectConversationTotals.value,
      [projectId]: response.total,
    };
    projectConversationOffsets.value = {
      ...projectConversationOffsets.value,
      [projectId]: currentOffset + response.entries.length,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
  }

  async function loadProjectWorkspace(projectId: string): Promise<void> {
    const response = await apiClient.projectWorkspace(projectId);
    applyWorkspaceSnapshot(projectId, response);
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
  }

  function applyWorkspaceSnapshot(
    projectId: string,
    response: { jobs: GenerationJob[]; assets: AssetRecord[]; plans: ConsultantPlanRecord[] },
  ): void {
    projectJobs.value = {
      ...projectJobs.value,
      [projectId]: response.jobs,
    };
    projectAssets.value = {
      ...projectAssets.value,
      [projectId]: response.assets,
    };
    projectPlans.value = {
      ...projectPlans.value,
      [projectId]: response.plans,
    };
  }

  async function executeProjectJob(projectId: string, jobId: string): Promise<void> {
    try {
      const response = await apiClient.executeProjectJob(projectId, jobId);
      applyWorkspaceSnapshot(projectId, response);
      lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
      errorMessageKey.value = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  async function executeReadyProjectJobs(projectId: string, jobIds: string[] = []): Promise<BatchExecuteData> {
    try {
      const result = await apiClient.executeReadyProjectJobs(projectId, jobIds);
      // The new envelope wraps workspace inside result.workspace (spec §5.14).
      applyWorkspaceSnapshot(projectId, result.workspace);
      lastBatchResult.value = { executedCount: result.executed_count, skipped: result.skipped };
      lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
      errorMessageKey.value = null;
      return result;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  async function updateProjectJob(
    projectId: string,
    jobId: string,
    payload: { worker: string | null; recipe: string | null; source_asset_id: string | null; mask_asset_id: string | null },
  ): Promise<void> {
    try {
      const response = await apiClient.updateProjectJob(projectId, jobId, payload);
      applyWorkspaceSnapshot(projectId, response);
      lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
      errorMessageKey.value = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  async function importProjectAsset(
    projectId: string,
    payload: { file: File; modality: string; asset_type: string; title: string; description?: string },
  ): Promise<void> {
    try {
      const response = await apiClient.importProjectAsset(projectId, payload);
      applyWorkspaceSnapshot(projectId, response);
      lastMessageKey.value = MessageKey.SUCCESS_ADD0;
      errorMessageKey.value = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  async function loadProjectLicenseReport(projectId: string): Promise<ProjectLicenseReport> {
    const response = await apiClient.projectLicenseReport(projectId);
    projectLicenseReports.value = {
      ...projectLicenseReports.value,
      [projectId]: response,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    return response;
  }

  async function loadProjectVersionGraph(projectId: string): Promise<ProjectVersionGraph> {
    const response = await apiClient.projectVersionGraph(projectId);
    projectVersionGraphs.value = {
      ...projectVersionGraphs.value,
      [projectId]: response,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    return response;
  }

  /** Loads the version-tree DAG (spec §8.2 / M5.6). Stores result in projectVersionTrees. */
  async function loadProjectVersionTree(projectId: string): Promise<VersionTreeResponse> {
    const response = await apiClient.projectVersionTree(projectId);
    projectVersionTrees.value = {
      ...projectVersionTrees.value,
      [projectId]: response,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    return response;
  }

  /** Fetches the structured diff between two asset versions (spec §8.2 / M5.6). */
  async function loadProjectVersionDiff(
    projectId: string,
    fromId: string,
    toId: string,
  ): Promise<VersionDiffResponse> {
    const response = await apiClient.projectVersionDiff(projectId, fromId, toId);
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    return response;
  }

  async function loadProjectTrainingWorkspace(projectId: string): Promise<TrainingJob[]> {
    const response = await apiClient.projectTrainingWorkspace(projectId);
    projectTrainingJobs.value = {
      ...projectTrainingJobs.value,
      [projectId]: response.jobs,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    return response.jobs;
  }

  async function createProjectTrainingJob(
    projectId: string,
    payload: { title: string; modality: string; dataset_path: string; worker?: string | null },
  ): Promise<TrainingJob[]> {
    const response = await apiClient.createProjectTrainingJob(projectId, payload);
    projectTrainingJobs.value = {
      ...projectTrainingJobs.value,
      [projectId]: response.jobs,
    };
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    errorMessageKey.value = null;
    return response.jobs;
  }

  async function requestProjectClarification(projectId: string, payload: ClarifyPayload): Promise<void> {
    try {
      consultantResponse.value = await apiClient.clarifyProject(projectId, payload);
      studioDrafts.value = {
        ...studioDrafts.value,
        [projectId]: {
          prompt: "",
        },
      };
      await Promise.all([loadProjectConversation(projectId, true), loadProjectWorkspace(projectId)]);
      lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
      errorMessageKey.value = null;
      if (isDevDiagnostics) {
        console.info("[misaka.app] consultant response received", {
          modality: payload.modality,
          projectId,
        });
      }
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  async function resumeConsultantSession(projectId: string): Promise<ConsultantSession | null> {
    const response = await apiClient.resumeConsultantSession(projectId);
    if (response.session) {
      consultantSessions.value = {
        ...consultantSessions.value,
        [projectId]: response.session,
      };
    }
    return response.session;
  }

  async function startConsultantSession(
    projectId: string,
    payload: ConsultantSessionStartPayload,
  ): Promise<ConsultantSession> {
    const response = await apiClient.startConsultantSession(projectId, payload);
    consultantSessions.value = {
      ...consultantSessions.value,
      [projectId]: response.session,
    };
    consultantResponse.value = response.result;
    await Promise.all([loadProjectConversation(projectId, true), loadProjectWorkspace(projectId)]);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    errorMessageKey.value = null;
    return response.session;
  }

  async function advanceConsultantSession(
    projectId: string,
    payload: ConsultantSessionAdvancePayload,
  ): Promise<ConsultantSession> {
    const response = await apiClient.advanceConsultantSession(projectId, payload);
    consultantSessions.value = {
      ...consultantSessions.value,
      [projectId]: response.session,
    };
    consultantResponse.value = response.result;
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    return response.session;
  }

  async function optimizeSynopsis(projectName: string, projectType: string, synopsis: string): Promise<void> {
    try {
      synopsisSuggestion.value = await apiClient.optimizeSynopsis({
        project_name: projectName,
        project_type: projectType,
        synopsis,
      });
      lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
      errorMessageKey.value = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  function closeSynopsisSuggestion(): void {
    synopsisSuggestion.value = null;
  }

  async function fetchIntegrationSnapshot(force = false): Promise<IntegrationSnapshot> {
    const now = Date.now();
    if (!force && integrationRequest) {
      return integrationRequest;
    }
    if (!force && integrationLoadedAt > 0 && now - integrationLoadedAt < INTEGRATION_REFRESH_INTERVAL_MS) {
      return integration.value;
    }
    integrationRequest = apiClient.integration()
      .then((response) => {
        integration.value = response;
        integrationLoadedAt = Date.now();
        return response;
      })
      .finally(() => {
        integrationRequest = null;
      });
    return integrationRequest;
  }

  async function loadIntegrationSnapshot(force = false): Promise<void> {
    await fetchIntegrationSnapshot(force);
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    if (isDevDiagnostics) {
      console.info("[misaka.app] integration snapshot loaded", {
        tools: integration.value.tools.length,
        workers: integration.value.workers.length,
        providers: integration.value.providers.length,
      });
    }
  }

  async function installWorker(workerName: string): Promise<void> {
    await apiClient.installWorker(workerName);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot(true);
  }

  async function startWorker(workerName: string): Promise<void> {
    await apiClient.startWorker(workerName);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot(true);
  }

  async function stopWorker(workerName: string): Promise<void> {
    await apiClient.stopWorker(workerName);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot(true);
  }

  async function smokeWorker(workerName: string): Promise<void> {
    const response = await apiClient.smokeWorker(workerName);
    workerSmokeResults.value = {
      ...workerSmokeResults.value,
      [workerName]: response,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot(true);
  }

  async function fetchLocalLlmStatus(force = false): Promise<LocalLlmStatus> {
    const now = Date.now();
    if (!force && localLlmRequest) {
      return localLlmRequest;
    }
    if (!force && localLlmStatus.value && localLlmLoadedAt > 0 && now - localLlmLoadedAt < LOCAL_LLM_REFRESH_INTERVAL_MS) {
      return localLlmStatus.value;
    }
    localLlmRequest = apiClient.localLlmStatus()
      .then((response) => {
        localLlmStatus.value = response;
        localLlmLoadedAt = Date.now();
        return response;
      })
      .finally(() => {
        localLlmRequest = null;
      });
    return localLlmRequest;
  }

  async function loadLocalLlmStatus(force = false): Promise<void> {
    await fetchLocalLlmStatus(force);
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
  }

  async function startLocalLlm(): Promise<void> {
    localLlmStatus.value = await apiClient.startLocalLlm();
    localLlmLoadedAt = Date.now();
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot(true);
  }

  async function downloadLocalModel(url: string): Promise<void> {
    lastDownloadedModel.value = await apiClient.downloadLocalModel({ url });
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot(true);
  }

  // ---------------------------------------------------------------------------
  // §7.1.1 training entity actions (M4.c)
  // ---------------------------------------------------------------------------

  /**
   * Loads (or reloads) all five training entities for a project in parallel.
   */
  async function loadProjectTrainingEntities(projectId: string): Promise<TrainingEntitiesSnapshot> {
    const snapshot = await apiClient.trainingEntities(projectId);
    projectTrainingEntities.value = {
      ...projectTrainingEntities.value,
      [projectId]: snapshot,
    };
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    return snapshot;
  }

  /**
   * Creates a character sheet and refreshes the project snapshot.
   */
  async function createCharacter(projectId: string, payload: CharacterSheetCreatePayload): Promise<CharacterSheet> {
    const response = await apiClient.createCharacter(projectId, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    return response.character;
  }

  /**
   * Updates a character sheet and refreshes the project snapshot.
   */
  async function updateCharacter(projectId: string, id: string, payload: CharacterSheetUpdatePayload): Promise<CharacterSheet> {
    const response = await apiClient.updateCharacter(projectId, id, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    return response.character;
  }

  /**
   * Deletes a character sheet and refreshes the project snapshot.
   */
  async function deleteCharacter(projectId: string, id: string): Promise<void> {
    await apiClient.deleteCharacter(projectId, id);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
  }

  /**
   * Creates a dataset pack and refreshes the project snapshot.
   */
  async function createDatasetPack(projectId: string, payload: DatasetPackCreatePayload): Promise<DatasetPack> {
    const response = await apiClient.createDatasetPack(projectId, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    return response.dataset_pack;
  }

  /**
   * Updates a dataset pack and refreshes the project snapshot.
   */
  async function updateDatasetPack(projectId: string, id: string, payload: DatasetPackUpdatePayload): Promise<DatasetPack> {
    const response = await apiClient.updateDatasetPack(projectId, id, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    return response.dataset_pack;
  }

  /**
   * Deletes a dataset pack and refreshes the project snapshot.
   */
  async function deleteDatasetPack(projectId: string, id: string): Promise<void> {
    await apiClient.deleteDatasetPack(projectId, id);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
  }

  /**
   * Creates a training recipe and refreshes the project snapshot.
   */
  async function createTrainingRecipe(projectId: string, payload: TrainingRecipeCreatePayload): Promise<TrainingRecipe> {
    const response = await apiClient.createTrainingRecipe(projectId, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    return response.training_recipe;
  }

  /**
   * Updates a training recipe and refreshes the project snapshot.
   */
  async function updateTrainingRecipe(projectId: string, id: string, payload: TrainingRecipeUpdatePayload): Promise<TrainingRecipe> {
    const response = await apiClient.updateTrainingRecipe(projectId, id, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    return response.training_recipe;
  }

  /**
   * Deletes a training recipe and refreshes the project snapshot.
   */
  async function deleteTrainingRecipe(projectId: string, id: string): Promise<void> {
    await apiClient.deleteTrainingRecipe(projectId, id);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
  }

  /**
   * Creates a LoRA preset and refreshes the project snapshot.
   */
  async function createLoraPreset(projectId: string, payload: LoraPresetCreatePayload): Promise<LoraPreset> {
    const response = await apiClient.createLoraPreset(projectId, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    return response.lora_preset;
  }

  /**
   * Updates a LoRA preset and refreshes the project snapshot.
   */
  async function updateLoraPreset(projectId: string, id: string, payload: LoraPresetUpdatePayload): Promise<LoraPreset> {
    const response = await apiClient.updateLoraPreset(projectId, id, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    return response.lora_preset;
  }

  /**
   * Deletes a LoRA preset and refreshes the project snapshot.
   */
  async function deleteLoraPreset(projectId: string, id: string): Promise<void> {
    await apiClient.deleteLoraPreset(projectId, id);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
  }

  /**
   * Creates an image-to-video recipe and refreshes the project snapshot.
   */
  async function createI2vRecipe(projectId: string, payload: ImageToVideoRecipeCreatePayload): Promise<ImageToVideoRecipe> {
    const response = await apiClient.createI2vRecipe(projectId, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    return response.i2v_recipe;
  }

  /**
   * Updates an image-to-video recipe and refreshes the project snapshot.
   */
  async function updateI2vRecipe(projectId: string, id: string, payload: ImageToVideoRecipeUpdatePayload): Promise<ImageToVideoRecipe> {
    const response = await apiClient.updateI2vRecipe(projectId, id, payload);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    return response.i2v_recipe;
  }

  /**
   * Deletes an image-to-video recipe and refreshes the project snapshot.
   */
  async function deleteI2vRecipe(projectId: string, id: string): Promise<void> {
    await apiClient.deleteI2vRecipe(projectId, id);
    await loadProjectTrainingEntities(projectId);
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
  }

  function updateProjectDraft(payload: CreateProjectPayload): void {
    projectDraft.value = { ...payload };
  }

  function getStudioDraft(projectId: string | null): ClarifyPayload {
    if (!projectId) {
      return {
        prompt: "",
      };
    }
    return (
      studioDrafts.value[projectId] ?? {
        prompt: "",
      }
    );
  }

  function updateStudioDraft(projectId: string, payload: ClarifyPayload): void {
    studioDrafts.value = {
      ...studioDrafts.value,
      [projectId]: { ...payload },
    };
  }

  function setAssetDrawerOpen(nextValue: boolean): void {
    assetDrawerOpen.value = nextValue;
  }

  return {
    assetDrawerOpen,
    consultantResponse,
    consultantSessions,
    currentProject,
    currentProjectId,
    currentProjectName,
    errorMessageKey,
    integration,
    lastDownloadedModel,
    lastMessageKey,
    localLlmStatus,
    networkState,
    networkStateTone,
    networkStatus,
    networkTone,
    projectAssets,
    projectLicenseReports,
    projectConversations,
    projectConversationOffsets,
    projectConversationTotals,
    projectDraft,
    projectJobs,
    projectPlans,
    projectTrainingEntities,
    projectTrainingJobs,
    projectVersionGraphs,
    projectVersionTrees,
    projectSchema,
    projectTypes,
    projects,
    studioDrafts,
    synopsisSuggestion,
    workerSmokeResults,
    lastBatchResult,
    bootstrap,
    closeSynopsisSuggestion,
    createProject,
    createProjectTrainingJob,
    downloadLocalModel,
    executeProjectJob,
    executeReadyProjectJobs,
    getStudioDraft,
    importProjectAsset,
    installWorker,
    loadIntegrationSnapshot,
    loadLocalLlmStatus,
    loadProject,
    loadProjectConversation,
    loadProjectLicenseReport,
    loadProjectTrainingWorkspace,
    loadProjectVersionGraph,
    loadProjectVersionTree,
    loadProjectVersionDiff,
    loadProjectWorkspace,
    loadProjects,
    advanceConsultantSession,
    optimizeSynopsis,
    requestProjectClarification,
    resumeConsultantSession,
    selectProject,
    startConsultantSession,
    setAssetDrawerOpen,
    smokeWorker,
    startLocalLlm,
    startWorker,
    stopWorker,
    updateProjectJob,
    updateProjectDraft,
    updateStudioDraft,
    loadProjectTrainingEntities,
    createCharacter,
    updateCharacter,
    deleteCharacter,
    createDatasetPack,
    updateDatasetPack,
    deleteDatasetPack,
    createTrainingRecipe,
    updateTrainingRecipe,
    deleteTrainingRecipe,
    createLoraPreset,
    updateLoraPreset,
    deleteLoraPreset,
    createI2vRecipe,
    updateI2vRecipe,
    deleteI2vRecipe,
  };
});
