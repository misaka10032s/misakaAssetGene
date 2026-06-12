<script setup lang="ts">
import { computed, onMounted, reactive } from "vue";

import { useAppStore } from "@/stores/app";
import type { ClarifyPayload, ConsultantSession } from "@/types/api";
import { Modality } from "@/types/enums";

const appStore = useAppStore();
const form = reactive<ClarifyPayload>({
  modality: Modality.MUSIC,
  prompt: "",
});

/**
 * Current persisted consultant session for the active project, if any.
 */
const currentSession = computed<ConsultantSession | null>(() => {
  const projectId = appStore.currentProjectId;
  return projectId ? appStore.consultantSessions[projectId] ?? null : null;
});

/**
 * Starts (or resumes) a persisted consultant session for the active project.
 */
async function requestClarification(): Promise<void> {
  const projectId = appStore.currentProjectId;
  if (!projectId) {
    return;
  }
  await appStore.startConsultantSession(projectId, {
    prompt: form.prompt,
    modality: form.modality ?? null,
    session_id: currentSession.value?.session_id ?? null,
  });
}

onMounted(async () => {
  const projectId = appStore.currentProjectId;
  if (projectId) {
    await appStore.resumeConsultantSession(projectId);
  }
});
</script>

<template>
  <section class="grid gap-4">
    <div v-if="!appStore.currentProjectName" class="rounded-xl border border-app-warning bg-app-warning/10 px-4 py-3 text-sm text-app-text">
      {{ $t("chat.noProjectNotice") }}
    </div>

    <section class="app-panel grid gap-3">
      <h2 class="app-section-title">{{ $t("chat.title") }}</h2>
      <label>
        <span class="app-muted">{{ $t("chat.modality") }}</span>
        <select v-model="form.modality" class="app-input">
          <option :value="Modality.MUSIC">{{ $t("modality.music") }}</option>
          <option :value="Modality.IMAGE">{{ $t("modality.image") }}</option>
          <option :value="Modality.VOICE">{{ $t("modality.voice") }}</option>
          <option :value="Modality.VIDEO">{{ $t("modality.video") }}</option>
        </select>
      </label>

      <label>
        <span class="app-muted">{{ $t("chat.prompt") }}</span>
        <textarea v-model="form.prompt" class="app-input min-h-32" rows="6" :placeholder="$t('chat.promptPlaceholder')"></textarea>
      </label>

      <button class="app-button" :disabled="!appStore.currentProjectName" @click="requestClarification">
        {{ $t("chat.submit") }}
      </button>

      <p v-if="currentSession" class="text-sm app-muted">
        {{ $t("chat.consultantState") }}: <span class="font-semibold text-app-text">{{ currentSession.state }}</span>
      </p>

      <div v-if="appStore.consultantResponse" class="rounded-xl border border-app-border bg-app-surfaceAlt p-4">
        <h3 class="font-semibold text-app-text">{{ $t("chat.responseTitle") }}</h3>
        <p class="mt-2 app-muted">{{ appStore.consultantResponse.summary }}</p>
        <ol class="mt-3 list-decimal pl-5 text-sm text-app-text">
          <li v-for="question in appStore.consultantResponse.questions" :key="question">
            {{ question }}
          </li>
        </ol>
      </div>
    </section>
  </section>
</template>
