import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import type { IntegrationSnapshot, WorkerSmokeResult } from "@/types/api";
import { MessageKey, NetworkMode, NetworkState, NetworkTone } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";

const isDevDiagnostics = appEnv.diagnosticsEnabled;
const INTEGRATION_REFRESH_INTERVAL_MS = 3_000;

/**
 * External-integration polling: tools/workers/providers snapshot, the
 * offline three-state network status (distinct from `core`'s core-API
 * health), and worker install/start/stop/smoke actions. Depends on `core`
 * (shared message-key status) only.
 */
export const useIntegrationStore = defineStore("app/integration", () => {
  const coreStore = useAppCoreStore();

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
  const workerSmokeResults = ref<Record<string, WorkerSmokeResult>>({});
  let integrationRequest: Promise<IntegrationSnapshot> | null = null;
  let integrationLoadedAt = 0;

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
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
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
    coreStore.lastMessageKey = MessageKey.SUCCESS_ADD0;
    coreStore.errorMessageKey = null;
    await loadIntegrationSnapshot(true);
  }

  async function startWorker(workerName: string): Promise<void> {
    await apiClient.startWorker(workerName);
    coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
    coreStore.errorMessageKey = null;
    await loadIntegrationSnapshot(true);
  }

  async function stopWorker(workerName: string): Promise<void> {
    await apiClient.stopWorker(workerName);
    coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
    coreStore.errorMessageKey = null;
    await loadIntegrationSnapshot(true);
  }

  async function smokeWorker(workerName: string): Promise<void> {
    const response = await apiClient.smokeWorker(workerName);
    workerSmokeResults.value = {
      ...workerSmokeResults.value,
      [workerName]: response,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    await loadIntegrationSnapshot(true);
  }

  return {
    integration,
    workerSmokeResults,
    networkState,
    networkStateTone,
    fetchIntegrationSnapshot,
    loadIntegrationSnapshot,
    installWorker,
    startWorker,
    stopWorker,
    smokeWorker,
  };
});
