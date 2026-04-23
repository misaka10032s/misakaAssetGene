import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import type {
  ClarifyPayload,
  ClarifyResult,
  CreateProjectPayload,
  IntegrationSnapshot,
  LocalLlmStatus,
  ModelDownloadResult,
  ProjectSummary,
  SynopsisOptimizeResult,
} from "@/types/api";
import { MessageKey, NetworkStatus, NetworkTone } from "@/types/enums";

const isDevDiagnostics = appEnv.diagnosticsEnabled;

export const useAppStore = defineStore("app", () => {
  const projects = ref<ProjectSummary[]>([]);
  const currentProjectName = ref<string | null>(null);
  const projectTypes = ref<string[]>([]);
  const networkStatus = ref<NetworkStatus>(NetworkStatus.BOOTSTRAPPING);
  const consultantResponse = ref<ClarifyResult | null>(null);
  const synopsisSuggestion = ref<SynopsisOptimizeResult | null>(null);
  const integration = ref<IntegrationSnapshot>({
    tools: [],
    workers: [],
    providers: [],
    registry_categories: [],
    model_search_paths: [],
  });
  const localLlmStatus = ref<LocalLlmStatus | null>(null);
  const lastDownloadedModel = ref<ModelDownloadResult | null>(null);
  const projectSchema = ref<string>("");
  const lastMessageKey = ref<MessageKey | null>(null);
  const errorMessageKey = ref<MessageKey | null>(null);

  const networkTone = computed<NetworkTone>(() => {
    if (networkStatus.value === NetworkStatus.CORE_ONLINE) {
      return NetworkTone.SUCCESS;
    }
    if (networkStatus.value === NetworkStatus.CORE_OFFLINE) {
      return NetworkTone.WARNING;
    }
    return NetworkTone.NEUTRAL;
  });

  /**
   * Initializes the shell state required by the root layout.
   */
  async function bootstrap(): Promise<void> {
    try {
      if (isDevDiagnostics) {
        console.info("[misaka.app] bootstrap start");
      }
      const [health, projectTypeData, projectsData, schemaData] = await Promise.all([
        apiClient.health(),
        apiClient.projectTypes(),
        apiClient.listProjects(),
        apiClient.projectSchema(),
      ]);

      networkStatus.value = health.status === "Core online" ? NetworkStatus.CORE_ONLINE : NetworkStatus.CORE_OFFLINE;
      projectTypes.value = projectTypeData.project_types;
      projects.value = projectsData.projects;
      currentProjectName.value = projectsData.current_project;
      projectSchema.value = JSON.stringify(schemaData.schema, null, 2);
      errorMessageKey.value = null;

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

  /**
   * Loads the current project list and active selection.
   */
  async function loadProjects(): Promise<void> {
    const response = await apiClient.listProjects();
    projects.value = response.projects;
    currentProjectName.value = response.current_project;
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    if (isDevDiagnostics) {
      console.info("[misaka.app] projects loaded", { count: projects.value.length });
    }
  }

  /**
   * Creates a project and refreshes the project list.
   *
   * @param payload - The project creation payload.
   */
  async function createProject(payload: CreateProjectPayload): Promise<void> {
    try {
      await apiClient.createProject(payload);
      lastMessageKey.value = MessageKey.SUCCESS_ADD0;
      await loadProjects();
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        errorMessageKey.value = error.messageKey;
      }
      throw error;
    }
  }

  /**
   * Switches the active project.
   *
   * @param projectName - The name of the project to activate.
   */
  async function selectProject(projectName: string): Promise<void> {
    await apiClient.selectProject({ name: projectName });
    currentProjectName.value = projectName;
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await loadProjects();
  }

  /**
   * Requests consultant clarification questions.
   *
   * @param payload - The selected modality and prompt.
   */
  async function requestClarification(payload: ClarifyPayload): Promise<void> {
    consultantResponse.value = await apiClient.clarify(payload);
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
    if (isDevDiagnostics) {
      console.info("[misaka.app] consultant response received", {
        modality: payload.modality,
      });
    }
  }

  /**
   * Requests an optimized synopsis suggestion.
   *
   * @param projectName - The project name entered by the user.
   * @param projectType - The selected or custom project type.
   * @param synopsis - The raw synopsis text.
   */
  async function optimizeSynopsis(projectName: string, projectType: string, synopsis: string): Promise<void> {
    synopsisSuggestion.value = await apiClient.optimizeSynopsis({
      project_name: projectName,
      project_type: projectType,
      synopsis,
    });
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
  }

  /**
   * Loads the integration snapshot used by the integration manager page.
   */
  async function loadIntegrationSnapshot(): Promise<void> {
    integration.value = await apiClient.integration();
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

  /**
   * Loads the local LLM server status.
   */
  async function loadLocalLlmStatus(): Promise<void> {
    localLlmStatus.value = await apiClient.localLlmStatus();
    lastMessageKey.value = MessageKey.SUCCESS_FETCH0;
    errorMessageKey.value = null;
  }

  /**
   * Starts the local LLM server.
   */
  async function startLocalLlm(): Promise<void> {
    localLlmStatus.value = await apiClient.startLocalLlm();
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot();
  }

  /**
   * Downloads a model into the local model path.
   *
   * @param url - A concrete Hugging Face file URL.
   */
  async function downloadLocalModel(url: string): Promise<void> {
    lastDownloadedModel.value = await apiClient.downloadLocalModel({ url });
    lastMessageKey.value = MessageKey.SUCCESS_ADD0;
    errorMessageKey.value = null;
    await loadIntegrationSnapshot();
  }

  return {
    consultantResponse,
    currentProjectName,
    errorMessageKey,
    integration,
    lastDownloadedModel,
    lastMessageKey,
    localLlmStatus,
    networkStatus,
    networkTone,
    projectSchema,
    projectTypes,
    projects,
    synopsisSuggestion,
    bootstrap,
    createProject,
    loadIntegrationSnapshot,
    loadLocalLlmStatus,
    loadProjects,
    downloadLocalModel,
    optimizeSynopsis,
    requestClarification,
    selectProject,
    startLocalLlm,
  };
});
