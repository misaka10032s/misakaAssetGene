import { ref, watch } from "vue";
import { defineStore } from "pinia";

import type { ClarifyPayload, CreateProjectPayload } from "@/types/api";
import { readStoredJson, writeStoredJson } from "@/stores/shared/storage";

const PROJECT_DRAFT_STORAGE_KEY = "misaka.projectDraft";
const STUDIO_DRAFT_STORAGE_KEY = "misaka.studioDrafts";

/**
 * `localStorage`-backed drafts: the "new project" form draft and the
 * per-project studio (consultant prompt) draft. A leaf store — it reads no
 * state from any other `stores/app/*` module, so `core`/`consultant` may
 * depend on it without risk of a cycle.
 */
export const useDraftsStore = defineStore("app/drafts", () => {
  const projectDraft = ref<CreateProjectPayload>(
    readStoredJson<CreateProjectPayload>(PROJECT_DRAFT_STORAGE_KEY, {
      name: "",
      type: "RPG",
      synopsis: "",
    }),
  );
  const studioDrafts = ref<Record<string, ClarifyPayload>>(
    readStoredJson<Record<string, ClarifyPayload>>(STUDIO_DRAFT_STORAGE_KEY, {}),
  );

  watch(
    projectDraft,
    (value) => {
      writeStoredJson(PROJECT_DRAFT_STORAGE_KEY, value);
    },
    { deep: true },
  );
  watch(
    studioDrafts,
    (value) => {
      writeStoredJson(STUDIO_DRAFT_STORAGE_KEY, value);
    },
    { deep: true },
  );

  function updateProjectDraft(payload: CreateProjectPayload): void {
    projectDraft.value = { ...payload };
  }

  function getStudioDraft(projectId: string | null): ClarifyPayload {
    if (!projectId) {
      return {
        prompt: "",
      };
    }
    return (
      studioDrafts.value[projectId] ?? {
        prompt: "",
      }
    );
  }

  function updateStudioDraft(projectId: string, payload: ClarifyPayload): void {
    studioDrafts.value = {
      ...studioDrafts.value,
      [projectId]: { ...payload },
    };
  }

  return {
    projectDraft,
    studioDrafts,
    updateProjectDraft,
    getStudioDraft,
    updateStudioDraft,
  };
});
