/**
 * Unit tests for useIntegrationStore() — the tools/workers/providers polling
 * snapshot and worker install/start/stop/smoke actions, ported unchanged
 * from the pre-split stores/app.ts.
 *
 * Priority: `fetchIntegrationSnapshot`'s in-flight request dedup and
 * time-window cache reuse (INTEGRATION_REFRESH_INTERVAL_MS = 3s) — the
 * mechanism that stops a burst of callers from stacking up duplicate
 * network requests. Fake timers drive `Date.now()` deterministically.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useIntegrationStore } from "@/stores/app/integration";
import { useAppCoreStore } from "@/stores/app/core";
import type { IntegrationSnapshot } from "@/types/api";
import { MessageKey, NetworkMode, NetworkState, NetworkTone } from "@/types/enums";

function makeSnapshot(overrides: Partial<IntegrationSnapshot> = {}): IntegrationSnapshot {
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
    ...overrides,
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

describe("useIntegrationStore().fetchIntegrationSnapshot — dedup + cache", () => {
  it("dedupes concurrent in-flight calls into a single network request", async () => {
    const store = useIntegrationStore();
    const spy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    const [a, b] = await Promise.all([store.fetchIntegrationSnapshot(), store.fetchIntegrationSnapshot()]);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
  });

  it("reuses the cached snapshot within the refresh interval instead of re-fetching", async () => {
    const store = useIntegrationStore();
    const spy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.fetchIntegrationSnapshot();
    expect(spy).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000); // still inside the 3s window
    await store.fetchIntegrationSnapshot();

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("re-fetches once the refresh interval has elapsed", async () => {
    const store = useIntegrationStore();
    const spy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.fetchIntegrationSnapshot();
    await vi.advanceTimersByTimeAsync(3_100); // past the 3s window
    await store.fetchIntegrationSnapshot();

    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("force=true bypasses both the in-flight dedup and the cache window", async () => {
    const store = useIntegrationStore();
    const spy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.fetchIntegrationSnapshot();
    await store.fetchIntegrationSnapshot(true);

    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("useIntegrationStore().loadIntegrationSnapshot", () => {
  it("sets the shared success message key on completion", async () => {
    const store = useIntegrationStore();
    const coreStore = useAppCoreStore();
    coreStore.errorMessageKey = MessageKey.FAIL_500;
    vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.loadIntegrationSnapshot();

    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });
});

describe("useIntegrationStore() — worker actions force-refresh the snapshot", () => {
  it("installWorker calls the API, sets SUCCESS_ADD0, and force-refreshes", async () => {
    const store = useIntegrationStore();
    const coreStore = useAppCoreStore();
    const installSpy = vi.spyOn(apiClient, "installWorker").mockResolvedValue(undefined);
    const snapshotSpy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.installWorker("kohya-ss");

    expect(installSpy).toHaveBeenCalledWith("kohya-ss");
    // installWorker sets SUCCESS_ADD0 first, but the trailing
    // `await loadIntegrationSnapshot(true)` (identical in the pre-split
    // app.ts) always overwrites it with SUCCESS_FETCH0 before this resolves.
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(snapshotSpy).toHaveBeenCalledTimes(1);
  });

  it("startWorker / stopWorker force-refresh the snapshot after their own API call", async () => {
    const store = useIntegrationStore();
    const startSpy = vi.spyOn(apiClient, "startWorker").mockResolvedValue(undefined);
    const stopSpy = vi.spyOn(apiClient, "stopWorker").mockResolvedValue(undefined);
    const snapshotSpy = vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.startWorker("kohya-ss");
    await store.stopWorker("kohya-ss");

    expect(startSpy).toHaveBeenCalledWith("kohya-ss");
    expect(stopSpy).toHaveBeenCalledWith("kohya-ss");
    expect(snapshotSpy).toHaveBeenCalledTimes(2);
  });

  it("smokeWorker stores the result keyed by worker name and force-refreshes", async () => {
    const store = useIntegrationStore();
    const smokeResult = { worker_name: "kohya-ss", ok: true, detail: "all good", checked_at: new Date().toISOString() };
    vi.spyOn(apiClient, "smokeWorker").mockResolvedValue(smokeResult);
    vi.spyOn(apiClient, "integration").mockResolvedValue(makeSnapshot());

    await store.smokeWorker("kohya-ss");

    expect(store.workerSmokeResults["kohya-ss"]).toEqual(smokeResult);
  });
});

describe("useIntegrationStore() — networkState / networkStateTone derivation", () => {
  it.each([
    [NetworkState.ONLINE, NetworkTone.SUCCESS],
    [NetworkState.DEGRADED, NetworkTone.WARNING],
    [NetworkState.OFFLINE, NetworkTone.NEUTRAL],
  ])("maps network.state=%s to tone %s", (state, tone) => {
    const store = useIntegrationStore();
    store.integration = makeSnapshot({
      network: { mode: NetworkMode.AUTO, state, reachable: true, local_available: true, summary: "", recent_transitions: [] },
    });

    expect(store.networkState).toBe(state);
    expect(store.networkStateTone).toBe(tone);
  });
});
