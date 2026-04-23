<script setup lang="ts">
import { reactive } from "vue";
import { useI18n } from "vue-i18n";

import { useAppStore } from "@/stores/app";
import { useWindowSize } from "@/composables/useWindowSize";
import type { CreateProjectPayload } from "@/types/api";

const appStore = useAppStore();
const { splitGridClass } = useWindowSize();
const { t } = useI18n();
const form = reactive<CreateProjectPayload>({
  name: "",
  type: "RPG",
  synopsis: "",
});

/**
 * Creates a project and resets the editable fields.
 */
async function createProject(): Promise<void> {
  await appStore.createProject({ ...form });
  form.name = "";
  form.synopsis = "";
}

/**
 * Switches the active project selection.
 *
 * @param projectName - The project name to activate.
 */
async function switchProject(projectName: string): Promise<void> {
  await appStore.selectProject(projectName);
}

/**
 * Requests an optimized synopsis draft.
 */
async function optimizeSynopsis(): Promise<void> {
  if (!form.name.trim() || !form.type.trim() || !form.synopsis.trim()) {
    return;
  }
  await appStore.optimizeSynopsis(form.name, form.type, form.synopsis);
}

/**
 * Replaces the synopsis with the optimized draft.
 */
function applySynopsisSuggestion(): void {
  if (!appStore.synopsisSuggestion) {
    return;
  }
  form.synopsis = appStore.synopsisSuggestion.optimized_synopsis;
}

/**
 * Appends the optimized synopsis below the current text for manual merging.
 */
function mergeSynopsisSuggestion(): void {
  if (!appStore.synopsisSuggestion) {
    return;
  }
  form.synopsis = `${form.synopsis.trim()}\n\n${appStore.synopsisSuggestion.optimized_synopsis}`.trim();
}

/**
 * Resolves a localized label for suggested project types and falls back to raw text for custom values.
 *
 * @param projectType - The stored project type string.
 * @returns A presentable label for the UI.
 */
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
        <button class="app-button-secondary" :disabled="!form.name.trim() || !form.type.trim() || !form.synopsis.trim()" @click="optimizeSynopsis">
          {{ $t("project.optimizeAction") }}
        </button>
      </div>

      <div v-if="appStore.synopsisSuggestion" class="mt-4 rounded-xl border border-app-border bg-app-surfaceAlt p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <h3 class="font-semibold text-app-text">{{ $t("project.optimizedTitle") }}</h3>
          <span class="app-muted">{{ appStore.synopsisSuggestion.provider ?? appStore.synopsisSuggestion.strategy }}</span>
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
          <li v-for="project in appStore.projects" :key="project.name" class="rounded-xl border border-app-border bg-app-surfaceAlt p-3">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <strong class="text-app-text">{{ project.name }}</strong>
                <span class="ml-2 app-muted">{{ getProjectTypeLabel(project.type) }}</span>
              </div>
              <div class="flex items-center gap-2">
                <span v-if="appStore.currentProjectName === project.name" class="app-muted text-app-primary">
                  {{ $t("project.currentTag") }}
                </span>
                <button class="app-button-secondary" @click="switchProject(project.name)">
                  {{ $t("project.switchAction") }}
                </button>
              </div>
            </div>
            <div class="mt-2 app-muted">{{ project.synopsis || $t("project.noSynopsis") }}</div>
          </li>
        </ul>
      </div>

      <div class="app-panel">
        <h2 class="app-section-title">{{ $t("project.schemaTitle") }}</h2>
        <pre class="whitespace-pre-wrap break-all text-xs text-app-muted">{{ appStore.projectSchema }}</pre>
      </div>
    </div>
  </section>
</template>
