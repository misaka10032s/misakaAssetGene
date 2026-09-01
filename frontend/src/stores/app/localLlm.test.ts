/**
 * Unit tests for useLocalLlmStore() — local-LLM (Ollama) status polling and
 * the model-download flow, ported unchanged from the pre-split
 * stores/app.ts.
 *
 * Priority: `fetchLocalLlmStatus`'s in-flight dedup + LOCAL_LLM_REFRESH_INTERVAL_MS
 * (3s) cache-window reuse (fake timers), and that `startLocalLlm` /
 * `downloadLocalModel` force-refresh the CROSS-STORE integration snapshot
 * afterward, exactly as the previous inline implementation did.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useLocalLlmStore } from "@/stores/app/localLlm";
import { useIntegrationStore } from "@/stores/app/integration";
import { useAppCoreStore } from "@/stores/app/core";
import type { LocalLlmStatus } from "@/types/api";
import { MessageKey, NetworkMode, NetworkState } from "@/types/enums";

function makeStatus(overrides: Partial<LocalLlmStatus> = {}): LocalLlmStatus {
  return {
    server: "ollama",
    base_url: "http://127.0.0.1:11434",
    is_running: false,
    managed_by_app: true,
    executable_found: true,
    executable_path: "/usr/local/bin/ollama",
    provider_order: ["ollama"],
    ...overrides,
  };
}

function makeIntegrationSnapshot() {
  return {
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
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useLocalLlmStore().fetchLocalLlmStatus — dedup + cache", () => {
  it("dedupes concurrent in-flight calls into a single network request", async () => {
    const store = useLocalLlmStore();
    const spy = vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue(makeStatus());

    const [a, b] = await Promise.all([store.fetchLocalLlmStatus(), store.fetchLocalLlmStatus()]);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
  });

  it("reuses the cached status within the refresh interval instead of re-fetching", async () => {
    const store = useLocalLlmStore();
    const spy = vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue(makeStatus());

    await store.fetchLocalLlmStatus();
    await vi.advanceTimersByTimeAsync(1_000); // inside the 3s window
    await store.fetchLocalLlmStatus();

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("re-fetches once the refresh interval has elapsed", async () => {
    const store = useLocalLlmStore();
    const spy = vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue(makeStatus());

    await store.fetchLocalLlmStatus();
    await vi.advanceTimersByTimeAsync(3_100); // past the 3s window
    await store.fetchLocalLlmStatus();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("force=true bypasses both the in-flight dedup and the cache window", async () => {
    const store = useLocalLlmStore();
    const spy = vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue(makeStatus());

    await store.fetchLocalLlmStatus();
    await store.fetchLocalLlmStatus(true);

    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("useLocalLlmStore().loadLocalLlmStatus", () => {
  it("sets the shared success message key on completion", async () => {
    const store = useLocalLlmStore();
    const coreStore = useAppCoreStore();
    coreStore.errorMessageKey = MessageKey.FAIL_500;
    vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue(makeStatus());

    await store.loadLocalLlmStatus();

    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });
});

describe("useLocalLlmStore() — cross-store integration refresh", () => {
  it("startLocalLlm sets the running status and force-refreshes the integration snapshot", async () => {
    const store = useLocalLlmStore();
    const integrationStore = useIntegrationStore();
    vi.spyOn(apiClient, "startLocalLlm").mockResolvedValue(makeStatus({ is_running: true }));
    const integrationSpy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeIntegrationSnapshot());

    await store.startLocalLlm();

    expect(store.localLlmStatus?.is_running).toBe(true);
    expect(integrationSpy).toHaveBeenCalledTimes(1);
    void integrationStore; // referenced only to document which store owns the refresh
  });

  it("downloadLocalModel stores the download result and force-refreshes the integration snapshot", async () => {
    const store = useLocalLlmStore();
    const downloadResult = { filename: "model.safetensors", saved_path: "/models/model.safetensors", source_url: "https://example.com/model" };
    vi.spyOn(apiClient, "downloadLocalModel").mockResolvedValue(downloadResult);
    const integrationSpy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeIntegrationSnapshot());

    await store.downloadLocalModel("https://example.com/model");

    expect(store.lastDownloadedModel).toEqual(downloadResult);
    expect(integrationSpy).toHaveBeenCalledTimes(1);
  });
});
