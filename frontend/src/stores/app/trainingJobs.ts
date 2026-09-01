import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import { appEnv } from "@/config/env";
import type { TrainingJob } from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";

const isDevDiagnostics = appEnv.diagnosticsEnabled;

/**
 * Per-project training job workspace, including the `subscribeTrainingJob`
 * SSE subscription (spec §7.3 deferred tail). Depends on `core` (shared
 * message-key status) only.
 */
export const useTrainingJobsStore = defineStore("app/trainingJobs", () => {
  const coreStore = useAppCoreStore();

  const projectTrainingJobs = ref<Record<string, TrainingJob[]>>({});

  async function loadProjectTrainingWorkspace(projectId: string): Promise<TrainingJob[]> {
    const response = await apiClient.projectTrainingWorkspace(projectId);
    projectTrainingJobs.value = {
      ...projectTrainingJobs.value,
      [projectId]: response.jobs,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    return response.jobs;
  }

  /**
   * Merges a single (freshly streamed) training job into the project's job
   * list, replacing the matching entry by id or appending if new.
   */
  function mergeTrainingJob(projectId: string, job: TrainingJob): void {
    const existing = projectTrainingJobs.value[projectId] ?? [];
    const index = existing.findIndex((candidate) => candidate.id === job.id);
    const next = index >= 0 ? existing.map((c, i) => (i === index ? job : c)) : [...existing, job];
    projectTrainingJobs.value = {
      ...projectTrainingJobs.value,
      [projectId]: next,
    };
  }

  /**
   * Subscribes to a training job's live progress via Server-Sent Events
   * (spec §7.3 deferred tail), replacing GET polling. Each `progress` / `done`
   * frame merges the updated job into projectTrainingJobs so the UI reacts
   * reactively. Returns an unsubscribe function that closes the EventSource;
   * `onDone` (if provided) fires once when the stream reaches a terminal frame.
   *
   * REAL-RUN: end-to-end progress against a live GPU training run is DEFERRED
   * to the user — the push path is contract-tested on the backend.
   */
  function subscribeTrainingJob(
    projectId: string,
    jobId: string,
    onDone?: (job: TrainingJob) => void,
  ): () => void {
    if (typeof window === "undefined" || typeof EventSource === "undefined") {
      // SSE unavailable (SSR / non-browser): no-op unsubscribe.
      return () => undefined;
    }
    const source = new EventSource(apiClient.trainingJobStreamUrl(projectId, jobId));

    const handleFrame = (event: MessageEvent<string>, terminal: boolean): void => {
      try {
        const payload = JSON.parse(event.data) as { job: TrainingJob };
        mergeTrainingJob(projectId, payload.job);
        if (terminal) {
          source.close();
          onDone?.(payload.job);
        }
      } catch (error) {
        if (isDevDiagnostics) {
          console.warn("[misaka.app] training stream frame parse failed", error);
        }
      }
    };

    source.addEventListener("progress", (event) => handleFrame(event as MessageEvent<string>, false));
    source.addEventListener("done", (event) => handleFrame(event as MessageEvent<string>, true));
    source.onerror = () => {
      // The browser auto-reconnects on transient errors; once the server has
      // closed after a terminal frame the connection ends. Close defensively
      // so we don't leak a reconnecting socket after completion.
      if (source.readyState === EventSource.CLOSED) {
        source.close();
      }
    };

    return () => source.close();
  }

  async function createProjectTrainingJob(
    projectId: string,
    payload: { title: string; modality: string; dataset_path: string; worker?: string | null },
  ): Promise<TrainingJob[]> {
    const response = await apiClient.createProjectTrainingJob(projectId, payload);
    projectTrainingJobs.value = {
      ...projectTrainingJobs.value,
      [projectId]: response.jobs,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_ADD0;
    coreStore.errorMessageKey = null;
    return response.jobs;
  }

  return {
    projectTrainingJobs,
    loadProjectTrainingWorkspace,
    mergeTrainingJob,
    subscribeTrainingJob,
    createProjectTrainingJob,
  };
});
