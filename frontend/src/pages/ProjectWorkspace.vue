<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useI18n } from "vue-i18n";

import { apiClient } from "@/api/client";
import { useAppStore } from "@/stores/app";
import ExportConfirmDialog from "@/components/ExportConfirmDialog.vue";
import InpaintMaskEditor from "@/components/InpaintMaskEditor.vue";
import LicenseReportView from "@/components/LicenseReportView.vue";
import TrainingEntities from "@/components/TrainingEntities.vue";
import type { AssetRecord, ConsultantAnalysis, ConversationEntry, GenerationJob } from "@/types/api";
import { Modality, PageKey } from "@/types/enums";

const route = useRoute();
const { t } = useI18n();
const appStore = useAppStore();

const projectId = computed<string>(() => String(route.params.projectId ?? ""));
const project = computed(() => appStore.projects.find((item) => item.id === projectId.value) ?? null);
const conversationEntries = computed<ConversationEntry[]>(() => appStore.projectConversations[projectId.value] ?? []);
const conversationTotal = computed<number>(() => appStore.projectConversationTotals[projectId.value] ?? conversationEntries.value.length);
const projectJobs = computed(() => appStore.projectJobs[projectId.value] ?? []);
const projectAssets = computed(() => appStore.projectAssets[projectId.value] ?? []);
const projectPlans = computed(() => appStore.projectPlans[projectId.value] ?? []);
const projectLicenseReport = computed(() => appStore.projectLicenseReports[projectId.value] ?? null);
const projectTrainingJobs = computed(() => appStore.projectTrainingJobs[projectId.value] ?? []);
const recentJobs = computed(() => projectJobs.value.slice().reverse().slice(0, 6));
const recentAssets = computed(() => projectAssets.value.slice().reverse().slice(0, 6));
const recentPlans = computed(() => projectPlans.value.slice(0, 6));
const readyJobs = computed(() => projectJobs.value.filter((job) => job.status === "ready" || job.status === "planned"));
const latestAnalysis = computed<ConsultantAnalysis | null>(() => {
  const assistantEntry = [...conversationEntries.value].reverse().find((entry) => entry.role === "assistant" && entry.analysis);
  return assistantEntry?.analysis ?? appStore.consultantResponse?.analysis ?? null;
});
const recentAssistantEntry = computed<ConversationEntry | null>(
  () => [...conversationEntries.value].reverse().find((entry) => entry.role === "assistant") ?? null,
);
const form = reactive({
  prompt: "",
});
const trainingForm = reactive({
  title: "",
  modality: Modality.IMAGE,
  dataset_path: "",
  worker: "",
});

const conversationViewport = ref<HTMLElement | null>(null);
const scrollTop = ref<number>(0);
const estimatedRowHeight = 188;
const viewportHeight = 560;
const overscan = 4;
const visibleCount = Math.ceil(viewportHeight / estimatedRowHeight) + overscan * 2;

const windowedConversation = computed(() => {
  const startIndex = Math.max(Math.floor(scrollTop.value / estimatedRowHeight) - overscan, 0);
  const endIndex = Math.min(startIndex + visibleCount, conversationEntries.value.length);
  return {
    startIndex,
    endIndex,
    items: conversationEntries.value.slice(startIndex, endIndex),
    topPadding: startIndex * estimatedRowHeight,
    bottomPadding: Math.max((conversationEntries.value.length - endIndex) * estimatedRowHeight, 0),
  };
});

const hasMoreConversation = computed<boolean>(() => conversationEntries.value.length < conversationTotal.value);
const deliverables = computed(() => latestAnalysis.value?.deliverables ?? []);
const executionSteps = computed(() => latestAnalysis.value?.execution_steps ?? []);
const guidancePath = computed(() => latestAnalysis.value?.guidance_path ?? []);
const blockingReasons = computed(() => latestAnalysis.value?.blocking_reasons ?? []);
const researchItems = computed(() => latestAnalysis.value?.required_research ?? []);
const searchQueries = computed(() => latestAnalysis.value?.search_queries ?? []);
const recommendedWorkers = computed(() => latestAnalysis.value?.recommended_workers ?? []);
const executingJobId = ref<string | null>(null);
const savingJobId = ref<string | null>(null);
const jobWorkerDrafts = ref<Record<string, string>>({});
const jobRecipeDrafts = ref<Record<string, string>>({});
const jobSourceDrafts = ref<Record<string, string>>({});
const jobMaskDrafts = ref<Record<string, string>>({});
const sourceUploadFiles = ref<Record<string, File | null>>({});
const maskUploadFiles = ref<Record<string, File | null>>({});
const loadingLicenseReport = ref<boolean>(false);
/** Controls visibility of the export-confirm dialog (spec §2 / M5.7). */
const showExportConfirm = ref<boolean>(false);

const exportDownloadUrl = computed<string>(() =>
  projectId.value ? apiClient.exportProjectDownloadUrl(projectId.value, true) : "#",
);

/** Opens the export-confirm dialog instead of downloading directly. */
function openExportConfirm(): void {
  if (!projectId.value) {
    return;
  }
  showExportConfirm.value = true;
}

/** Called when the user cancels the export-confirm dialog. */
function onExportCancelled(): void {
  showExportConfirm.value = false;
}

/** Called after the user confirmed — dialog closes. */
function onExportConfirmed(): void {
  showExportConfirm.value = false;
}

function formatList(items: string[], separator = " / "): string {
  return items.length ? items.join(separator) : "-";
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}

function scrollConversationToBottom(): void {
  if (!conversationViewport.value) {
    return;
  }
  conversationViewport.value.scrollTop = conversationViewport.value.scrollHeight;
  scrollTop.value = conversationViewport.value.scrollTop;
}

watch(
  projectId,
  async (nextProjectId) => {
    if (!nextProjectId) {
      return;
    }
    await Promise.all([appStore.loadProject(nextProjectId), appStore.selectProject(nextProjectId)]);
    const draft = appStore.getStudioDraft(nextProjectId);
    form.prompt = draft.prompt;
    await appStore.loadProjectTrainingWorkspace(nextProjectId);
    scrollTop.value = 0;
    await nextTick();
    scrollConversationToBottom();
  },
  { immediate: true },
);

watch(
  form,
  (value) => {
    if (!projectId.value) {
      return;
    }
    appStore.updateStudioDraft(projectId.value, value);
  },
  { deep: true },
);

watch(
  projectJobs,
  (jobs) => {
    const nextRecipeDrafts = { ...jobRecipeDrafts.value };
    const nextWorkerDrafts = { ...jobWorkerDrafts.value };
    const nextSourceDrafts = { ...jobSourceDrafts.value };
    const nextMaskDrafts = { ...jobMaskDrafts.value };
    for (const job of jobs) {
      nextWorkerDrafts[job.id] = job.worker ?? "";
      nextRecipeDrafts[job.id] = job.recipe ?? "auto";
      nextSourceDrafts[job.id] = job.source_asset_id ?? "";
      nextMaskDrafts[job.id] = job.mask_asset_id ?? "";
    }
    jobWorkerDrafts.value = nextWorkerDrafts;
    jobRecipeDrafts.value = nextRecipeDrafts;
    jobSourceDrafts.value = nextSourceDrafts;
    jobMaskDrafts.value = nextMaskDrafts;
  },
  { immediate: true },
);

function onConversationScroll(): void {
  scrollTop.value = conversationViewport.value?.scrollTop ?? 0;
}

async function loadOlderConversation(): Promise<void> {
  if (!projectId.value || !hasMoreConversation.value) {
    return;
  }
  const previousScrollHeight = conversationViewport.value?.scrollHeight ?? 0;
  const previousScrollTop = conversationViewport.value?.scrollTop ?? 0;
  await appStore.loadProjectConversation(projectId.value, false);
  await nextTick();
  if (conversationViewport.value) {
    conversationViewport.value.scrollTop = conversationViewport.value.scrollHeight - previousScrollHeight + previousScrollTop;
    scrollTop.value = conversationViewport.value.scrollTop;
  }
}

async function requestClarification(): Promise<void> {
  if (!projectId.value || !form.prompt.trim()) {
    return;
  }
  try {
    await appStore.requestProjectClarification(projectId.value, { prompt: form.prompt });
    const draft = appStore.getStudioDraft(projectId.value);
    form.prompt = draft.prompt;
    await nextTick();
    scrollConversationToBottom();
  } catch {
    return;
  }
}

function canExecuteJob(status: string): boolean {
  return status === "ready" || status === "planned";
}

async function executeJob(jobId: string): Promise<void> {
  if (!projectId.value) {
    return;
  }
  executingJobId.value = jobId;
  try {
    await appStore.executeProjectJob(projectId.value, jobId);
  } finally {
    executingJobId.value = null;
  }
}

async function executeReadyJobs(): Promise<void> {
  if (!projectId.value || !readyJobs.value.length) {
    return;
  }
  executingJobId.value = "__batch__";
  try {
    await appStore.executeReadyProjectJobs(projectId.value, readyJobs.value.map((job) => job.id));
  } finally {
    executingJobId.value = null;
  }
}

/** Human-readable batch summary from the last execute-ready call (spec §5.14). */
const batchSummary = computed<string | null>(() => {
  const result = appStore.lastBatchResult;
  if (result === null) {
    return null;
  }
  const parts: string[] = [];
  if (result.executedCount > 0 || result.skipped.length === 0) {
    parts.push(t("chat.batchResultExecuted", { count: result.executedCount }));
  }
  if (result.skipped.length > 0) {
    const reasons = result.skipped.map((s) => s.reason).join("; ");
    parts.push(t("chat.batchResultSkipped", { count: result.skipped.length, reasons }));
  }
  return parts.join(" / ");
});

function showRecipeControls(job: GenerationJob): boolean {
  return currentWorker(job) === "comfyui";
}

function showSourceControls(job: GenerationJob): boolean {
  return ["comfyui", "gpt-sovits", "gpt_sovits", "voxcpm", "ultimate-rvc", "stable-audio-tools", "ace-step"].includes(currentWorker(job));
}

function showMaskControls(job: GenerationJob): boolean {
  return showRecipeControls(job) && currentRecipe(job) === "inpaint";
}

function currentWorker(job: GenerationJob): string {
  return jobWorkerDrafts.value[job.id] ?? job.worker ?? "";
}

function currentRecipe(job: GenerationJob): string {
  return jobRecipeDrafts.value[job.id] ?? job.recipe ?? "auto";
}

function availableSourceAssets(job: GenerationJob): AssetRecord[] {
  if (currentWorker(job) === "comfyui") {
    return projectAssets.value.filter((asset) => asset.modality === Modality.IMAGE);
  }
  return projectAssets.value.filter((asset) => asset.modality === Modality.VOICE || asset.modality === Modality.MUSIC);
}

function availableMaskAssets(): AssetRecord[] {
  return projectAssets.value.filter((asset) => asset.modality === Modality.IMAGE);
}

function onSourceUploadSelected(jobId: string, event: Event): void {
  const input = event.target as HTMLInputElement | null;
  sourceUploadFiles.value = {
    ...sourceUploadFiles.value,
    [jobId]: input?.files?.[0] ?? null,
  };
}

function onMaskUploadSelected(jobId: string, event: Event): void {
  const input = event.target as HTMLInputElement | null;
  maskUploadFiles.value = {
    ...maskUploadFiles.value,
    [jobId]: input?.files?.[0] ?? null,
  };
}

async function saveJobSettings(job: GenerationJob): Promise<void> {
  if (!projectId.value) {
    return;
  }
  savingJobId.value = job.id;
  try {
    await appStore.updateProjectJob(projectId.value, job.id, {
      worker: currentWorker(job) || null,
      recipe: showRecipeControls(job) ? currentRecipe(job) : null,
      source_asset_id: jobSourceDrafts.value[job.id] || null,
      mask_asset_id: jobMaskDrafts.value[job.id] || null,
    });
  } finally {
    savingJobId.value = null;
  }
}

async function uploadAssetForJob(job: GenerationJob, kind: "source" | "mask"): Promise<void> {
  if (!projectId.value) {
    return;
  }
  const file = kind === "source" ? sourceUploadFiles.value[job.id] : maskUploadFiles.value[job.id];
  if (!file) {
    return;
  }
  const title = kind === "source" ? `Source for ${job.title} - ${file.name}` : `Mask for ${job.title} - ${file.name}`;
  const modality =
    kind === "mask" || currentWorker(job) === "comfyui"
      ? Modality.IMAGE
      : currentWorker(job) === "stable-audio-tools" || currentWorker(job) === "ace-step"
        ? Modality.MUSIC
      : Modality.VOICE;
  const assetType =
    kind === "mask"
      ? "mask"
      : currentWorker(job) === "comfyui"
        ? "input_image"
        : currentWorker(job) === "stable-audio-tools" || currentWorker(job) === "ace-step"
          ? "input_audio"
        : "reference_audio";
  savingJobId.value = job.id;
  try {
    await appStore.importProjectAsset(projectId.value, {
      file,
      modality,
      asset_type: assetType,
      title,
      description: title,
    });
    const uploadedAsset = [...projectAssets.value]
      .slice()
      .reverse()
      .find((asset) => asset.title === title || asset.path.endsWith(file.name));
    if (uploadedAsset) {
      if (kind === "source") {
        jobSourceDrafts.value = {
          ...jobSourceDrafts.value,
          [job.id]: uploadedAsset.id,
        };
      } else {
        jobMaskDrafts.value = {
          ...jobMaskDrafts.value,
          [job.id]: uploadedAsset.id,
        };
      }
      await saveJobSettings(job);
    }
  } finally {
    if (kind === "source") {
      sourceUploadFiles.value = { ...sourceUploadFiles.value, [job.id]: null };
    } else {
      maskUploadFiles.value = { ...maskUploadFiles.value, [job.id]: null };
    }
    savingJobId.value = null;
  }
}

async function loadLicenseReport(): Promise<void> {
  if (!projectId.value) {
    return;
  }
  loadingLicenseReport.value = true;
  try {
    await appStore.loadProjectLicenseReport(projectId.value);
  } finally {
    loadingLicenseReport.value = false;
  }
}

async function createTrainingJob(): Promise<void> {
  if (!projectId.value || !trainingForm.title.trim() || !trainingForm.dataset_path.trim()) {
    return;
  }
  await appStore.createProjectTrainingJob(projectId.value, {
    title: trainingForm.title,
    modality: trainingForm.modality,
    dataset_path: trainingForm.dataset_path,
    worker: trainingForm.worker.trim() || null,
  });
  trainingForm.title = "";
  trainingForm.dataset_path = "";
  trainingForm.worker = "";
}

// ---------------------------------------------------------------------------
// M5.9 — Inpaint mask editor wiring (spec §5.11)
// ---------------------------------------------------------------------------

/** The asset currently open in the mask editor, or null when closed. */
const inpaintTargetAsset = ref<AssetRecord | null>(null);

/** Whether an inpaint submit is in progress (mask upload + refine job creation). */
const inpaintSubmitting = ref<boolean>(false);

/** Error message for the inpaint flow (shown near the editor). */
const inpaintError = ref<string | null>(null);

/**
 * Returns true for image assets that can be the source of an inpaint job.
 * Excludes mask assets themselves to avoid circular references.
 */
function canInpaint(asset: AssetRecord): boolean {
  return asset.modality === Modality.IMAGE && asset.asset_type !== "mask";
}

/**
 * Opens the inpaint mask editor for a given image asset.
 * @param asset - The source image asset to inpaint.
 */
function openInpaintEditor(asset: AssetRecord): void {
  inpaintTargetAsset.value = asset;
  inpaintError.value = null;
}

/** Closes the inpaint mask editor without submitting. */
function closeInpaintEditor(): void {
  inpaintTargetAsset.value = null;
  inpaintError.value = null;
}

/**
 * Handles the submit event from InpaintMaskEditor.
 * Flow:
 *   1. Upload maskBlob as a new asset (modality=image, asset_type=mask).
 *   2. Call refineAsset with strategy=inpaint, mask_asset_id = uploaded asset id.
 *   3. Close the editor and refresh workspace.
 *
 * @param payload - { maskBlob: PNG blob, prompt: user edit description }
 */
async function onInpaintSubmit(payload: { maskBlob: Blob; prompt: string }): Promise<void> {
  if (!projectId.value || !inpaintTargetAsset.value) {
    return;
  }
  inpaintSubmitting.value = true;
  inpaintError.value = null;
  const sourceAsset = inpaintTargetAsset.value;
  const maskFileName = `mask_${sourceAsset.id}_${Date.now()}.png`;
  try {
    // Step 1: upload the painted mask PNG as an asset.
    const maskFile = new File([payload.maskBlob], maskFileName, { type: "image/png" });
    await appStore.importProjectAsset(projectId.value, {
      file: maskFile,
      modality: Modality.IMAGE,
      asset_type: "mask",
      title: `Mask for ${sourceAsset.title}`,
      description: `Inpaint mask painted by user for asset ${sourceAsset.id}`,
    });

    // Step 2: find the uploaded mask asset by title/filename.
    const uploadedMask = [...projectAssets.value]
      .slice()
      .reverse()
      .find(
        (asset) =>
          asset.asset_type === "mask" &&
          (asset.title === `Mask for ${sourceAsset.title}` || asset.path.endsWith(maskFileName)),
      );

    if (!uploadedMask) {
      inpaintError.value = t("inpaint.maskUploadError");
      return;
    }

    // Step 3: create the inpaint refine job.
    await appStore.refineAsset(projectId.value, sourceAsset.id, {
      instruction: payload.prompt || `Inpaint edit on ${sourceAsset.title}`,
      title: `${sourceAsset.title} (inpaint)`,
      strategy: "inpaint",
      mask_asset_id: uploadedMask.id,
    });

    closeInpaintEditor();
  } catch {
    inpaintError.value = t("inpaint.maskUploadError");
  } finally {
    inpaintSubmitting.value = false;
  }
}
</script>

<template>
  <section class="grid gap-5">
    <div v-if="!project" class="rounded-2xl border border-app-warning bg-app-warning/10 px-4 py-3 text-sm text-app-text">
      {{ $t("chat.noProjectNotice") }}
    </div>

    <template v-else>
      <section class="app-panel grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div class="min-w-0 space-y-4">
          <div class="space-y-2">
            <p class="app-kicker">{{ project.type }}</p>
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="app-title text-2xl sm:text-3xl">{{ project.name }}</h2>
              <span
                v-for="modality in latestAnalysis?.inferred_modalities ?? []"
                :key="`hero-${modality}`"
                class="app-chip"
              >
                {{ modality }}
              </span>
            </div>
            <p class="max-w-4xl text-sm leading-7 text-app-muted">{{ project.synopsis || $t("project.noSynopsis") }}</p>
          </div>

          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <div class="app-panel-muted">
              <p class="app-kicker">{{ $t("project.type") }}</p>
              <p class="mt-2 text-base font-semibold text-app-text">{{ project.type }}</p>
            </div>
            <div class="app-panel-muted">
              <p class="app-kicker">{{ $t("chat.planTitle") }}</p>
              <p class="mt-2 text-base font-semibold text-app-text">{{ formatList(latestAnalysis?.inferred_modalities ?? []) }}</p>
            </div>
            <div class="app-panel-muted">
              <p class="app-kicker">{{ $t("chat.jobsTitle") }}</p>
              <p class="mt-2 text-base font-semibold text-app-text">{{ projectJobs.length }}</p>
            </div>
            <div class="app-panel-muted">
              <p class="app-kicker">{{ $t("chat.historyTitle") }}</p>
              <p class="mt-2 text-base font-semibold text-app-text">{{ conversationTotal }}</p>
            </div>
          </div>
        </div>

        <div class="flex flex-wrap gap-3 lg:justify-end">
          <button
            class="app-button"
            type="button"
            :disabled="!readyJobs.length || executingJobId === '__batch__'"
            @click="executeReadyJobs"
          >
            {{ executingJobId === "__batch__" ? $t("chat.executingReadyAction") : $t("chat.executeReadyAction") }}
          </button>
          <button class="app-button-secondary" type="button" @click="appStore.setAssetDrawerOpen(true)">
            {{ $t("assets.title") }}
          </button>
          <!-- Export opens the license-confirm dialog before downloading (spec §2 / M5.7). -->
          <button class="app-button-secondary" type="button" :disabled="!projectId" @click="openExportConfirm">
            {{ $t("project.exportAction") }}
          </button>
          <button class="app-button-secondary" type="button" :disabled="loadingLicenseReport" @click="loadLicenseReport">
            {{ loadingLicenseReport ? $t("project.loadingLicenseAction") : $t("project.licenseAction") }}
          </button>
          <RouterLink class="app-button-secondary" :to="{ name: PageKey.VERSIONS, params: { projectId } }">
            {{ $t("project.versionsAction") }}
          </RouterLink>
        </div>
        <!-- Batch-execute summary: shows executed count + any skipped/blocked jobs (spec §5.14) -->
        <p v-if="batchSummary" class="mt-1 text-sm app-muted">{{ batchSummary }}</p>
      </section>

      <!-- License Report view (spec §2 / M5.7): enriched tri-state per-worker table + summary -->
      <section v-if="projectLicenseReport" class="app-panel grid gap-5">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 class="app-section-title">{{ $t("licenseReport.title") }}</h3>
            <p class="app-muted">{{ projectLicenseReport.project_name }}</p>
          </div>
          <p class="text-sm text-app-muted">{{ formatDateTime(projectLicenseReport.generated_at) }}</p>
        </div>
        <!-- Delegates all rendering to LicenseReportView for re-use in the export dialog -->
        <LicenseReportView :report="projectLicenseReport" />
      </section>

      <!-- Export-confirm dialog (spec §2 / M5.7): shown before the user downloads the zip -->
      <ExportConfirmDialog
        v-if="showExportConfirm && projectId"
        :project-id="projectId"
        :download-url="exportDownloadUrl"
        @cancel="onExportCancelled"
        @confirmed="onExportConfirmed"
      />

      <section class="grid gap-5 2xl:grid-cols-[minmax(0,1.38fr)_23rem] 2xl:items-start">
        <div class="grid gap-5">
          <section class="app-panel grid gap-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="app-section-title">{{ $t("chat.title") }}</h2>
              <span class="app-chip">{{ $t("chat.autoModalityHint") }}</span>
            </div>
            <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
              <label class="grid gap-2">
                <span class="app-muted">{{ $t("chat.prompt") }}</span>
                <textarea
                  v-model="form.prompt"
                  class="app-input min-h-36 resize-y"
                  rows="6"
                  :placeholder="$t('chat.promptPlaceholder')"
                ></textarea>
              </label>
              <div class="app-panel-muted flex flex-col justify-between gap-4">
                <div class="grid gap-2">
                  <p class="app-kicker">{{ $t("chat.nextStep") }}</p>
                  <p class="text-sm leading-7 text-app-text">
                    {{ appStore.consultantResponse?.next_step ?? $t("chat.autoModalityHint") }}
                  </p>
                </div>
                <button class="app-button w-full" :disabled="!form.prompt.trim()" @click="requestClarification">
                  {{ $t("chat.submit") }}
                </button>
              </div>
            </div>
          </section>

          <section class="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(18rem,0.9fr)]">
            <section class="app-panel grid gap-4">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <h2 class="app-section-title">{{ $t("chat.planTitle") }}</h2>
                <span v-if="latestAnalysis?.inferred_modalities.length" class="app-chip">
                  {{ latestAnalysis.inferred_modalities.join(" / ") }}
                </span>
              </div>
              <p v-if="!latestAnalysis" class="app-muted">{{ $t("chat.noPlan") }}</p>
              <template v-else>
                <p class="text-sm leading-7 text-app-text">{{ appStore.consultantResponse?.summary ?? latestAnalysis.objective }}</p>

                <div class="grid gap-3 sm:grid-cols-2">
                  <div class="app-panel-muted">
                    <p class="app-kicker">{{ $t("chat.franchise") }}</p>
                    <p class="mt-2 text-sm leading-7 text-app-text">{{ latestAnalysis.franchise || "-" }}</p>
                  </div>
                  <div class="app-panel-muted">
                    <p class="app-kicker">{{ $t("chat.characters") }}</p>
                    <p class="mt-2 text-sm leading-7 text-app-text">{{ formatList(latestAnalysis.characters, ", ") }}</p>
                  </div>
                  <div class="app-panel-muted">
                    <p class="app-kicker">{{ $t("chat.outfits") }}</p>
                    <p class="mt-2 text-sm leading-7 text-app-text">{{ formatList(latestAnalysis.outfits, ", ") }}</p>
                  </div>
                  <div class="app-panel-muted">
                    <p class="app-kicker">{{ $t("chat.matrixAxes") }}</p>
                    <p class="mt-2 text-sm leading-7 text-app-text">{{ formatList(latestAnalysis.matrix_axes, " × ") }}</p>
                  </div>
                </div>

                <div v-if="deliverables.length" class="grid gap-2">
                  <h3 class="font-semibold text-app-text">{{ $t("chat.deliverables") }}</h3>
                  <ul class="grid gap-2">
                    <li
                      v-for="deliverable in deliverables"
                      :key="`${deliverable.modality}-${deliverable.title}`"
                      class="app-panel-muted"
                    >
                      <div class="flex flex-wrap items-center justify-between gap-2">
                        <strong class="text-sm text-app-text">{{ deliverable.title }}</strong>
                        <span class="app-chip">{{ deliverable.modality }}</span>
                      </div>
                      <p class="mt-2 text-sm leading-7 text-app-muted">{{ formatList(deliverable.variants, ", ") }}</p>
                      <p v-if="deliverable.worker" class="mt-2 text-sm text-app-text">
                        {{ $t("chat.worker") }}: {{ deliverable.worker }}
                      </p>
                    </li>
                  </ul>
                </div>
              </template>
            </section>

            <section class="app-panel grid gap-4">
              <h2 class="app-section-title">{{ $t("chat.nextStep") }}</h2>
              <template v-if="latestAnalysis">
                <div v-if="blockingReasons.length" class="grid gap-2">
                  <h3 class="font-semibold text-app-warning">{{ $t("chat.blockingReason") }}</h3>
                  <ul class="grid gap-2">
                    <li v-for="item in blockingReasons" :key="item" class="rounded-2xl border border-app-warning/30 bg-app-warning/10 px-4 py-3 text-sm text-app-text">
                      {{ item }}
                    </li>
                  </ul>
                </div>

                <div v-if="guidancePath.length" class="grid gap-2">
                  <h3 class="font-semibold text-app-text">{{ $t("chat.guidancePath") }}</h3>
                  <ol class="grid gap-2 list-decimal pl-5 text-sm leading-7 text-app-text">
                    <li v-for="item in guidancePath" :key="item">{{ item }}</li>
                  </ol>
                </div>

                <div v-if="executionSteps.length" class="grid gap-2">
                  <h3 class="font-semibold text-app-text">{{ $t("chat.executionSteps") }}</h3>
                  <ol class="grid gap-2">
                    <li
                      v-for="step in executionSteps"
                      :key="`${step.title}-${step.worker ?? 'none'}`"
                      class="app-panel-muted"
                    >
                      <strong class="text-sm text-app-text">{{ step.title }}</strong>
                      <p class="mt-2 text-sm leading-7 text-app-muted">{{ step.detail }}</p>
                      <p v-if="step.worker" class="mt-2 text-sm text-app-text">{{ $t("chat.worker") }}: {{ step.worker }}</p>
                    </li>
                  </ol>
                </div>
              </template>
              <p v-else class="app-muted">{{ $t("chat.noPlan") }}</p>
            </section>
          </section>

          <section class="app-panel grid gap-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="app-section-title">{{ $t("chat.historyTitle") }}</h2>
              <div class="flex flex-wrap items-center gap-2">
                <span class="app-chip">{{ conversationEntries.length }} / {{ conversationTotal }}</span>
                <button
                  v-if="hasMoreConversation"
                  class="app-button-secondary"
                  type="button"
                  @click="loadOlderConversation"
                >
                  {{ $t("chat.loadOlder") }}
                </button>
              </div>
            </div>
            <p class="app-muted">
              {{ recentAssistantEntry ? formatDateTime(recentAssistantEntry.created_at) : $t("chat.historyEmpty") }}
            </p>
            <p v-if="!conversationEntries.length" class="app-muted">{{ $t("chat.historyEmpty") }}</p>
            <div
              v-else
              ref="conversationViewport"
              class="max-h-[34rem] overflow-y-auto rounded-[24px] border border-app-border bg-app-surfaceAlt/75 p-4"
              @scroll="onConversationScroll"
            >
              <div :style="{ height: `${windowedConversation.topPadding}px` }"></div>
              <ul class="grid gap-3">
                <li
                  v-for="entry in windowedConversation.items"
                  :key="entry.id"
                  class="max-w-[94%] rounded-[24px] border p-4 shadow-sm"
                  :class="
                    entry.role === 'user'
                      ? 'ml-auto border-app-primary/30 bg-app-primary/10'
                      : 'mr-auto border-app-border bg-app-surface'
                  "
                >
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="app-chip">
                      {{ entry.role === "user" ? $t("chat.userRole") : $t("chat.assistantRole") }}
                    </span>
                    <span class="text-xs text-app-muted">{{ formatDateTime(entry.created_at) }}</span>
                  </div>
                  <p class="mt-3 whitespace-pre-wrap break-words text-sm leading-7 text-app-text">{{ entry.content }}</p>
                  <ol v-if="entry.questions.length" class="mt-3 grid gap-2 list-decimal pl-5 text-sm leading-7 text-app-text">
                    <li v-for="question in entry.questions" :key="`${entry.id}-${question}`">
                      {{ question }}
                    </li>
                  </ol>
                </li>
              </ul>
              <div :style="{ height: `${windowedConversation.bottomPadding}px` }"></div>
            </div>
          </section>
        </div>

        <aside class="grid gap-5 2xl:sticky 2xl:top-6 2xl:self-start">
          <section class="app-panel grid gap-4">
            <h2 class="app-section-title">{{ $t("chat.nextStep") }}</h2>
            <div class="grid gap-4">
              <div v-if="researchItems.length" class="grid gap-2">
                <h3 class="font-semibold text-app-text">{{ $t("chat.requiredResearch") }}</h3>
                <ul class="grid gap-2">
                  <li v-for="item in researchItems" :key="item" class="app-panel-muted text-sm leading-7 text-app-text">
                    {{ item }}
                  </li>
                </ul>
              </div>

              <div v-if="searchQueries.length" class="grid gap-2">
                <h3 class="font-semibold text-app-text">{{ $t("chat.searchQueries") }}</h3>
                <ul class="grid gap-2">
                  <li v-for="query in searchQueries" :key="query" class="app-panel-muted text-sm leading-7 text-app-text">
                    {{ query }}
                  </li>
                </ul>
              </div>

              <div v-if="recommendedWorkers.length" class="grid gap-2">
                <h3 class="font-semibold text-app-text">{{ $t("chat.worker") }}</h3>
                <div class="flex flex-wrap gap-2">
                  <span v-for="worker in recommendedWorkers" :key="worker" class="app-chip">{{ worker }}</span>
                </div>
              </div>
            </div>
          </section>

          <!-- §7.1.1 training entity CRUD + consultant suggestion cards (M4.c) -->
          <TrainingEntities :project-id="projectId" :analysis="latestAnalysis" />

          <section class="app-panel grid gap-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="app-section-title">{{ $t("training.title") }}</h2>
              <span class="app-chip">{{ projectTrainingJobs.length }}</span>
            </div>
            <p class="app-muted">{{ $t("training.intro") }}</p>
            <div class="grid gap-3">
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.name") }}</span>
                <input v-model="trainingForm.title" class="app-input" :placeholder="$t('training.namePlaceholder')" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.modality") }}</span>
                <select v-model="trainingForm.modality" class="app-input">
                  <option :value="Modality.IMAGE">{{ $t("modality.image") }}</option>
                  <option :value="Modality.VOICE">{{ $t("modality.voice") }}</option>
                </select>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.datasetPath") }}</span>
                <input v-model="trainingForm.dataset_path" class="app-input" :placeholder="$t('training.datasetPlaceholder')" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.worker") }}</span>
                <input v-model="trainingForm.worker" class="app-input" :placeholder="$t('training.workerPlaceholder')" />
              </label>
              <button class="app-button-secondary" type="button" :disabled="!trainingForm.title.trim() || !trainingForm.dataset_path.trim()" @click="createTrainingJob">
                {{ $t("training.createAction") }}
              </button>
            </div>
            <p v-if="!projectTrainingJobs.length" class="app-muted">{{ $t("training.empty") }}</p>
            <ul v-else class="grid gap-3">
              <li v-for="job in projectTrainingJobs" :key="job.id" class="app-panel-muted">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <strong class="text-sm text-app-text">{{ job.title }}</strong>
                  <span class="app-chip">{{ job.status }}</span>
                </div>
                <p class="mt-2 text-sm text-app-text">{{ job.worker }} · {{ job.modality }}</p>
                <p class="mt-1 break-all text-sm text-app-muted">{{ job.dataset_path }}</p>
                <p v-if="job.note" class="mt-2 text-sm text-app-warning">{{ job.note }}</p>
              </li>
            </ul>
          </section>

          <section class="app-panel grid gap-3">
            <h2 class="app-section-title">{{ $t("chat.jobsTitle") }}</h2>
            <p v-if="!recentJobs.length" class="app-muted">{{ $t("chat.noJobs") }}</p>
            <ul v-else class="grid gap-3">
              <li v-for="job in recentJobs" :key="job.id" class="app-panel-muted">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <strong class="text-sm text-app-text">{{ job.title }}</strong>
                  <span class="app-chip">{{ job.status }}</span>
                </div>
                <p class="mt-2 text-sm leading-7 text-app-muted">{{ job.summary }}</p>
                <p v-if="job.recipe" class="mt-2 text-sm text-app-text">{{ $t("chat.recipe") }}: {{ job.recipe }}</p>
                <p v-if="job.worker" class="mt-2 text-sm text-app-text">{{ $t("chat.worker") }}: {{ job.worker }}</p>
                <p v-if="job.progress_label" class="mt-2 text-sm text-app-text">
                  {{ $t("chat.progress") }}: {{ job.progress }}% · {{ job.progress_label }}
                </p>
                <p v-if="job.blocking_reason" class="mt-2 text-sm text-app-warning">
                  {{ $t("chat.blockingReason") }}: {{ job.blocking_reason }}
                </p>
                <p v-if="job.last_error" class="mt-2 text-sm text-app-warning">
                  {{ $t("chat.lastError") }}: {{ job.last_error }}
                </p>
                <div v-if="showRecipeControls(job) || showSourceControls(job)" class="mt-3 grid gap-3">
                  <label class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted">{{ $t("chat.worker") }}</span>
                    <select v-model="jobWorkerDrafts[job.id]" class="app-input">
                      <option v-if="job.worker" :value="job.worker">{{ job.worker }}</option>
                      <option
                        v-for="worker in recommendedWorkers.filter((worker) => worker !== job.worker)"
                        :key="`${job.id}-worker-${worker}`"
                        :value="worker"
                      >
                        {{ worker }}
                      </option>
                    </select>
                  </label>
                  <label v-if="showRecipeControls(job)" class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted">{{ $t("chat.recipe") }}</span>
                    <select v-model="jobRecipeDrafts[job.id]" class="app-input">
                      <option value="auto">{{ $t("chat.recipeAuto") }}</option>
                      <option value="txt2img">txt2img</option>
                      <option value="img2img">img2img</option>
                      <option value="inpaint">inpaint</option>
                    </select>
                  </label>
                  <label v-if="showSourceControls(job)" class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted">{{ $t("chat.sourceAsset") }}</span>
                    <select v-model="jobSourceDrafts[job.id]" class="app-input">
                      <option value="">{{ $t("chat.noneOption") }}</option>
                      <option v-for="asset in availableSourceAssets(job)" :key="`${job.id}-source-${asset.id}`" :value="asset.id">
                        {{ asset.title }}
                      </option>
                    </select>
                  </label>
                  <div v-if="showSourceControls(job)" class="grid gap-2">
                    <input
                      class="text-sm text-app-muted file:mr-3 file:rounded-xl file:border-0 file:bg-app-primary/12 file:px-3 file:py-2 file:text-app-text"
                      type="file"
                      :accept="currentWorker(job) === 'comfyui' ? 'image/*' : 'audio/*'"
                      @change="onSourceUploadSelected(job.id, $event)"
                    />
                    <button
                      class="app-button-secondary"
                      type="button"
                      :disabled="!sourceUploadFiles[job.id] || savingJobId === job.id"
                      @click="uploadAssetForJob(job, 'source')"
                    >
                      {{ $t("chat.uploadSourceAction") }}
                    </button>
                  </div>
                  <label v-if="showMaskControls(job)" class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted">{{ $t("chat.maskAsset") }}</span>
                    <select v-model="jobMaskDrafts[job.id]" class="app-input">
                      <option value="">{{ $t("chat.noneOption") }}</option>
                      <option v-for="asset in availableMaskAssets()" :key="`${job.id}-mask-${asset.id}`" :value="asset.id">
                        {{ asset.title }}
                      </option>
                    </select>
                  </label>
                  <div v-if="showMaskControls(job)" class="grid gap-2">
                    <input
                      class="text-sm text-app-muted file:mr-3 file:rounded-xl file:border-0 file:bg-app-primary/12 file:px-3 file:py-2 file:text-app-text"
                      type="file"
                      accept="image/*"
                      @change="onMaskUploadSelected(job.id, $event)"
                    />
                    <button
                      class="app-button-secondary"
                      type="button"
                      :disabled="!maskUploadFiles[job.id] || savingJobId === job.id"
                      @click="uploadAssetForJob(job, 'mask')"
                    >
                      {{ $t("chat.uploadMaskAction") }}
                    </button>
                  </div>
                  <div class="flex justify-end">
                    <button
                      class="app-button-secondary"
                      type="button"
                      :disabled="savingJobId === job.id"
                      @click="saveJobSettings(job)"
                    >
                      {{ savingJobId === job.id ? $t("chat.savingJobAction") : $t("chat.saveJobAction") }}
                    </button>
                  </div>
                </div>
                <div v-if="canExecuteJob(job.status)" class="mt-3 flex justify-end">
                  <button
                    class="app-button-secondary"
                    type="button"
                    :disabled="executingJobId === job.id"
                    @click="executeJob(job.id)"
                  >
                    {{ executingJobId === job.id ? $t("chat.executingAction") : $t("chat.executeAction") }}
                  </button>
                </div>
              </li>
            </ul>
          </section>

          <section class="app-panel grid gap-3">
            <h2 class="app-section-title">{{ $t("chat.planHistoryTitle") }}</h2>
            <p v-if="!recentPlans.length" class="app-muted">{{ $t("chat.noPlanHistory") }}</p>
            <ul v-else class="grid gap-3">
              <li v-for="plan in recentPlans" :key="plan.id" class="app-panel-muted">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <strong class="text-sm text-app-text">{{ plan.title }}</strong>
                  <span class="text-xs text-app-muted">{{ formatDateTime(plan.created_at) }}</span>
                </div>
                <p class="mt-2 text-sm leading-7 text-app-muted">{{ plan.summary }}</p>
                <p class="mt-2 break-words text-xs leading-6 text-app-muted">{{ plan.path }}</p>
              </li>
            </ul>
          </section>

          <section class="app-panel grid gap-3">
            <h2 class="app-section-title">{{ $t("chat.assetsTitle") }}</h2>
            <p v-if="!recentAssets.length" class="app-muted">{{ $t("chat.noAssets") }}</p>
            <ul v-else class="grid gap-3">
              <li v-for="asset in recentAssets" :key="asset.id" class="app-panel-muted">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <strong class="text-sm text-app-text">{{ asset.title }}</strong>
                  <span class="app-chip">{{ asset.asset_type }}</span>
                </div>
                <p class="mt-2 break-words text-xs leading-6 text-app-muted">{{ asset.path }}</p>
                <p v-if="asset.description" class="mt-2 text-sm leading-7 text-app-text">{{ asset.description }}</p>
                <!-- M5.9: Inpaint button for image assets -->
                <div v-if="canInpaint(asset)" class="mt-3 flex justify-end">
                  <button
                    type="button"
                    class="app-button-secondary text-sm"
                    @click="openInpaintEditor(asset)"
                  >
                    {{ $t("inpaint.openEditorAction") }}
                  </button>
                </div>
              </li>
            </ul>
          </section>
        </aside>
      </section>

      <!-- M5.9: Inpaint mask editor modal -->
      <InpaintMaskEditor
        v-if="inpaintTargetAsset"
        :asset-id="inpaintTargetAsset.id"
        :image-url="apiClient.assetFileUrl(projectId, inpaintTargetAsset.id)"
        :submitting="inpaintSubmitting"
        @submit="onInpaintSubmit"
        @cancel="closeInpaintEditor"
      />

      <!-- Inpaint error banner (shown outside the editor after dismiss) -->
      <div
        v-if="inpaintError && !inpaintTargetAsset"
        class="fixed bottom-4 right-4 z-50 rounded-xl border border-app-warning/40 bg-app-surface px-4 py-3 text-sm text-app-warning shadow-lg"
      >
        {{ inpaintError }}
        <button class="ml-3 underline" type="button" @click="inpaintError = null">{{ $t("app.close") }}</button>
      </div>

    </template>
  </section>
</template>
