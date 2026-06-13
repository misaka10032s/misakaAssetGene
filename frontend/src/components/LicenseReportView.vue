<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";

import type { LicenseReportEntry, LicenseReportSummary, ProjectLicenseReport } from "@/types/api";

const { t } = useI18n();

const props = defineProps<{
  report: ProjectLicenseReport;
}>();

/** Formats a datetime string for display. */
function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}

/** Formats a list of strings with a separator, returning "-" when empty. */
function formatList(items: string[], separator = " / "): string {
  return items.length ? items.join(separator) : "-";
}

/**
 * Returns i18n key text for a tri-state boolean field.
 * null -> "unknown" (MUST be visually distinct from false; spec §2 legal-risk clarity).
 */
function triStateLabel(value: boolean | null): string {
  if (value === true) {
    return t("licenseReport.triState.yes");
  }
  if (value === false) {
    return t("licenseReport.triState.no");
  }
  return t("licenseReport.triState.unknown");
}

/**
 * Returns UnoCSS token classes for a tri-state boolean field.
 * unknown is styled with warning tone — it is NOT the same as "no".
 */
function triStateClass(value: boolean | null, positiveIsGood: boolean): string {
  if (value === null) {
    // Unknown: always warning — never confuse with a definitive no.
    return "text-app-warning";
  }
  if (value === true) {
    return positiveIsGood ? "text-app-success" : "text-app-warning";
  }
  // false
  return positiveIsGood ? "text-app-warning" : "text-app-success";
}

const summary = computed<LicenseReportSummary>(() => props.report.summary);

/** True when any NSFW-present workers exist. */
const hasNsfw = computed<boolean>(() => summary.value.has_nsfw);

/** True when there are any unknown fields requiring manual review. */
const hasUnknown = computed<boolean>(
  () =>
    summary.value.commercial_unknown > 0 ||
    summary.value.attribution_unknown > 0 ||
    summary.value.nsfw_unknown > 0,
);
</script>

<template>
  <!-- Summary banner section -->
  <div class="grid gap-3">
    <!-- NSFW banner: only shown when confirmed NSFW present -->
    <div
      v-if="hasNsfw"
      class="rounded-xl border border-app-warning/40 bg-app-warning/10 px-4 py-3 text-sm font-medium text-app-warning"
    >
      {{ $t("licenseReport.nsfwBanner") }}
    </div>
    <!-- Unknown-fields banner -->
    <div
      v-if="hasUnknown"
      class="rounded-xl border border-app-border bg-app-surfaceAlt/80 px-4 py-3 text-sm text-app-muted"
    >
      {{ $t("licenseReport.unknownBanner") }}
    </div>
  </div>

  <!-- Project-level summary counts -->
  <section class="grid gap-3">
    <h4 class="app-section-title">{{ $t("licenseReport.summaryTitle") }}</h4>
    <div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      <div class="app-panel-muted">
        <p class="app-kicker">{{ $t("licenseReport.totalWorkers") }}</p>
        <p class="mt-2 text-base font-semibold text-app-text">{{ summary.total_workers }}</p>
      </div>
      <!-- Commercial counts -->
      <div class="app-panel-muted">
        <p class="app-kicker">{{ $t("licenseReport.fields.commercial") }}</p>
        <div class="mt-2 grid gap-1 text-sm">
          <p class="text-app-success">{{ $t("licenseReport.commercial.ok") }}: {{ summary.commercial_ok }}</p>
          <p class="text-app-warning">{{ $t("licenseReport.commercial.no") }}: {{ summary.commercial_no }}</p>
          <p class="text-app-warning">{{ $t("licenseReport.commercial.unknown") }}: {{ summary.commercial_unknown }}</p>
        </div>
      </div>
      <!-- Attribution counts -->
      <div class="app-panel-muted">
        <p class="app-kicker">{{ $t("licenseReport.fields.attribution") }}</p>
        <div class="mt-2 grid gap-1 text-sm">
          <p class="text-app-warning">{{ $t("licenseReport.attribution.required") }}: {{ summary.attribution_required }}</p>
          <p class="text-app-success">{{ $t("licenseReport.attribution.notRequired") }}: {{ summary.attribution_not_required }}</p>
          <p class="text-app-muted">{{ $t("licenseReport.attribution.unknown") }}: {{ summary.attribution_unknown }}</p>
        </div>
      </div>
      <!-- NSFW counts -->
      <div class="app-panel-muted">
        <p class="app-kicker">{{ $t("licenseReport.fields.nsfw") }}</p>
        <div class="mt-2 grid gap-1 text-sm">
          <p :class="summary.nsfw_present > 0 ? 'text-app-warning' : 'text-app-muted'">
            {{ $t("licenseReport.nsfw.present") }}: {{ summary.nsfw_present }}
          </p>
          <p class="text-app-success">{{ $t("licenseReport.nsfw.absent") }}: {{ summary.nsfw_absent }}</p>
          <p class="text-app-muted">{{ $t("licenseReport.nsfw.unknown") }}: {{ summary.nsfw_unknown }}</p>
        </div>
      </div>
    </div>
  </section>

  <!-- Per-entry table -->
  <div class="grid gap-3 xl:grid-cols-2">
    <article
      v-for="entry in report.entries"
      :key="entry.worker_name"
      class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4"
    >
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <h4 class="font-semibold text-app-text">{{ entry.display_name }}</h4>
          <p class="break-all text-sm text-app-muted">{{ entry.repo }}</p>
        </div>
        <span class="app-chip flex-shrink-0">{{ entry.license ?? $t("project.licenseUnknown") }}</span>
      </div>

      <!-- Tri-state fields row: commercial / attribution / NSFW -->
      <div class="mt-3 flex flex-wrap gap-3">
        <!-- Commercial: positive = true is good; style unknown as warning -->
        <div class="flex items-center gap-1.5 text-sm" :class="triStateClass(entry.commercial, true)">
          <span class="app-kicker">{{ $t("licenseReport.fields.commercial") }}</span>
          <span class="font-semibold">{{ triStateLabel(entry.commercial) }}</span>
        </div>
        <!-- Attribution required: positive = true is BAD (requires work); unknown = warning -->
        <div class="flex items-center gap-1.5 text-sm" :class="triStateClass(entry.attribution, false)">
          <span class="app-kicker">{{ $t("licenseReport.fields.attribution") }}</span>
          <span class="font-semibold">{{ triStateLabel(entry.attribution) }}</span>
        </div>
        <!-- NSFW: positive = true is bad -->
        <div class="flex items-center gap-1.5 text-sm" :class="triStateClass(entry.nsfw, false)">
          <span class="app-kicker">{{ $t("licenseReport.fields.nsfw") }}</span>
          <span class="font-semibold">{{ triStateLabel(entry.nsfw) }}</span>
        </div>
      </div>

      <!-- Attribution note when present -->
      <p v-if="entry.attribution_note" class="mt-2 text-xs leading-5 text-app-muted">
        {{ entry.attribution_note }}
      </p>

      <!-- Usage stats -->
      <div class="mt-3 grid gap-1 text-sm text-app-text">
        <p>{{ $t("licenseReport.fields.jobs") }}: {{ entry.job_count }}</p>
        <p>{{ $t("licenseReport.fields.assets") }}: {{ entry.asset_count }}</p>
        <p>{{ $t("licenseReport.fields.modalities") }}: {{ formatList(entry.modalities) }}</p>
        <p v-if="entry.readiness_note" class="text-app-warning">
          {{ $t("licenseReport.fields.readiness") }}: {{ entry.readiness_note }}
        </p>
      </div>
    </article>
  </div>

  <!-- Warnings list -->
  <div v-if="report.warnings.length" class="grid gap-2">
    <div
      v-for="warning in report.warnings"
      :key="warning"
      class="rounded-xl border border-app-warning/30 bg-app-warning/10 px-3 py-2 text-sm text-app-text"
    >
      {{ warning }}
    </div>
  </div>
</template>
