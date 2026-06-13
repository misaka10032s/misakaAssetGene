<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";

import { apiClient } from "@/api/client";
import type { LicenseReportSummary } from "@/types/api";

const { t } = useI18n();

const props = defineProps<{
  /** Project ID whose license summary to display before export. */
  projectId: string;
  /** The full export download URL — navigated to on confirm. */
  downloadUrl: string;
}>();

const emit = defineEmits<{
  /** Emitted when the user cancels without exporting. */
  (event: "cancel"): void;
  /** Emitted when the user confirms and export begins. */
  (event: "confirmed"): void;
}>();

const loading = ref<boolean>(false);
const loadError = ref<boolean>(false);
const summary = ref<LicenseReportSummary | null>(null);

/** True when any confirmed-NSFW workers are present in the summary. */
const hasNsfw = computed<boolean>(() => (summary.value?.has_nsfw ?? false));

/** True when any unknown license fields are present — requires manual review. */
const hasUnknown = computed<boolean>(() => {
  if (!summary.value) {
    return false;
  }
  return (
    summary.value.commercial_unknown > 0 ||
    summary.value.attribution_unknown > 0 ||
    summary.value.nsfw_unknown > 0
  );
});

/** Fetches the license report summary for the project. */
async function loadSummary(): Promise<void> {
  loading.value = true;
  loadError.value = false;
  try {
    const report = await apiClient.projectLicenseReport(props.projectId);
    summary.value = report.summary;
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
}

/** User confirms — navigate to the download URL and emit the event. */
function confirmExport(): void {
  window.open(props.downloadUrl, "_blank", "noreferrer");
  emit("confirmed");
}

/** User cancels the export. */
function cancel(): void {
  emit("cancel");
}

onMounted(() => {
  void loadSummary();
});
</script>

<template>
  <!-- Modal overlay -->
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4">
    <div class="w-full max-w-lg rounded-[28px] border border-app-border bg-app-surface p-6 shadow-2xl shadow-black/40">
      <!-- Header -->
      <div class="mb-4">
        <h2 class="app-section-title">{{ $t("licenseReport.exportConfirmTitle") }}</h2>
        <p class="mt-1 text-sm text-app-muted">{{ $t("licenseReport.exportConfirmBody") }}</p>
      </div>

      <!-- Loading state -->
      <div v-if="loading" class="flex items-center gap-3 py-4 text-sm text-app-muted">
        <span class="inline-block h-4 w-4 animate-spin rounded-full border-2 border-app-primary border-t-transparent"></span>
        {{ $t("licenseReport.exportConfirmLoading") }}
      </div>

      <!-- Load error — allow skip -->
      <div v-else-if="loadError" class="rounded-xl border border-app-warning/30 bg-app-warning/10 px-4 py-3 text-sm text-app-text">
        {{ $t("licenseReport.exportConfirmLoadError") }}
      </div>

      <!-- Summary display -->
      <template v-else-if="summary">
        <!-- NSFW warning banner -->
        <div
          v-if="hasNsfw"
          class="mb-3 rounded-xl border border-app-warning/40 bg-app-warning/10 px-4 py-3 text-sm font-medium text-app-warning"
        >
          {{ $t("licenseReport.exportConfirmNsfwWarning") }}
        </div>
        <!-- Unknown-fields warning -->
        <div
          v-if="hasUnknown"
          class="mb-3 rounded-xl border border-app-border bg-app-surfaceAlt/80 px-4 py-3 text-sm text-app-muted"
        >
          {{ $t("licenseReport.exportConfirmUnknownWarning") }}
        </div>

        <!-- Count grid -->
        <ul class="grid gap-2 text-sm text-app-text">
          <li class="flex items-center justify-between rounded-xl border border-app-border px-3 py-2">
            <span class="text-app-muted">{{ $t("licenseReport.totalWorkers") }}</span>
            <span class="font-semibold">{{ summary.total_workers }}</span>
          </li>
          <li v-if="summary.commercial_ok" class="flex items-center justify-between rounded-xl border border-app-border px-3 py-2">
            <span class="text-app-muted">{{ $t("licenseReport.commercial.ok") }}</span>
            <span class="font-semibold text-app-success">{{ summary.commercial_ok }}</span>
          </li>
          <li v-if="summary.commercial_no" class="flex items-center justify-between rounded-xl border border-app-warning/30 bg-app-warning/5 px-3 py-2">
            <span class="text-app-warning">{{ $t("licenseReport.commercial.no") }}</span>
            <span class="font-semibold text-app-warning">{{ summary.commercial_no }}</span>
          </li>
          <!-- unknown commercial: must be shown distinctly, NOT as "no" -->
          <li v-if="summary.commercial_unknown" class="flex items-center justify-between rounded-xl border border-app-border bg-app-surfaceAlt/60 px-3 py-2">
            <span class="text-app-muted">{{ $t("licenseReport.commercial.unknown") }}</span>
            <span class="font-semibold text-app-muted">{{ summary.commercial_unknown }}</span>
          </li>
          <li v-if="summary.attribution_required" class="flex items-center justify-between rounded-xl border border-app-border px-3 py-2">
            <span class="text-app-muted">{{ $t("licenseReport.attribution.required") }}</span>
            <span class="font-semibold text-app-warning">{{ summary.attribution_required }}</span>
          </li>
          <li v-if="summary.nsfw_present" class="flex items-center justify-between rounded-xl border border-app-warning/30 bg-app-warning/5 px-3 py-2">
            <span class="text-app-warning">{{ $t("licenseReport.nsfw.present") }}</span>
            <span class="font-semibold text-app-warning">{{ summary.nsfw_present }}</span>
          </li>
        </ul>
      </template>

      <!-- Action buttons -->
      <div class="mt-6 flex flex-wrap justify-end gap-3">
        <button class="app-button-secondary" type="button" @click="cancel">
          {{ $t("licenseReport.exportConfirmCancelAction") }}
        </button>
        <!-- Skip option on load error -->
        <button
          v-if="loadError"
          class="app-button-secondary"
          type="button"
          @click="confirmExport"
        >
          {{ $t("licenseReport.exportConfirmSkipAction") }}
        </button>
        <!-- Primary confirm -->
        <button
          v-if="!loadError"
          class="app-button"
          type="button"
          :disabled="loading"
          @click="confirmExport"
        >
          {{ $t("licenseReport.exportConfirmProceedAction") }}
        </button>
      </div>
    </div>
  </div>
</template>
