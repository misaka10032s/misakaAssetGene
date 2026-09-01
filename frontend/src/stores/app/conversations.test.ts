/**
 * Unit tests for useConversationsStore().loadProjectConversation — the
 * per-project paged conversation history, ported unchanged from the
 * pre-split stores/app.ts (see the merge-base copy of that file for the
 * authoritative behaviour this test is written against).
 *
 * The offset/dedup logic is the realistic failure point here: a page is
 * fetched with `offset=currentOffset, limit=CONVERSATION_PAGE_SIZE(30)`, the
 * NEW entries are placed BEFORE the already-loaded ones, and de-duplication
 * keeps the FIRST occurrence by id — i.e. a newly re-fetched entry wins over
 * a stale cached copy with the same id. Off-by-one on the offset bookkeeping
 * and "which copy survives dedup" are exactly the kind of bug this guards.
 *
 * apiClient is mocked at the network boundary only (`projectConversationPage`);
 * the store itself is never mocked.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useConversationsStore } from "@/stores/app/conversations";
import { useAppCoreStore } from "@/stores/app/core";
import type { ConversationEntry } from "@/types/api";
import { MessageKey } from "@/types/enums";

function makeEntry(overrides: Partial<ConversationEntry> = {}): ConversationEntry {
  return {
    id: "entry-1",
    role: "user",
    content: "hello",
    created_at: new Date().toISOString(),
    modality: null,
    questions: [],
    analysis: null,
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useConversationsStore().loadProjectConversation", () => {
  it("loads the first page for a project with no conversation yet", async () => {
    const store = useConversationsStore();
    const spy = vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      entries: [makeEntry({ id: "c1" }), makeEntry({ id: "c2" })],
      total: 2,
      offset: 0,
      limit: 30,
      has_more: false,
    });

    await store.loadProjectConversation("p1", true);

    expect(spy).toHaveBeenCalledWith("p1", 0, 30);
    expect(store.projectConversations.p1?.map((e) => e.id)).toEqual(["c1", "c2"]);
    expect(store.projectConversationTotals.p1).toBe(2);
    expect(store.projectConversationOffsets.p1).toBe(2);
  });

  it("forces offset back to 0 on reset even if a stale offset is already recorded", async () => {
    const store = useConversationsStore();
    store.projectConversationOffsets = { p1: 999 };
    const spy = vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      entries: [makeEntry({ id: "c1" })],
      total: 1,
      offset: 0,
      limit: 30,
      has_more: false,
    });

    await store.loadProjectConversation("p1", true);

    expect(spy).toHaveBeenCalledWith("p1", 0, 30);
  });

  it("merges a page that overlaps existing entries, keeping the freshly-fetched copy on a duplicate id", async () => {
    const store = useConversationsStore();
    store.projectConversations = {
      p1: [makeEntry({ id: "c1", content: "old c1" }), makeEntry({ id: "c2", content: "old c2" })],
    };
    store.projectConversationOffsets = { p1: 2 };
    const spy = vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      // c2 re-appears with fresh content — simulates an overlapping page.
      entries: [makeEntry({ id: "c2", content: "fresh c2" }), makeEntry({ id: "c3", content: "new c3" })],
      total: 5,
      offset: 2,
      limit: 30,
      has_more: true,
    });

    await store.loadProjectConversation("p1");

    expect(spy).toHaveBeenCalledWith("p1", 2, 30);
    const ids = store.projectConversations.p1?.map((e) => e.id);
    expect(ids).toEqual(["c2", "c3", "c1"]);
    expect(store.projectConversations.p1?.find((e) => e.id === "c2")?.content).toBe("fresh c2");
    expect(store.projectConversationOffsets.p1).toBe(4);
    expect(store.projectConversationTotals.p1).toBe(5);
  });

  it("handles an empty response without duplicating or losing existing entries", async () => {
    const store = useConversationsStore();
    store.projectConversations = { p1: [makeEntry({ id: "c1" })] };
    store.projectConversationOffsets = { p1: 1 };
    vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      entries: [],
      total: 1,
      offset: 1,
      limit: 30,
      has_more: false,
    });

    await store.loadProjectConversation("p1");

    expect(store.projectConversations.p1?.map((e) => e.id)).toEqual(["c1"]);
    expect(store.projectConversationOffsets.p1).toBe(1);
    expect(store.projectConversationTotals.p1).toBe(1);
  });

  it("sets the shared success message key and clears any prior error on success", async () => {
    const store = useConversationsStore();
    const coreStore = useAppCoreStore();
    coreStore.errorMessageKey = MessageKey.FAIL_500;
    vi.spyOn(apiClient, "projectConversationPage").mockResolvedValue({
      entries: [],
      total: 0,
      offset: 0,
      limit: 30,
      has_more: false,
    });

    await store.loadProjectConversation("p-empty", true);

    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });

  it("leaves store state untouched when the request rejects (no partial mutation)", async () => {
    const store = useConversationsStore();
    store.projectConversations = { p1: [makeEntry({ id: "c1" })] };
    const before = store.projectConversations.p1;
    vi.spyOn(apiClient, "projectConversationPage").mockRejectedValue(new Error("network down"));

    await expect(store.loadProjectConversation("p1")).rejects.toThrow("network down");

    expect(store.projectConversations.p1).toBe(before);
  });
});
