import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import type { LocalLlmStatus, ModelDownloadResult } from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";
import { useIntegrationStore } from "@/stores/app/integration";

const LOCAL_LLM_REFRESH_INTERVAL_MS = 3_000;

/**
 * Local-LLM (Ollama) status and model-download flow. Depends on `core`
 * (shared message-key status) and `integration` (starting the local LLM or
 * downloading a model both force-refresh the integration snapshot, exactly
 * as the previous inline implementation did) — one-directional,
 * `integration` does not depend back on this store.
 */
export const useLocalLlmStore = defineStore("app/localLlm", () => {
  const coreStore = useAppCoreStore();
  const integrationStore = useIntegrationStore();

  const localLlmStatus = ref<LocalLlmStatus | null>(null);
  const lastDownloadedModel = ref<ModelDownloadResult | null>(null);
  let localLlmRequest: Promise<LocalLlmStatus> | null = null;
  let localLlmLoadedAt = 0;

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
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
  }

  async function startLocalLlm(): Promise<void> {
    localLlmStatus.value = await apiClient.startLocalLlm();
    localLlmLoadedAt = Date.now();
    coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
    coreStore.errorMessageKey = null;
    await integrationStore.loadIntegrationSnapshot(true);
  }

  async function downloadLocalModel(url: string): Promise<void> {
    lastDownloadedModel.value = await apiClient.downloadLocalModel({ url });
    coreStore.lastMessageKey = MessageKey.SUCCESS_ADD0;
    coreStore.errorMessageKey = null;
    await integrationStore.loadIntegrationSnapshot(true);
  }

  return {
    localLlmStatus,
    lastDownloadedModel,
    fetchLocalLlmStatus,
    loadLocalLlmStatus,
    startLocalLlm,
    downloadLocalModel,
  };
});
