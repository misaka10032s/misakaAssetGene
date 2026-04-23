<script setup lang="ts">
import { onMounted, reactive } from "vue";

import { useAppStore } from "@/stores/app";
import { useWindowSize } from "@/composables/useWindowSize";
import { ProviderStatus } from "@/types/enums";

const appStore = useAppStore();
const { tripleGridClass } = useWindowSize();
const modelForm = reactive({
  url: "",
});

onMounted(() => {
  void appStore.loadIntegrationSnapshot();
  void appStore.loadLocalLlmStatus();
});

/**
 * Starts the local LLM server on demand.
 */
async function startLocalLlm(): Promise<void> {
  await appStore.startLocalLlm();
}

/**
 * Downloads a model file from a Hugging Face URL.
 */
async function downloadModel(): Promise<void> {
  if (!modelForm.url.trim()) {
    return;
  }
  await appStore.downloadLocalModel(modelForm.url);
  modelForm.url = "";
}
</script>

<template>
  <section class="min-w-0 grid gap-4" :class="tripleGridClass">
    <div class="app-panel xl:col-span-3">
      <h2 class="app-section-title">{{ $t("settings.title") }}</h2>
      <p class="app-muted">{{ $t("settings.intro") }}</p>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.localLlmTitle") }}</h2>
      <div v-if="appStore.localLlmStatus" class="grid gap-2">
        <p class="text-sm text-app-text">
          {{ $t("settings.serverStatus") }}:
          <span :class="appStore.localLlmStatus.is_running ? 'text-app-success' : 'text-app-warning'">
            {{ appStore.localLlmStatus.is_running ? $t("settings.serverOn") : $t("settings.serverOff") }}
          </span>
        </p>
        <p class="break-all app-muted">{{ appStore.localLlmStatus.server }} · {{ appStore.localLlmStatus.base_url }}</p>
        <p class="break-all app-muted">{{ $t("settings.executable") }}: {{ appStore.localLlmStatus.executable_path ?? $t("settings.notFound") }}</p>
        <button
          class="app-button-secondary"
          :disabled="appStore.localLlmStatus.is_running || !appStore.localLlmStatus.executable_found"
          @click="startLocalLlm"
        >
          {{ $t("settings.startServerAction") }}
        </button>
      </div>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.downloadTitle") }}</h2>
      <label class="grid gap-2">
        <span class="app-muted">{{ $t("settings.downloadLabel") }}</span>
        <input v-model="modelForm.url" class="app-input" :placeholder="$t('settings.downloadPlaceholder')" />
      </label>
      <button class="mt-4 app-button" :disabled="!modelForm.url.trim()" @click="downloadModel">
        {{ $t("settings.downloadAction") }}
      </button>
      <p v-if="appStore.lastDownloadedModel" class="mt-3 break-all app-muted">
        {{ appStore.lastDownloadedModel.filename }} → {{ appStore.lastDownloadedModel.saved_path }}
      </p>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.tools") }}</h2>
      <ul>
        <li v-for="tool in appStore.integration.tools" :key="tool.name">
          {{ tool.name }} — {{ tool.version }}
        </li>
      </ul>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.workers") }}</h2>
      <ul>
        <li v-for="worker in appStore.integration.workers" :key="worker.name">
          {{ worker.name }} — {{ worker.reference }}
        </li>
      </ul>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.providers") }}</h2>
      <ul class="grid gap-3">
        <li v-for="provider in appStore.integration.providers" :key="provider.name" class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
          <div class="font-semibold text-app-text">{{ provider.name }}</div>
          <div class="break-all app-muted">{{ provider.mode }} · {{ provider.base_url }}</div>
          <div class="mt-2 text-sm text-app-text">
            <span v-if="provider.status === ProviderStatus.READY">{{ $t("settings.ready") }}</span>
            <span v-else-if="provider.status === ProviderStatus.CONFIGURED">{{ $t("settings.configured") }}</span>
            <span v-else-if="provider.status === ProviderStatus.UNAVAILABLE">{{ $t("settings.unavailable") }}</span>
            <span v-else>{{ $t("settings.disabled") }}</span>
          </div>
        </li>
      </ul>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.registry") }}</h2>
      <ul>
        <li v-for="category in appStore.integration.registry_categories" :key="category">
          {{ category }}
        </li>
      </ul>
    </div>

    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("settings.modelPaths") }}</h2>
      <ul class="grid gap-2">
        <li v-for="modelPath in appStore.integration.model_search_paths" :key="modelPath" class="break-all app-muted">
          {{ modelPath }}
        </li>
      </ul>
    </div>
  </section>
</template>
