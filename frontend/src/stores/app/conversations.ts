import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import type { ConversationEntry } from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";

const CONVERSATION_PAGE_SIZE = 30;

/**
 * Per-project conversation history, with the offset/total bookkeeping the
 * "load more" pagination needs and de-duplication across repeated pages.
 * Depends on `core` (shared message-key status) only.
 */
export const useConversationsStore = defineStore("app/conversations", () => {
  const coreStore = useAppCoreStore();

  const projectConversations = ref<Record<string, ConversationEntry[]>>({});
  const projectConversationTotals = ref<Record<string, number>>({});
  const projectConversationOffsets = ref<Record<string, number>>({});

  async function loadProjectConversation(projectId: string, reset = false): Promise<void> {
    const currentOffset = reset ? 0 : projectConversationOffsets.value[projectId] ?? 0;
    const response = await apiClient.projectConversationPage(projectId, currentOffset, CONVERSATION_PAGE_SIZE);
    const existingEntries = reset ? [] : projectConversations.value[projectId] ?? [];
    const mergedEntries = [...response.entries, ...existingEntries];
    const dedupedEntries = mergedEntries.filter(
      (entry, index, collection) => collection.findIndex((candidate) => candidate.id === entry.id) === index,
    );
    projectConversations.value = {
      ...projectConversations.value,
      [projectId]: dedupedEntries,
    };
    projectConversationTotals.value = {
      ...projectConversationTotals.value,
      [projectId]: response.total,
    };
    projectConversationOffsets.value = {
      ...projectConversationOffsets.value,
      [projectId]: currentOffset + response.entries.length,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
  }

  return {
    projectConversations,
    projectConversationTotals,
    projectConversationOffsets,
    loadProjectConversation,
  };
});
