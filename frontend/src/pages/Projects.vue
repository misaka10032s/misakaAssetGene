<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";

import { useAppStore } from "@/stores/app";
import { useWindowSize } from "@/composables/useWindowSize";
import type { CreateProjectPayload } from "@/types/api";
import { PageKey, ProviderStatus } from "@/types/enums";

const appStore = useAppStore();
const router = useRouter();
const { splitGridClass } = useWindowSize();
const { t } = useI18n();
const form = reactive<CreateProjectPayload>({ ...appStore.projectDraft });
const hasReadySynopsisProvider = computed<boolean>(() =>
  appStore.integration.providers.some((provider) => provider.status === ProviderStatus.READY),
);
const hasFilledSynopsisInputs = computed<boolean>(() => Boolean(form.name.trim() && form.type.trim() && form.synopsis.trim()));
const canOptimizeSynopsis = computed<boolean>(() => hasReadySynopsisProvider.value && hasFilledSynopsisInputs.value);
const optimizeHintKey = computed<string>(() => {
  if (!appStore.integration.providers.length) {
    return "project.optimizeLoading";
  }
  if (!hasReadySynopsisProvider.value) {
    return "project.optimizeUnavailable";
  }
  if (!hasFilledSynopsisInputs.value) {
    return "project.optimizeIncomplete";
  }
  return "project.optimizeReady";
});

watch(
  form,
  (value) => {
    appStore.updateProjectDraft(value);
  },
  { deep: true },
);

async function createProject(): Promise<void> {
  try {
    const createdProject = await appStore.createProject({ ...form });
    await appStore.selectProject(createdProject.id);
    form.name = appStore.projectDraft.name;
    form.type = appStore.projectDraft.type;
    form.synopsis = appStore.projectDraft.synopsis;
    await router.push({ name: PageKey.PROJECT, params: { projectId: createdProject.id } });
  } catch {
    return;
  }
}

async function switchProject(projectId: string): Promise<void> {
  try {
    await appStore.selectProject(projectId);
    await router.push({ name: PageKey.PROJECT, params: { projectId } });
  } catch {
    return;
  }
}

async function optimizeSynopsis(): Promise<void> {
  if (!canOptimizeSynopsis.value) {
    return;
  }
  try {
    await appStore.optimizeSynopsis(form.name, form.type, form.synopsis);
  } catch {
    return;
  }
}

function applySynopsisSuggestion(): void {
  if (!appStore.synopsisSuggestion) {
    return;
  }
  form.synopsis = appStore.synopsisSuggestion.optimized_synopsis;
}

function mergeSynopsisSuggestion(): void {
  if (!appStore.synopsisSuggestion) {
    return;
  }
  form.synopsis = `${form.synopsis.trim()}\n\n${appStore.synopsisSuggestion.optimized_synopsis}`.trim();
}

function getProjectTypeLabel(projectType: string): string {
  return appStore.projectTypes.includes(projectType) ? t(`projectType.${projectType}`) : projectType;
}
</script>

<template>
  <section class="grid gap-4" :class="splitGridClass">
    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("project.createTitle") }}</h2>
      <label>
        <span class="app-muted">{{ $t("project.name") }}</span>
        <input v-model="form.name" class="app-input" :placeholder="$t('project.namePlaceholder')" />
      </label>
      <label>
        <span class="app-muted">{{ $t("project.type") }}</span>
        <input v-model="form.type" class="app-input" list="project-type-options" :placeholder="$t('project.typePlaceholder')" />
        <datalist id="project-type-options">
          <option v-for="projectType in appStore.projectTypes" :key="projectType" :value="projectType">
            {{ $t(`projectType.${projectType}`) }}
          </option>
        </datalist>
      </label>
      <label>
        <span class="app-muted">{{ $t("project.synopsis") }}</span>
        <textarea
          v-model="form.synopsis"
          class="app-input min-h-32"
          rows="5"
          :placeholder="$t('project.synopsisPlaceholder')"
        ></textarea>
      </label>
      <div class="flex flex-wrap gap-2">
        <button class="app-button" @click="createProject">
          {{ $t("project.createAction") }}
        </button>
        <button class="app-button-secondary" :disabled="!canOptimizeSynopsis" @click="optimizeSynopsis">
          {{ $t("project.optimizeAction") }}
        </button>
      </div>
      <p class="mt-3 app-muted">
        {{ $t(optimizeHintKey) }}
      </p>

      <div v-if="appStore.synopsisSuggestion" class="mt-4 rounded-xl border border-app-border bg-app-surfaceAlt p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="font-semibold text-app-text">{{ $t("project.optimizedTitle") }}</h3>
          <div class="flex items-center gap-2">
            <span class="app-muted">{{ appStore.synopsisSuggestion.provider ?? appStore.synopsisSuggestion.strategy }}</span>
            <button class="app-button-secondary" type="button" @click="appStore.closeSynopsisSuggestion()">
              {{ $t("app.close") }}
            </button>
          </div>
        </div>
        <p class="mt-3 whitespace-pre-wrap text-sm text-app-text">{{ appStore.synopsisSuggestion.optimized_synopsis }}</p>
        <div class="mt-4 flex flex-wrap gap-2">
          <button class="app-button" @click="applySynopsisSuggestion">
            {{ $t("project.applyOptimizedAction") }}
          </button>
          <button class="app-button-secondary" @click="mergeSynopsisSuggestion">
            {{ $t("project.mergeOptimizedAction") }}
          </button>
        </div>
      </div>
    </div>

    <div class="grid gap-4">
      <div class="app-panel">
        <h2 class="app-section-title">{{ $t("project.listTitle") }}</h2>
        <p v-if="!appStore.projects.length" class="app-muted">{{ $t("project.empty") }}</p>
        <ul v-else class="grid gap-3">
          <li v-for="project in appStore.projects" :key="project.id" class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <strong class="text-app-text">{{ project.name }}</strong>
                <span class="ml-2 app-muted">{{ getProjectTypeLabel(project.type) }}</span>
              </div>
              <div class="flex items-center gap-2">
                <span v-if="appStore.currentProjectId === project.id" class="app-muted text-app-primary">
                  {{ $t("project.currentTag") }}
                </span>
                <button class="app-button-secondary" @click="switchProject(project.id)">
                  {{ $t("project.switchAction") }}
                </button>
              </div>
            </div>
            <div class="mt-2 app-muted">{{ project.synopsis || $t("project.noSynopsis") }}</div>
          </li>
        </ul>
      </div>

      <details class="app-panel">
        <summary class="cursor-pointer text-app-text">{{ $t("project.schemaTitle") }}</summary>
        <p class="mt-3 app-muted">{{ $t("project.schemaDescription") }}</p>
        <pre class="mt-3 whitespace-pre-wrap break-all text-xs text-app-muted">{{ appStore.projectSchema }}</pre>
      </details>
    </div>
  </section>
</template>
