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
  if (!appStore.integration.tools.length && !appStore.integration.workers.length) {
    void appStore.loadIntegrationSnapshot();
  }
  if (!appStore.localLlmStatus) {
    void appStore.loadLocalLlmStatus();
  }
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

/**
 * Installs or syncs a worker repository to the recommended revision.
 *
 * @param workerName - The worker identifier.
 */
async function installWorker(workerName: string): Promise<void> {
  await appStore.installWorker(workerName);
}

async function startWorker(workerName: string): Promise<void> {
  await appStore.startWorker(workerName);
}

async function stopWorker(workerName: string): Promise<void> {
  await appStore.stopWorker(workerName);
}

async function smokeWorker(workerName: string): Promise<void> {
  await appStore.smokeWorker(workerName);
}
</script>

<template>
  <section class="min-w-0 grid gap-4" :class="tripleGridClass">
    <div class="app-panel xl:col-span-3">
      <h2 class="app-section-title">{{ $t("settings.title") }}</h2>
      <p class="app-muted">{{ $t("settings.intro") }}</p>
      <div class="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        <div class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
          <p class="app-muted">{{ $t("settings.networkMode") }}</p>
          <p class="mt-1 font-semibold text-app-text">{{ appStore.integration.network.mode }}</p>
        </div>
        <div class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
          <p class="app-muted">{{ $t("settings.networkReachability") }}</p>
          <p class="mt-1 font-semibold text-app-text">
            {{ appStore.integration.network.reachable ? $t("settings.ready") : $t("settings.unavailable") }}
          </p>
        </div>
        <div class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
          <p class="app-muted">{{ $t("settings.networkSummary") }}</p>
          <p class="mt-1 text-sm text-app-text">{{ appStore.integration.network.summary || "-" }}</p>
        </div>
      </div>
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
      <ul class="grid gap-3">
        <li v-for="worker in appStore.integration.workers" :key="worker.name" class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
          <div class="font-semibold text-app-text">{{ worker.display_name }}</div>
          <div class="break-all app-muted">{{ worker.repo }}</div>
          <div class="mt-2 text-sm text-app-text">
            {{ $t("settings.installedState") }}:
            <span :class="worker.is_installed ? 'text-app-success' : 'text-app-warning'">
              {{ worker.is_installed ? $t("settings.installed") : $t("settings.notInstalled") }}
            </span>
          </div>
          <div class="mt-1 text-sm text-app-text">
            {{ $t("settings.runningState") }}:
            <span :class="worker.is_running ? 'text-app-success' : 'text-app-warning'">
              {{ worker.is_running ? $t("settings.running") : $t("settings.stopped") }}
            </span>
          </div>
          <div class="mt-1 text-sm text-app-text">
            {{ $t("settings.runtimeState") }}:
            <span class="text-app-primary">{{ worker.runtime_state }}</span>
          </div>
          <div class="mt-2 break-all app-muted">{{ $t("settings.recommendedWorker") }}: {{ worker.recommended_reference }}</div>
          <div class="break-all app-muted">
            {{ $t("settings.installedWorker") }}: {{ worker.installed_reference ?? $t("settings.notInstalled") }}
          </div>
          <div class="break-all app-muted">{{ $t("settings.workerPath") }}: {{ worker.path }}</div>
          <div class="break-all app-muted">{{ $t("settings.vramRequirement") }}: {{ worker.vram_requirement_mb }} MB</div>
          <div v-if="worker.last_job_at" class="break-all app-muted">{{ $t("settings.lastJobAt") }}: {{ worker.last_job_at }}</div>
          <div v-if="worker.health_check" class="break-all app-muted">{{ worker.health_check }}</div>
          <div v-if="worker.readiness_note" class="mt-2 text-sm text-app-warning">
            {{ $t("settings.readinessNote") }}: {{ worker.readiness_note }}
          </div>
          <div v-if="worker.managed_pid" class="mt-1 app-muted">
            {{ $t("settings.managedPid") }}: {{ worker.managed_pid }}
          </div>
          <div class="mt-4 flex flex-wrap gap-2">
            <button class="app-button-secondary" @click="installWorker(worker.name)">
              {{ worker.is_installed ? $t("settings.resyncWorkerAction") : $t("settings.installWorkerAction") }}
            </button>
            <button class="app-button-secondary" :disabled="!worker.is_installed || worker.is_running" @click="startWorker(worker.name)">
              {{ $t("settings.startWorkerAction") }}
            </button>
            <button class="app-button-secondary" :disabled="!worker.is_running" @click="stopWorker(worker.name)">
              {{ $t("settings.stopWorkerAction") }}
            </button>
            <button class="app-button-secondary" :disabled="!worker.is_installed" @click="smokeWorker(worker.name)">
              {{ $t("settings.smokeWorkerAction") }}
            </button>
          </div>
          <div v-if="appStore.workerSmokeResults[worker.name]" class="mt-3 text-sm text-app-text">
            {{ $t("settings.smokeResult") }}:
            {{
              appStore.workerSmokeResults[worker.name].ok
                ? $t("settings.ready")
                : appStore.workerSmokeResults[worker.name].detail
            }}
          </div>
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
