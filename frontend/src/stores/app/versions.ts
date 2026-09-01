import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import type { ProjectVersionGraph, VersionDiffResponse, VersionTreeResponse } from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";

/**
 * Per-project version graphs/trees/diffs (spec §8.2 / M5.6). Depends on
 * `core` (shared message-key status) only.
 */
export const useVersionsStore = defineStore("app/versions", () => {
  const coreStore = useAppCoreStore();

  const projectVersionGraphs = ref<Record<string, ProjectVersionGraph>>({});
  /** Per-project version-tree DAG data (spec §8.2 / M5.6). Key = project_id. */
  const projectVersionTrees = ref<Record<string, VersionTreeResponse>>({});

  async function loadProjectVersionGraph(projectId: string): Promise<ProjectVersionGraph> {
    const response = await apiClient.projectVersionGraph(projectId);
    projectVersionGraphs.value = {
      ...projectVersionGraphs.value,
      [projectId]: response,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    return response;
  }

  /** Loads the version-tree DAG (spec §8.2 / M5.6). Stores result in projectVersionTrees. */
  async function loadProjectVersionTree(projectId: string): Promise<VersionTreeResponse> {
    const response = await apiClient.projectVersionTree(projectId);
    projectVersionTrees.value = {
      ...projectVersionTrees.value,
      [projectId]: response,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    return response;
  }

  /** Fetches the structured diff between two asset versions (spec §8.2 / M5.6). */
  async function loadProjectVersionDiff(
    projectId: string,
    fromId: string,
    toId: string,
  ): Promise<VersionDiffResponse> {
    const response = await apiClient.projectVersionDiff(projectId, fromId, toId);
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    return response;
  }

  return {
    projectVersionGraphs,
    projectVersionTrees,
    loadProjectVersionGraph,
    loadProjectVersionTree,
    loadProjectVersionDiff,
  };
});
