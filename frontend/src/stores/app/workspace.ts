import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import type {
  AssetRecord,
  BatchExecuteData,
  ConsultantPlanRecord,
  GenerationJob,
  RefinePayload,
  SkippedJobInfo,
} from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";

/**
 * A project's execution workspace: generation jobs, assets, consultant plans,
 * the asset-refine flow, and the asset-drawer UI flag. Depends on `core`
 * (shared message-key status) only.
 */
export const useWorkspaceStore = defineStore("app/workspace", () => {
  const coreStore = useAppCoreStore();

  const projectPlans = ref<Record<string, ConsultantPlanRecord[]>>({});
  const projectJobs = ref<Record<string, GenerationJob[]>>({});
  const projectAssets = ref<Record<string, AssetRecord[]>>({});
  const assetDrawerOpen = ref<boolean>(false);
  /** Last batch-execute summary — exposed so the UI can show honest skip counts (spec §5.14). */
  const lastBatchResult = ref<{ executedCount: number; skipped: SkippedJobInfo[] } | null>(null);

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

  async function loadProjectWorkspace(projectId: string): Promise<void> {
    const response = await apiClient.projectWorkspace(projectId);
    applyWorkspaceSnapshot(projectId, response);
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
  }

  async function executeProjectJob(projectId: string, jobId: string): Promise<void> {
    try {
      const response = await apiClient.executeProjectJob(projectId, jobId);
      applyWorkspaceSnapshot(projectId, response);
      coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
      coreStore.errorMessageKey = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
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
      coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
      coreStore.errorMessageKey = null;
      return result;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
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
      coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
      coreStore.errorMessageKey = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
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
      coreStore.lastMessageKey = MessageKey.SUCCESS_ADD0;
      coreStore.errorMessageKey = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
      }
      throw error;
    }
  }

  /**
   * Creates a refine job for an asset, wiring the §6.2 strategy decision tree
   * and the inpaint mask upload flow (spec §5.11 / M5.9).
   */
  async function refineAsset(projectId: string, assetId: string, payload: RefinePayload): Promise<void> {
    try {
      const response = await apiClient.refineAsset(projectId, assetId, payload);
      applyWorkspaceSnapshot(projectId, response);
      coreStore.lastMessageKey = MessageKey.SUCCESS_ADD0;
      coreStore.errorMessageKey = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
      }
      throw error;
    }
  }

  function setAssetDrawerOpen(nextValue: boolean): void {
    assetDrawerOpen.value = nextValue;
  }

  return {
    projectPlans,
    projectJobs,
    projectAssets,
    assetDrawerOpen,
    lastBatchResult,
    applyWorkspaceSnapshot,
    loadProjectWorkspace,
    executeProjectJob,
    executeReadyProjectJobs,
    updateProjectJob,
    importProjectAsset,
    refineAsset,
    setAssetDrawerOpen,
  };
});
