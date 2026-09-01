/**
 * Unit tests for useConsultantStore() — consultant sessions and the
 * synopsis-optimize suggestion flow, ported unchanged from the pre-split
 * stores/app.ts. Priority: the load/refresh orchestration
 * (requestProjectClarification / startConsultantSession fan out to
 * conversations + workspace, and clear the studio draft) and each action's
 * error branch.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useConsultantStore } from "@/stores/app/consultant";
import { useAppCoreStore } from "@/stores/app/core";
import { useDraftsStore } from "@/stores/app/drafts";
import type { ClarifyResult, ConsultantSession } from "@/types/api";
import { ConsultantState, MessageKey, Modality } from "@/types/enums";

function makeClarifyResult(overrides: Partial<ClarifyResult> = {}): ClarifyResult {
  return {
    modality: Modality.IMAGE,
    summary: "summary",
    questions: [],
    template_loaded: false,
    next_step: "review",
    analysis: null,
    ...overrides,
  };
}

function makeSession(overrides: Partial<ConsultantSession> = {}): ConsultantSession {
  const now = new Date().toISOString();
  return {
    session_id: "session-1",
    project_id: "p1",
    modality: Modality.IMAGE,
    state: ConsultantState.CLARIFY,
    checklist_status: {},
    slots: {},
    plan: null,
    last_result: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useConsultantStore().requestProjectClarification", () => {
  it("stores the result, clears the studio draft, and refreshes conversation + workspace", async () => {
    const store = useConsultantStore();
    const coreStore = useAppCoreStore();
    const draftsStore = useDraftsStore();
    draftsStore.updateStudioDraft("p1", { prompt: "draw a dragon" });
    const clarifySpy = vi.spyOn(apiClient, "clarifyProject").mockResolvedValue(makeClarifyResult());
    const convSpy = vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      entries: [],
      total: 0,
      offset: 0,
      limit: 30,
      has_more: false,
    });
    const workspaceSpy = vi
      .spyOn(apiClient, "projectWorkspace")
      .mockResolvedValue({ jobs: [], assets: [], plans: [] });

    await store.requestProjectClarification("p1", { prompt: "draw a dragon" });

    expect(clarifySpy).toHaveBeenCalledWith("p1", { prompt: "draw a dragon" });
    expect(draftsStore.getStudioDraft("p1")).toEqual({ prompt: "" });
    expect(convSpy).toHaveBeenCalled();
    expect(workspaceSpy).toHaveBeenCalled();
    expect(coreStore.errorMessageKey).toBeNull();
  });

  it("on failure, records the error key, leaves the studio draft untouched, and rethrows", async () => {
    const store = useConsultantStore();
    const coreStore = useAppCoreStore();
    const draftsStore = useDraftsStore();
    draftsStore.updateStudioDraft("p1", { prompt: "kept draft" });
    vi.spyOn(apiClient, "clarifyProject").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_500));

    await expect(store.requestProjectClarification("p1", { prompt: "kept draft" })).rejects.toBeInstanceOf(
      apiClient.ApiClientError,
    );

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_500);
    expect(draftsStore.getStudioDraft("p1")).toEqual({ prompt: "kept draft" });
  });
});

describe("useConsultantStore().resumeConsultantSession", () => {
  it("stores the session when one is returned", async () => {
    const store = useConsultantStore();
    const session = makeSession();
    vi.spyOn(apiClient, "resumeConsultantSession").mockResolvedValue({ session });

    const result = await store.resumeConsultantSession("p1");

    expect(result).toEqual(session);
    expect(store.consultantSessions.p1).toEqual(session);
  });

  it("leaves consultantSessions untouched when no session exists", async () => {
    const store = useConsultantStore();
    vi.spyOn(apiClient, "resumeConsultantSession").mockResolvedValue({ session: null });

    const result = await store.resumeConsultantSession("p1");

    expect(result).toBeNull();
    expect(store.consultantSessions.p1).toBeUndefined();
  });
});

describe("useConsultantStore().startConsultantSession", () => {
  it("stores the session + result and refreshes conversation + workspace", async () => {
    const store = useConsultantStore();
    const coreStore = useAppCoreStore();
    const session = makeSession();
    const result = makeClarifyResult({ summary: "started" });
    vi.spyOn(apiClient, "startConsultantSession").mockResolvedValue({ session, result, missing_slots: [] });
    vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      entries: [],
      total: 0,
      offset: 0,
      limit: 30,
      has_more: false,
    });
    vi.spyOn(apiClient, "projectWorkspace").mockResolvedValue({ jobs: [], assets: [], plans: [] });

    const returned = await store.startConsultantSession("p1", { prompt: "start" });

    expect(returned).toEqual(session);
    expect(store.consultantSessions.p1).toEqual(session);
    expect(store.consultantResponse).toEqual(result);
    // Unlike requestProjectClarification, startConsultantSession sets its
    // message key AFTER the conversation/workspace reload (matches the
    // pre-split app.ts's startConsultantSession body exactly).
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_ADD0);
  });
});

describe("useConsultantStore().advanceConsultantSession", () => {
  it("stores the advanced session + result and reports success without a workspace reload", async () => {
    const store = useConsultantStore();
    const coreStore = useAppCoreStore();
    const session = makeSession({ state: ConsultantState.ACCEPT });
    const result = makeClarifyResult({ summary: "advanced" });
    const spy = vi
      .spyOn(apiClient, "advanceConsultantSession")
      .mockResolvedValue({ session, result, missing_slots: [] });

    const returned = await store.advanceConsultantSession("p1", { session_id: "session-1", accept: true });

    expect(spy).toHaveBeenCalledWith("p1", { session_id: "session-1", accept: true });
    expect(returned).toEqual(session);
    expect(store.consultantSessions.p1).toEqual(session);
    expect(store.consultantResponse).toEqual(result);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_SWITCH0);
  });
});

describe("useConsultantStore().optimizeSynopsis / closeSynopsisSuggestion", () => {
  it("stores the suggestion on success", async () => {
    const store = useConsultantStore();
    const coreStore = useAppCoreStore();
    const suggestion = { optimized_synopsis: "a better synopsis", strategy: "rewrite", provider: "ollama" };
    const spy = vi.spyOn(apiClient, "optimizeSynopsis").mockResolvedValue(suggestion);

    await store.optimizeSynopsis("My Project", "RPG", "original synopsis");

    expect(spy).toHaveBeenCalledWith({
      project_name: "My Project",
      project_type: "RPG",
      synopsis: "original synopsis",
    });
    expect(store.synopsisSuggestion).toEqual(suggestion);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
  });

  it("on failure, records the error key and rethrows without setting a suggestion", async () => {
    const store = useConsultantStore();
    const coreStore = useAppCoreStore();
    vi.spyOn(apiClient, "optimizeSynopsis").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_500));

    await expect(store.optimizeSynopsis("P", "RPG", "s")).rejects.toBeInstanceOf(apiClient.ApiClientError);

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_500);
    expect(store.synopsisSuggestion).toBeNull();
  });

  it("closeSynopsisSuggestion clears a previously-set suggestion", () => {
    const store = useConsultantStore();
    store.synopsisSuggestion = { optimized_synopsis: "x", strategy: "rewrite", provider: null };

    store.closeSynopsisSuggestion();

    expect(store.synopsisSuggestion).toBeNull();
  });
});
