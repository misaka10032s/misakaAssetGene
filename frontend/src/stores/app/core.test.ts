/**
 * Unit tests for useAppCoreStore() — the projects list, currentProjectId ->
 * currentProject derivation, network-status/tone derivation, the shared
 * message-key status pair every other split store also writes to, and the
 * `synopsisSuggestion` ref (see core.ts's doc comment for why it lives here
 * instead of in `consultant`).
 *
 * Written against the pre-split stores/app.ts behaviour (merge-base copy).
 * `createProject` there also reset the synopsis suggestion
 * (`synopsisSuggestion.value = null`) — a behaviour regression was found
 * where `core.ts`'s `createProject` no longer did this after the split;
 * fixed by relocating `synopsisSuggestion` ownership to this store (see the
 * `createProject` describe block below for the regression test).
 *
 * apiClient is mocked at the network boundary only; the stores under test
 * are never mocked.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useAppCoreStore } from "@/stores/app/core";
import { useConsultantStore } from "@/stores/app/consultant";
import { useDraftsStore } from "@/stores/app/drafts";
import type { ProjectSummary } from "@/types/api";
import { MessageKey, NetworkStatus, NetworkTone } from "@/types/enums";

function makeProject(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return { id: "p1", name: "Project One", type: "RPG", synopsis: "", ...overrides };
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useAppCoreStore() — projects list + loadProjects", () => {
  it("loads projects and the current project id, and reports success", async () => {
    const store = useAppCoreStore();
    vi.spyOn(apiClient, "listProjects").mockResolvedValue({
      projects: [makeProject({ id: "p1" }), makeProject({ id: "p2", name: "Two" })],
      current_project_id: "p2",
    });

    await store.loadProjects();

    expect(store.projects.map((p) => p.id)).toEqual(["p1", "p2"]);
    expect(store.currentProjectId).toBe("p2");
    expect(store.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(store.errorMessageKey).toBeNull();
  });
});

describe("useAppCoreStore() — currentProject / currentProjectName derivation", () => {
  it("derives the matching project when currentProjectId is in the list", () => {
    const store = useAppCoreStore();
    store.projects = [makeProject({ id: "p1", name: "Alpha" }), makeProject({ id: "p2", name: "Beta" })];
    store.currentProjectId = "p2";

    expect(store.currentProject?.id).toBe("p2");
    expect(store.currentProjectName).toBe("Beta");
  });

  it("resolves to null (not throwing) when currentProjectId names a project not in the list", () => {
    const store = useAppCoreStore();
    store.projects = [makeProject({ id: "p1" })];
    store.currentProjectId = "ghost-id";

    expect(store.currentProject).toBeNull();
    expect(store.currentProjectName).toBeNull();
  });

  it("resolves to null when no project is selected", () => {
    const store = useAppCoreStore();
    store.projects = [makeProject({ id: "p1" })];
    store.currentProjectId = null;

    expect(store.currentProject).toBeNull();
  });
});

describe("useAppCoreStore() — networkTone derivation", () => {
  it.each([
    [NetworkStatus.CORE_ONLINE, NetworkTone.SUCCESS],
    [NetworkStatus.CORE_OFFLINE, NetworkTone.WARNING],
    [NetworkStatus.BOOTSTRAPPING, NetworkTone.NEUTRAL],
  ])("maps %s to %s", (status, tone) => {
    const store = useAppCoreStore();
    store.networkStatus = status;
    expect(store.networkTone).toBe(tone);
  });
});

describe("useAppCoreStore().createProject", () => {
  it("creates the project, resets the draft, refreshes the list, and returns the new project", async () => {
    const store = useAppCoreStore();
    const draftsStore = useDraftsStore();
    draftsStore.projectDraft = { name: "stale", type: "RPG", synopsis: "stale synopsis" };
    const created = makeProject({ id: "new-1", name: "New Project", type: "VN" });
    const createSpy = vi.spyOn(apiClient, "createProject").mockResolvedValue({ project: created });
    const listSpy = vi
      .spyOn(apiClient, "listProjects")
      .mockResolvedValue({ projects: [created], current_project_id: "new-1" });

    const payload = { name: "New Project", type: "VN", synopsis: "..." };
    const result = await store.createProject(payload);

    expect(createSpy).toHaveBeenCalledWith(payload);
    expect(result).toEqual(created);
    expect(draftsStore.projectDraft).toEqual({ name: "", type: "VN", synopsis: "" });
    // createProject sets SUCCESS_ADD0, but the trailing `await loadProjects()`
    // (present identically in the pre-split app.ts) always overwrites it with
    // SUCCESS_FETCH0 before this call resolves — asserting SUCCESS_FETCH0 here
    // matches the ORIGINAL behaviour, not just the new code.
    expect(store.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(store.projects.map((p) => p.id)).toEqual(["new-1"]);
  });

  it("on failure, records the error message key and rethrows without touching the draft", async () => {
    const store = useAppCoreStore();
    const draftsStore = useDraftsStore();
    draftsStore.projectDraft = { name: "kept", type: "RPG", synopsis: "kept" };
    vi.spyOn(apiClient, "createProject").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_500));

    await expect(store.createProject({ name: "x", type: "RPG", synopsis: "" })).rejects.toBeInstanceOf(
      apiClient.ApiClientError,
    );

    expect(store.errorMessageKey).toBe(MessageKey.FAIL_500);
    expect(draftsStore.projectDraft).toEqual({ name: "kept", type: "RPG", synopsis: "kept" });
  });

  it("clears a stale synopsis suggestion from the previous project on success (regression: pre-split app.ts did this via synopsisSuggestion.value = null)", async () => {
    const store = useAppCoreStore();
    // synopsisSuggestion is owned by core but produced/displayed via
    // useConsultantStore() — assert through BOTH handles to prove the
    // relocation didn't just move the symptom (core.test.ts) while leaving
    // the consumer (useConsultantStore()) still seeing the stale value.
    const consultantStore = useConsultantStore();
    consultantStore.synopsisSuggestion = {
      optimized_synopsis: "stale suggestion from the previous project",
      strategy: "rewrite",
      provider: "ollama",
    };
    const created = makeProject({ id: "new-1", name: "New Project", type: "VN" });
    vi.spyOn(apiClient, "createProject").mockResolvedValue({ project: created });
    vi.spyOn(apiClient, "listProjects").mockResolvedValue({ projects: [created], current_project_id: "new-1" });

    await store.createProject({ name: "New Project", type: "VN", synopsis: "..." });

    expect(store.synopsisSuggestion).toBeNull();
    expect(consultantStore.synopsisSuggestion).toBeNull();
  });

  it("on failure, leaves an existing synopsis suggestion untouched", async () => {
    const store = useAppCoreStore();
    const consultantStore = useConsultantStore();
    consultantStore.synopsisSuggestion = {
      optimized_synopsis: "kept suggestion",
      strategy: "rewrite",
      provider: "ollama",
    };
    vi.spyOn(apiClient, "createProject").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_500));

    await expect(store.createProject({ name: "x", type: "RPG", synopsis: "" })).rejects.toBeInstanceOf(
      apiClient.ApiClientError,
    );

    expect(consultantStore.synopsisSuggestion?.optimized_synopsis).toBe("kept suggestion");
  });
});

describe("useAppCoreStore().selectProject", () => {
  it("selects the project, refreshes the list, and runs the caller's onSelected callback", async () => {
    const store = useAppCoreStore();
    vi.spyOn(apiClient, "selectProject").mockResolvedValue({ project: makeProject({ id: "p2" }) });
    const listSpy = vi
      .spyOn(apiClient, "listProjects")
      .mockResolvedValue({ projects: [makeProject({ id: "p2" })], current_project_id: "p2" });
    const onSelected = vi.fn().mockResolvedValue(undefined);

    await store.selectProject("p2", onSelected);

    expect(store.currentProjectId).toBe("p2");
    // selectProject sets SUCCESS_SWITCH0 first, but `Promise.all([loadProjects(), ...])`
    // (identically present in the pre-split app.ts) always lets loadProjects overwrite it
    // with SUCCESS_FETCH0 before this call resolves — matches ORIGINAL behaviour.
    expect(store.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(listSpy).toHaveBeenCalledTimes(1);
    expect(onSelected).toHaveBeenCalledWith("p2");
  });
});

describe("useAppCoreStore().loadProject", () => {
  it("replaces an existing project in place when its id is already in the list", async () => {
    const store = useAppCoreStore();
    store.projects = [makeProject({ id: "p1", name: "Old Name" }), makeProject({ id: "p2" })];
    const updated = makeProject({ id: "p1", name: "Renamed" });
    vi.spyOn(apiClient, "getProject").mockResolvedValue({ project: updated });

    const result = await store.loadProject("p1");

    expect(result).toEqual(updated);
    expect(store.projects).toHaveLength(2);
    expect(store.projects.find((p) => p.id === "p1")?.name).toBe("Renamed");
  });

  it("appends the project when its id is not already in the list", async () => {
    const store = useAppCoreStore();
    store.projects = [makeProject({ id: "p1" })];
    const fresh = makeProject({ id: "p-new", name: "Fresh" });
    vi.spyOn(apiClient, "getProject").mockResolvedValue({ project: fresh });

    await store.loadProject("p-new");

    expect(store.projects.map((p) => p.id)).toEqual(["p1", "p-new"]);
  });
});
