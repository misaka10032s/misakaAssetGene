/**
 * Unit tests for useVersionsStore() — per-project version graphs/trees/diffs
 * (spec §8.2 / M5.6), ported unchanged from the pre-split stores/app.ts.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useVersionsStore } from "@/stores/app/versions";
import { useAppCoreStore } from "@/stores/app/core";
import { MessageKey } from "@/types/enums";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useVersionsStore().loadProjectVersionGraph", () => {
  it("stores the graph keyed by project id and reports success", async () => {
    const store = useVersionsStore();
    const coreStore = useAppCoreStore();
    const graph = { nodes: [], edges: [] };
    vi.spyOn(apiClient, "projectVersionGraph").mockResolvedValue(graph);

    const result = await store.loadProjectVersionGraph("p1");

    expect(store.projectVersionGraphs.p1).toEqual(graph);
    expect(result).toEqual(graph);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });
});

describe("useVersionsStore().loadProjectVersionTree", () => {
  it("stores the tree keyed by project id and reports success", async () => {
    const store = useVersionsStore();
    const tree = { nodes: [], cycle_detected: false, capped: false, node_cap: 500 };
    vi.spyOn(apiClient, "projectVersionTree").mockResolvedValue(tree);

    const result = await store.loadProjectVersionTree("p1");

    expect(store.projectVersionTrees.p1).toEqual(tree);
    expect(result).toEqual(tree);
  });

  it("surfaces cycle_detected/capped flags exactly as the backend returns them", async () => {
    const store = useVersionsStore();
    vi.spyOn(apiClient, "projectVersionTree").mockResolvedValue({
      nodes: [],
      cycle_detected: true,
      capped: true,
      node_cap: 100,
    });

    await store.loadProjectVersionTree("p1");

    expect(store.projectVersionTrees.p1?.cycle_detected).toBe(true);
    expect(store.projectVersionTrees.p1?.capped).toBe(true);
    expect(store.projectVersionTrees.p1?.node_cap).toBe(100);
  });
});

describe("useVersionsStore().loadProjectVersionDiff", () => {
  it("returns the diff without caching it in store state (it is not a keyed ref)", async () => {
    const store = useVersionsStore();
    const coreStore = useAppCoreStore();
    const diff = {
      from_id: "v1",
      to_id: "v2",
      prompt_delta: "added a hat",
      param_delta: {},
      mask_diff: null,
      recipe_diff: null,
      strategy_diff: null,
      backend_diff: null,
    };
    const spy = vi.spyOn(apiClient, "projectVersionDiff").mockResolvedValue(diff);

    const result = await store.loadProjectVersionDiff("p1", "v1", "v2");

    expect(spy).toHaveBeenCalledWith("p1", "v1", "v2");
    expect(result).toEqual(diff);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
  });
});
