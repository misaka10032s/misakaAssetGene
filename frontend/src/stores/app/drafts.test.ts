/**
 * Unit tests for useDraftsStore() — the localStorage-backed "new project"
 * form draft and per-project studio (consultant prompt) draft, ported
 * unchanged from the pre-split stores/app.ts (readStoredJson/writeStoredJson
 * + the deep `watch` that persists on change).
 *
 * The behaviour this guards: nothing stored yet must return the fallback
 * (never throw), corrupt JSON in storage must not crash the store, a write
 * must round-trip through localStorage, drafts are isolated per project id,
 * and — the one a naive re-implementation could easily get wrong — the
 * store must NOT write to localStorage merely from being instantiated and
 * reading its initial value back. A spurious write on load would silently
 * overwrite whatever the user already had stored under that key with the
 * in-memory fallback (or a re-serialization of what was just read, best
 * case, but still an unnecessary write this test treats as a regression).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";

import { useDraftsStore } from "@/stores/app/drafts";

const PROJECT_DRAFT_STORAGE_KEY = "misaka.projectDraft";
const STUDIO_DRAFT_STORAGE_KEY = "misaka.studioDrafts";

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("useDraftsStore() — localStorage persistence", () => {
  it("returns the fallback project draft when nothing is stored yet, without throwing", () => {
    expect(() => useDraftsStore()).not.toThrow();
    const store = useDraftsStore();
    expect(store.projectDraft).toEqual({ name: "", type: "RPG", synopsis: "" });
    expect(store.studioDrafts).toEqual({});
  });

  it("falls back instead of throwing on malformed JSON already in storage", () => {
    window.localStorage.setItem(PROJECT_DRAFT_STORAGE_KEY, "{not valid json");
    window.localStorage.setItem(STUDIO_DRAFT_STORAGE_KEY, "[[[broken");

    expect(() => useDraftsStore()).not.toThrow();
    const store = useDraftsStore();
    expect(store.projectDraft).toEqual({ name: "", type: "RPG", synopsis: "" });
    expect(store.studioDrafts).toEqual({});
  });

  it("does NOT write to localStorage on initial load", () => {
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");

    useDraftsStore();

    expect(setItemSpy).not.toHaveBeenCalled();
  });

  it("round-trips a project draft write through localStorage", async () => {
    const store = useDraftsStore();

    store.updateProjectDraft({ name: "My Project", type: "VN", synopsis: "A tale" });
    await nextTick();

    const raw = window.localStorage.getItem(PROJECT_DRAFT_STORAGE_KEY);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string)).toEqual({ name: "My Project", type: "VN", synopsis: "A tale" });
  });

  it("round-trips a studio draft write through localStorage", async () => {
    const store = useDraftsStore();

    store.updateStudioDraft("proj-1", { prompt: "draw a cat" });
    await nextTick();

    const raw = window.localStorage.getItem(STUDIO_DRAFT_STORAGE_KEY);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string)).toEqual({ "proj-1": { prompt: "draw a cat" } });
  });

  it("isolates studio drafts per project id", () => {
    const store = useDraftsStore();

    store.updateStudioDraft("proj-A", { prompt: "A's prompt" });
    store.updateStudioDraft("proj-B", { prompt: "B's prompt" });

    expect(store.getStudioDraft("proj-A")).toEqual({ prompt: "A's prompt" });
    expect(store.getStudioDraft("proj-B")).toEqual({ prompt: "B's prompt" });
  });

  it("getStudioDraft falls back to an empty prompt for a null or unknown project id", () => {
    const store = useDraftsStore();
    store.updateStudioDraft("proj-A", { prompt: "A's prompt" });

    expect(store.getStudioDraft(null)).toEqual({ prompt: "" });
    expect(store.getStudioDraft("unknown-project")).toEqual({ prompt: "" });
  });
});
