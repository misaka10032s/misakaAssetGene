import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import type { CreateProjectPayload, ProjectSummary } from "@/types/api";
import { MessageKey, NetworkStatus, NetworkTone } from "@/types/enums";

import { useDraftsStore } from "@/stores/app/drafts";

const isDevDiagnostics = appEnv.diagnosticsEnabled;

/**
 * Root project store: the project list, which project is "current", network
 * health, and the shared `lastMessageKey`/`errorMessageKey` status pair that
 * every other split store also writes to on success/failure.
 *
 * This is the one store every other `stores/app/*` module depends on (for
 * `currentProjectId`/`currentProject` reads and for reporting the shared
 * message-key status) — it must never import any of them back, or the
 * import graph gains a cycle (G4).
 *
 * The one exception is `drafts`: `createProject` resets the project draft on
 * success, so this store depends on `drafts` (one-directional; `drafts` does
 * not depend back on `core`).
 */
export const useAppCoreStore = defineStore("app/core", () => {
  const projects = ref<ProjectSummary[]>([]);
  const currentProjectId = ref<string | null>(null);
  const projectTypes = ref<string[]>([]);
  const networkStatus = ref<NetworkStatus>(NetworkStatus.BOOTSTRAPPING);
  const projectSchema = ref<string>("");
  const lastMessageKey = ref<MessageKey | null>(null);
  const errorMessageKey = ref<MessageKey | null>(null);

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
      const draftsStore = useDraftsStore();
      draftsStore.projectDraft = {
        name: "",
        type: payload.type,
        synopsis: "",
      };
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

  async function selectProject(
    projectId: string,
    onSelected: (projectId: string) => Promise<unknown>,
  ): Promise<void> {
    await apiClient.selectProject({ project_id: projectId });
    currentProjectId.value = projectId;
    lastMessageKey.value = MessageKey.SUCCESS_SWITCH0;
    errorMessageKey.value = null;
    await Promise.all([loadProjects(), onSelected(projectId)]);
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

  return {
    projects,
    currentProjectId,
    projectTypes,
    networkStatus,
    projectSchema,
    lastMessageKey,
    errorMessageKey,
    currentProject,
    currentProjectName,
    networkTone,
    loadProjects,
    createProject,
    selectProject,
    loadProject,
  };
});
