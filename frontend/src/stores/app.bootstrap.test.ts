/**
 * Unit tests for useAppStore()'s own orchestration logic — the thin facade
 * code that lives in stores/app.ts itself rather than in any single domain
 * store: `bootstrap` (fans out across core/integration/localLlm, then loads
 * conversation+workspace for the current project) and `selectProject`
 * (composes core's narrow select with the conversation+workspace reload).
 *
 * Complements app.subscribeTrainingJob.test.ts (kept untouched), which
 * exercises subscribeTrainingJob but not these two facade functions.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useAppStore } from "@/stores/app";
import { NetworkMode, NetworkState, NetworkStatus } from "@/types/enums";

function stubBootstrapNetwork(overrides: { healthStatus?: string } = {}) {
  vi.spyOn(apiClient, "health").mockResolvedValue({
    status: overrides.healthStatus ?? "Core online",
    repo_root: "/repo",
    environment: "dev",
  });
  vi.spyOn(apiClient, "projectTypes").mockResolvedValue({ project_types: ["RPG"] });
  vi.spyOn(apiClient, "listProjects").mockResolvedValue({ projects: [], current_project_id: null });
  vi.spyOn(apiClient, "projectSchema").mockResolvedValue({ schema: {} });
  vi.spyOn(apiClient, "integration").mockResolvedValue({
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
  vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue({
    server: "ollama",
    base_url: "http://127.0.0.1:11434",
    is_running: false,
    managed_by_app: true,
    executable_found: true,
    executable_path: null,
    provider_order: [],
  });
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useAppStore().bootstrap", () => {
  it("on success, marks the core API online and loads project types/schema", async () => {
    const store = useAppStore();
    stubBootstrapNetwork({ healthStatus: "Core online" });

    await store.bootstrap();

    expect(store.networkStatus).toBe(NetworkStatus.CORE_ONLINE);
    expect(store.projectTypes).toEqual(["RPG"]);
    expect(store.errorMessageKey).toBeNull();
  });

  it("marks core offline when the health check reports anything other than Core online", async () => {
    const store = useAppStore();
    stubBootstrapNetwork({ healthStatus: "Core degraded" });

    await store.bootstrap();

    expect(store.networkStatus).toBe(NetworkStatus.CORE_OFFLINE);
  });

  it("loads the current project's conversation + workspace when bootstrap finds one already selected", async () => {
    const store = useAppStore();
    vi.spyOn(apiClient, "health").mockResolvedValue({ status: "Core online", repo_root: "/repo", environment: "dev" });
    vi.spyOn(apiClient, "projectTypes").mockResolvedValue({ project_types: [] });
    vi.spyOn(apiClient, "listProjects").mockResolvedValue({
      projects: [{ id: "p1", name: "Existing", type: "RPG", synopsis: "" }],
      current_project_id: "p1",
    });
    vi.spyOn(apiClient, "projectSchema").mockResolvedValue({ schema: {} });
    vi.spyOn(apiClient, "integration").mockResolvedValue({
      tools: [],
      workers: [],
      providers: [],
      registry_categories: [],
      model_search_paths: [],
      network: { mode: NetworkMode.AUTO, state: NetworkState.OFFLINE, reachable: false, local_available: false, summary: "", recent_transitions: [] },
    });
    vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue({
      server: "ollama",
      base_url: "http://127.0.0.1:11434",
      is_running: false,
      managed_by_app: true,
      executable_found: true,
      executable_path: null,
      provider_order: [],
    });
    const convSpy = vi
      .spyOn(apiClient, "projectConversationPage")
      .mockResolvedValue({ entries: [], total: 0, offset: 0, limit: 30, has_more: false });
    const workspaceSpy = vi.spyOn(apiClient, "projectWorkspace").mockResolvedValue({ jobs: [], assets: [], plans: [] });

    await store.bootstrap();

    expect(convSpy).toHaveBeenCalledWith("p1", 0, 30);
    expect(workspaceSpy).toHaveBeenCalledWith("p1");
  });

  it("on a network error, marks core offline and surfaces the ApiClientError's message key", async () => {
    const store = useAppStore();
    vi.spyOn(apiClient, "health").mockRejectedValue(new apiClient.ApiClientError("message.fail.500" as never));
    vi.spyOn(apiClient, "projectTypes").mockResolvedValue({ project_types: [] });
    vi.spyOn(apiClient, "listProjects").mockResolvedValue({ projects: [], current_project_id: null });
    vi.spyOn(apiClient, "projectSchema").mockResolvedValue({ schema: {} });
    vi.spyOn(apiClient, "integration").mockResolvedValue({
      tools: [],
      workers: [],
      providers: [],
      registry_categories: [],
      model_search_paths: [],
      network: { mode: NetworkMode.AUTO, state: NetworkState.OFFLINE, reachable: false, local_available: false, summary: "", recent_transitions: [] },
    });
    vi.spyOn(apiClient, "localLlmStatus").mockResolvedValue({
      server: "ollama",
      base_url: "http://127.0.0.1:11434",
      is_running: false,
      managed_by_app: true,
      executable_found: true,
      executable_path: null,
      provider_order: [],
    });

    await store.bootstrap();

    expect(store.networkStatus).toBe(NetworkStatus.CORE_OFFLINE);
    expect(store.errorMessageKey).toBe("message.fail.500");
  });
});

describe("useAppStore().selectProject", () => {
  it("selects the project and reloads its conversation + workspace", async () => {
    const store = useAppStore();
    vi.spyOn(apiClient, "selectProject").mockResolvedValue({
      project: { id: "p2", name: "Two", type: "RPG", synopsis: "" },
    });
    vi.spyOn(apiClient, "listProjects").mockResolvedValue({
      projects: [{ id: "p2", name: "Two", type: "RPG", synopsis: "" }],
      current_project_id: "p2",
    });
    const convSpy = vi
      .spyOn(apiClient, "projectConversationPage")
      .mockResolvedValue({ entries: [], total: 0, offset: 0, limit: 30, has_more: false });
    const workspaceSpy = vi.spyOn(apiClient, "projectWorkspace").mockResolvedValue({ jobs: [], assets: [], plans: [] });

    await store.selectProject("p2");

    expect(store.currentProjectId).toBe("p2");
    expect(convSpy).toHaveBeenCalledWith("p2", 0, 30);
    expect(workspaceSpy).toHaveBeenCalledWith("p2");
  });
});
