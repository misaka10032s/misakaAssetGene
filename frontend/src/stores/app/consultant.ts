import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import type {
  ClarifyPayload,
  ClarifyResult,
  ConsultantSession,
  ConsultantSessionAdvancePayload,
  ConsultantSessionStartPayload,
  SynopsisOptimizeResult,
} from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";
import { useConversationsStore } from "@/stores/app/conversations";
import { useWorkspaceStore } from "@/stores/app/workspace";
import { useDraftsStore } from "@/stores/app/drafts";

const isDevDiagnostics = appEnv.diagnosticsEnabled;

/**
 * Consultant sessions and the synopsis-optimize suggestion flow. Depends on
 * `core` (message-key status), `conversations` + `workspace` (a successful
 * clarify/session-start reloads both, exactly as the previous inline
 * implementation did), and `drafts` (clears the studio draft on a successful
 * clarify). None of those four depend back on this store.
 */
export const useConsultantStore = defineStore("app/consultant", () => {
  const coreStore = useAppCoreStore();
  const conversationsStore = useConversationsStore();
  const workspaceStore = useWorkspaceStore();
  const draftsStore = useDraftsStore();

  const consultantResponse = ref<ClarifyResult | null>(null);
  const consultantSessions = ref<Record<string, ConsultantSession>>({});
  const synopsisSuggestion = ref<SynopsisOptimizeResult | null>(null);

  async function requestProjectClarification(projectId: string, payload: ClarifyPayload): Promise<void> {
    try {
      consultantResponse.value = await apiClient.clarifyProject(projectId, payload);
      draftsStore.studioDrafts = {
        ...draftsStore.studioDrafts,
        [projectId]: {
          prompt: "",
        },
      };
      await Promise.all([
        conversationsStore.loadProjectConversation(projectId, true),
        workspaceStore.loadProjectWorkspace(projectId),
      ]);
      coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
      coreStore.errorMessageKey = null;
      if (isDevDiagnostics) {
        console.info("[misaka.app] consultant response received", {
          modality: payload.modality,
          projectId,
        });
      }
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
      }
      throw error;
    }
  }

  async function resumeConsultantSession(projectId: string): Promise<ConsultantSession | null> {
    const response = await apiClient.resumeConsultantSession(projectId);
    if (response.session) {
      consultantSessions.value = {
        ...consultantSessions.value,
        [projectId]: response.session,
      };
    }
    return response.session;
  }

  async function startConsultantSession(
    projectId: string,
    payload: ConsultantSessionStartPayload,
  ): Promise<ConsultantSession> {
    const response = await apiClient.startConsultantSession(projectId, payload);
    consultantSessions.value = {
      ...consultantSessions.value,
      [projectId]: response.session,
    };
    consultantResponse.value = response.result;
    await Promise.all([
      conversationsStore.loadProjectConversation(projectId, true),
      workspaceStore.loadProjectWorkspace(projectId),
    ]);
    coreStore.lastMessageKey = MessageKey.SUCCESS_ADD0;
    coreStore.errorMessageKey = null;
    return response.session;
  }

  async function advanceConsultantSession(
    projectId: string,
    payload: ConsultantSessionAdvancePayload,
  ): Promise<ConsultantSession> {
    const response = await apiClient.advanceConsultantSession(projectId, payload);
    consultantSessions.value = {
      ...consultantSessions.value,
      [projectId]: response.session,
    };
    consultantResponse.value = response.result;
    coreStore.lastMessageKey = MessageKey.SUCCESS_SWITCH0;
    coreStore.errorMessageKey = null;
    return response.session;
  }

  async function optimizeSynopsis(projectName: string, projectType: string, synopsis: string): Promise<void> {
    try {
      synopsisSuggestion.value = await apiClient.optimizeSynopsis({
        project_name: projectName,
        project_type: projectType,
        synopsis,
      });
      coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
      coreStore.errorMessageKey = null;
    } catch (error) {
      if (error instanceof apiClient.ApiClientError) {
        coreStore.errorMessageKey = error.messageKey;
      }
      throw error;
    }
  }

  function closeSynopsisSuggestion(): void {
    synopsisSuggestion.value = null;
  }

  return {
    consultantResponse,
    consultantSessions,
    synopsisSuggestion,
    requestProjectClarification,
    resumeConsultantSession,
    startConsultantSession,
    advanceConsultantSession,
    optimizeSynopsis,
    closeSynopsisSuggestion,
  };
});
