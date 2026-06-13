<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";

import RefStatusBadge from "@/components/RefStatusBadge.vue";
import { apiClient } from "@/api/client";
import type { CrossRefEntry, CrossRefListData, MaterializeData } from "@/types/api";
import { CrossRefStatus } from "@/types/enums";

/**
 * Displays all cross-project references for a project and provides an opt-in
 * materialization action (spec §5.6.2 / §5.6.3 / §5.6.6 / M5.8).
 */
const props = defineProps<{
  projectId: string;
}>();

const { t } = useI18n();

// ---------------------------------------------------------------------------
// Refs data state
// ---------------------------------------------------------------------------

const loading = ref(false);
const error = ref<string | null>(null);
const data = ref<CrossRefListData | null>(null);

/** Refs that are broken or outdated — require user attention. */
const urgentRefs = computed<CrossRefEntry[]>(() =>
  (data.value?.refs ?? []).filter(
    (r) => r.status === CrossRefStatus.BROKEN || r.status === CrossRefStatus.OUTDATED,
  ),
);

// ---------------------------------------------------------------------------
// Materialization state
// ---------------------------------------------------------------------------

const materializing = ref(false);
const materializeError = ref<string | null>(null);
const materializeResult = ref<MaterializeData | null>(null);
/** Controls visibility of the materialization confirm dialog. */
const showMaterializeConfirm = ref(false);

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

/** Fetches the latest ref list from the backend and refreshes the local state. */
async function loadRefs(): Promise<void> {
  if (!props.projectId) return;
  loading.value = true;
  error.value = null;
  try {
    data.value = await apiClient.listProjectRefs(props.projectId);
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t("refs.errorLoad");
  } finally {
    loading.value = false;
  }
}

// ---------------------------------------------------------------------------
// Materialization flow — explicit / opt-in only (spec §5.6.6)
// ---------------------------------------------------------------------------

function requestMaterialize(): void {
  showMaterializeConfirm.value = true;
  materializeResult.value = null;
  materializeError.value = null;
}

function cancelMaterialize(): void {
  showMaterializeConfirm.value = false;
}

/** Called when the user confirms the materialize action (never automatic). */
async function confirmMaterialize(): Promise<void> {
  showMaterializeConfirm.value = false;
  materializing.value = true;
  materializeError.value = null;
  materializeResult.value = null;
  try {
    // POST with empty body to materialize all refs.
    materializeResult.value = await apiClient.materializeProjectRefs(props.projectId, {});
    // Refresh the refs panel so statuses reflect the materialized copies.
    await loadRefs();
  } catch (err: unknown) {
    materializeError.value = err instanceof Error ? err.message : t("refs.materializeError");
  } finally {
    materializing.value = false;
  }
}

onMounted(() => {
  loadRefs();
});
</script>

<template>
  <div class="app-panel grid gap-4">
    <!-- Header row -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="app-section-title">{{ t("refs.panelTitle") }}</h3>
      <div class="flex items-center gap-2">
        <button class="app-button-secondary" :disabled="loading" @click="loadRefs">
          {{ loading ? t("refs.loading") : t("refs.refreshAction") }}
        </button>
        <button
          class="app-button-secondary"
          :disabled="materializing || loading || !data || data.refs.length === 0"
          @click="requestMaterialize"
        >
          {{ materializing ? t("refs.materializingAction") : t("refs.materializeAction") }}
        </button>
      </div>
    </div>

    <!-- Cycle warning -->
    <div
      v-if="data && data.cycle_warning.length > 0"
      class="rounded-xl border border-app-warning bg-app-warning/10 px-4 py-3 text-sm text-app-text"
    >
      <strong>{{ t("refs.cycleWarning") }}</strong>
      <ul class="mt-1 list-disc pl-5 text-app-muted">
        <li v-for="(cycle, idx) in data.cycle_warning" :key="idx">
          {{ cycle.join(" → ") }}
        </li>
      </ul>
    </div>

    <!-- Error -->
    <p v-if="error" class="text-sm text-red-300">{{ error }}</p>

    <!-- Empty state -->
    <p v-else-if="data && data.refs.length === 0" class="app-muted">
      {{ t("refs.empty") }}
    </p>

    <!-- Attention banner: broken / outdated refs -->
    <div
      v-else-if="urgentRefs.length > 0"
      class="rounded-xl border border-app-warning bg-app-warning/10 px-4 py-3 text-sm"
    >
      <strong class="text-app-text">{{ t("refs.urgentBanner", { count: urgentRefs.length }) }}</strong>
      <p class="mt-1 text-app-muted">{{ t("refs.urgentHint") }}</p>
    </div>

    <!-- Ref list -->
    <ul v-if="data && data.refs.length > 0" class="grid gap-2">
      <li
        v-for="entry in data.refs"
        :key="entry.ref"
        class="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-app-border bg-app-surfaceAlt p-3"
      >
        <div class="min-w-0 flex-1">
          <code class="break-all text-xs text-app-text">{{ entry.ref }}</code>
          <p v-if="entry.message" class="mt-1 text-xs text-app-muted">{{ entry.message }}</p>
          <p v-if="entry.path" class="mt-0.5 break-all text-xs text-app-muted">{{ entry.path }}</p>
        </div>
        <RefStatusBadge :status="entry.status" :message="entry.message" />
      </li>
    </ul>

    <!-- Materialize confirm dialog -->
    <div
      v-if="showMaterializeConfirm"
      class="rounded-xl border border-app-border bg-app-surface p-4 shadow-xl"
      role="dialog"
      :aria-label="t('refs.materializeConfirmTitle')"
    >
      <h4 class="font-semibold text-app-text">{{ t("refs.materializeConfirmTitle") }}</h4>
      <p class="mt-2 text-sm text-app-muted">{{ t("refs.materializeConfirmBody") }}</p>
      <div class="mt-4 flex gap-2">
        <button class="app-button" @click="confirmMaterialize">
          {{ t("refs.materializeConfirmAction") }}
        </button>
        <button class="app-button-secondary" @click="cancelMaterialize">
          {{ t("refs.materializeCancelAction") }}
        </button>
      </div>
    </div>

    <!-- Materialize result summary -->
    <div
      v-if="materializeResult"
      class="rounded-xl border border-app-border bg-app-surfaceAlt p-4 text-sm"
    >
      <p class="font-semibold text-app-text">
        {{ t("refs.materializeResultTitle", { count: materializeResult.materialized.length }) }}
      </p>
      <p v-if="materializeResult.broken.length > 0" class="mt-1 font-semibold text-red-300">
        {{ t("refs.materializeBrokenCount", { count: materializeResult.broken.length }) }}
      </p>
      <!-- Broken ref details -->
      <ul v-if="materializeResult.broken.length > 0" class="mt-2 grid gap-1">
        <li v-for="brokenEntry in materializeResult.broken" :key="brokenEntry.ref" class="text-xs text-app-muted">
          <code class="break-all">{{ brokenEntry.ref }}</code>
          <span v-if="brokenEntry.message"> — {{ brokenEntry.message }}</span>
        </li>
      </ul>
    </div>

    <!-- Materialize error -->
    <p v-if="materializeError" class="text-sm text-red-300">{{ materializeError }}</p>
  </div>
</template>
