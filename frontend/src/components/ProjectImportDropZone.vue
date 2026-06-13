<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";

import { apiClient } from "@/api/client";
import type { ProjectImportData } from "@/types/api";

/**
 * Drag-and-drop zone for importing a *.misaka.zip project archive (spec §5.5 / M5.8).
 * Security constraints:
 *  - Only .zip files are accepted (checked before upload).
 *  - The backend independently validates the SHA-256 manifest, zip-slip, and manifest schema.
 *  - Error states (hash mismatch, invalid archive) are surfaced honestly — never silently ignored.
 *  - The import is NEVER automatic; the user must explicitly drop the file.
 */

const emit = defineEmits<{
  /** Fired after a successful import so the parent can refresh the project list. */
  (event: "imported", result: ProjectImportData): void;
}>();

const { t } = useI18n();

const isDragOver = ref(false);
const uploading = ref(false);
const uploadError = ref<string | null>(null);
const importResult = ref<ProjectImportData | null>(null);

// ---------------------------------------------------------------------------
// Drag event handlers
// ---------------------------------------------------------------------------

function onDragEnter(e: DragEvent): void {
  e.preventDefault();
  isDragOver.value = true;
}

function onDragOver(e: DragEvent): void {
  e.preventDefault();
  isDragOver.value = true;
}

function onDragLeave(e: DragEvent): void {
  e.preventDefault();
  isDragOver.value = false;
}

function onDrop(e: DragEvent): void {
  e.preventDefault();
  isDragOver.value = false;
  const files = e.dataTransfer?.files;
  if (files && files.length > 0) {
    handleFile(files[0]);
  }
}

// ---------------------------------------------------------------------------
// File input fallback (click to browse)
// ---------------------------------------------------------------------------

const fileInputRef = ref<HTMLInputElement | null>(null);

function triggerFilePicker(): void {
  fileInputRef.value?.click();
}

function onFileSelected(e: Event): void {
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  if (file) {
    handleFile(file);
  }
  // Reset so the same file can be re-selected after an error.
  target.value = "";
}

// ---------------------------------------------------------------------------
// Upload logic
// ---------------------------------------------------------------------------

/**
 * Validates the file extension client-side, then uploads to the backend.
 * Security: .zip extension is checked before sending; the backend performs
 * all trust-worthy checks (SHA-256 manifest, zip-slip, schema).
 * Any validation failure from the backend is surfaced verbatim to the user.
 */
async function handleFile(file: File): Promise<void> {
  uploadError.value = null;
  importResult.value = null;

  // Client-side extension guard — fast reject for obviously wrong file types.
  if (!file.name.toLowerCase().endsWith(".zip")) {
    uploadError.value = t("projectImport.errorNotZip");
    return;
  }

  uploading.value = true;
  try {
    const result = await apiClient.importProject(file);
    importResult.value = result;
    emit("imported", result);
  } catch (err: unknown) {
    // Surface the backend error honestly — hash mismatch, zip-slip, invalid manifest, etc.
    uploadError.value = err instanceof Error ? err.message : t("projectImport.errorGeneric");
  } finally {
    uploading.value = false;
  }
}

function resetResult(): void {
  importResult.value = null;
  uploadError.value = null;
}
</script>

<template>
  <div class="grid gap-3">
    <!-- Drop zone -->
    <div
      class="flex min-h-32 cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-8 text-center transition"
      :class="
        isDragOver
          ? 'border-app-primary bg-app-primary/10 text-app-text'
          : 'border-app-border bg-app-surfaceAlt text-app-muted hover:border-app-primary hover:bg-app-primary/5'
      "
      role="button"
      :aria-label="t('projectImport.dropZoneLabel')"
      tabindex="0"
      @click="triggerFilePicker"
      @keydown.enter="triggerFilePicker"
      @keydown.space.prevent="triggerFilePicker"
      @dragenter="onDragEnter"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <span class="text-2xl" aria-hidden="true">⬇</span>
      <span v-if="uploading" class="text-sm font-semibold text-app-primary">
        {{ t("projectImport.uploading") }}
      </span>
      <span v-else class="text-sm font-medium">
        {{ t("projectImport.dropHint") }}
      </span>
      <span class="text-xs text-app-muted">{{ t("projectImport.acceptedFormat") }}</span>
    </div>

    <!-- Hidden file input -->
    <input
      ref="fileInputRef"
      type="file"
      accept=".zip"
      class="hidden"
      :aria-hidden="true"
      tabindex="-1"
      @change="onFileSelected"
    />

    <!-- Upload error — surface backend message honestly (security constraint) -->
    <div
      v-if="uploadError"
      class="rounded-xl border border-app-danger/50 bg-app-danger/15 px-4 py-3 text-sm"
    >
      <p class="font-semibold text-app-danger">{{ t("projectImport.errorTitle") }}</p>
      <p class="mt-1 break-words text-app-muted">{{ uploadError }}</p>
      <button class="mt-2 text-xs text-app-primary underline" @click="resetResult">
        {{ t("projectImport.retryAction") }}
      </button>
    </div>

    <!-- Success result -->
    <div
      v-if="importResult"
      class="rounded-xl border border-app-success/50 bg-app-success/15 px-4 py-3 text-sm"
    >
      <p class="font-semibold text-app-text">{{ t("projectImport.successTitle") }}</p>
      <p class="mt-1 text-app-muted">
        {{ t("projectImport.successProjectName", { name: importResult.project_name }) }}
      </p>
      <p v-if="importResult.collision_resolved" class="mt-1 text-app-muted">
        {{ t("projectImport.collisionResolved", { originalId: importResult.origin_id ?? "?" }) }}
      </p>
      <button class="mt-2 text-xs text-app-primary underline" @click="resetResult">
        {{ t("projectImport.importAnotherAction") }}
      </button>
    </div>
  </div>
</template>
