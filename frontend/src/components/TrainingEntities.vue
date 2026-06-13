<script setup lang="ts">
/**
 * TrainingEntities — CRUD panel for all five §7.1.1 training entities.
 *
 * Renders list + create/edit/delete for:
 *   character_sheet, dataset_pack, training_recipe, lora_preset, i2v_recipe
 *
 * Suggestion cards (spec §4.4 / §5.12.1):
 *   - Shown when ConsultantAnalysis.is_training_flow is true.
 *   - Each card shows reason + prefilled preview.
 *   - "existing_id" is resolved client-side: if the entity list already
 *     contains a match we show "already exists / use existing" instead of
 *     a duplicate create button.
 *   - The system NEVER auto-creates — only on explicit user click.
 */
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import { useWindowSize } from "@/composables/useWindowSize";
import { useAppStore } from "@/stores/app";
import type {
  CharacterSheet,
  CharacterSheetCreatePayload,
  CharacterSheetUpdatePayload,
  ConsultantAnalysis,
  DatasetPack,
  DatasetPackCreatePayload,
  DatasetPackUpdatePayload,
  ImageToVideoRecipe,
  ImageToVideoRecipeCreatePayload,
  ImageToVideoRecipeUpdatePayload,
  LoraLayer,
  LoraPreset,
  LoraPresetCreatePayload,
  LoraPresetUpdatePayload,
  TrainingEntitiesSnapshot,
  TrainingRecipe,
  TrainingRecipeCreatePayload,
  TrainingRecipeUpdatePayload,
  TrainingSuggestionCard,
} from "@/types/api";
import { TrainingEntityKind } from "@/types/enums";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  /** The project whose entities are managed. */
  projectId: string;
  /**
   * Latest ConsultantAnalysis — may carry suggestion_cards when the
   * consultant detects a training intent (spec §5.12.1).
   */
  analysis: ConsultantAnalysis | null;
}>();

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

const { t } = useI18n();
const appStore = useAppStore();
const { splitGridClass, isMobile } = useWindowSize();

type TabKey = "characters" | "dataset_packs" | "training_recipes" | "lora_presets" | "i2v_recipes";

const activeTab = ref<TabKey>("characters");
const loading = ref(false);

const snapshot = computed<TrainingEntitiesSnapshot | null>(
  () => appStore.projectTrainingEntities[props.projectId] ?? null,
);

const suggestionCards = computed<TrainingSuggestionCard[]>(
  () => (props.analysis?.is_training_flow ? (props.analysis.suggestion_cards ?? []) : []),
);

// ---------------------------------------------------------------------------
// Load on mount / project change
// ---------------------------------------------------------------------------

watch(
  () => props.projectId,
  async (nextId) => {
    if (!nextId) return;
    loading.value = true;
    try {
      await appStore.loadProjectTrainingEntities(nextId);
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);

// ---------------------------------------------------------------------------
// Suggestion-card client-side existing_id resolution (spec §4.4)
//
// The backend may return existing_id=null even when a matching entity is
// already in the project (deferred M4.b item).  We resolve here:
//  - For character_sheet: match by prefilled.name
//  - For dataset_pack:    match by prefilled.source
//  - For training_recipe: match by prefilled.base_model
//  - For lora_preset:     match by prefilled.name
//  - For i2v_recipe:      match by prefilled.name
// If no client-side match is found we fall back to the backend-supplied
// existing_id (which may be null).
// ---------------------------------------------------------------------------

/**
 * Returns the resolved existing entity id for a suggestion card.
 * Combines backend existing_id with client-side list matching.
 *
 * Known M4.c limitation — weak key matching:
 *   Each entity kind is matched on a single lowercased string field (e.g. `name`,
 *   `source`, `base_model`). This means:
 *   - Duplicate names → false positive (reports "already exists" for the wrong entity).
 *   - Rename after creation → false negative (fails to detect the entity, shows create button).
 *   The backend `existing_id` field is preferred when present; client-side matching is a
 *   best-effort fallback for cases where the backend returns null.
 */
function resolveExistingId(card: TrainingSuggestionCard): string | null {
  if (card.existing_id) return card.existing_id;
  if (!snapshot.value) return null;

  switch (card.entity_kind) {
    case TrainingEntityKind.CHARACTER_SHEET: {
      const needle = String(card.prefilled["name"] ?? "").toLowerCase();
      if (!needle) return null;
      return snapshot.value.characters.find((c) => c.name.toLowerCase() === needle)?.id ?? null;
    }
    case TrainingEntityKind.DATASET_PACK: {
      const needle = String(card.prefilled["source"] ?? "").toLowerCase();
      if (!needle) return null;
      return snapshot.value.dataset_packs.find((d) => d.source.toLowerCase() === needle)?.id ?? null;
    }
    case TrainingEntityKind.TRAINING_RECIPE: {
      const needle = String(card.prefilled["base_model"] ?? "").toLowerCase();
      if (!needle) return null;
      return snapshot.value.training_recipes.find((r) => r.base_model.toLowerCase() === needle)?.id ?? null;
    }
    case TrainingEntityKind.LORA_PRESET: {
      const needle = String(card.prefilled["name"] ?? "").toLowerCase();
      if (!needle) return null;
      return snapshot.value.lora_presets.find((p) => p.name.toLowerCase() === needle)?.id ?? null;
    }
    case TrainingEntityKind.I2V_RECIPE: {
      const needle = String(card.prefilled["name"] ?? "").toLowerCase();
      if (!needle) return null;
      return snapshot.value.i2v_recipes.find((r) => r.name.toLowerCase() === needle)?.id ?? null;
    }
    default:
      return null;
  }
}

/**
 * Returns a human-readable label for an entity kind.
 */
function entityKindLabel(kind: string): string {
  const key = `training.suggestionCardEntityKind.${kind}`;
  return t(key) ?? kind;
}

// ---------------------------------------------------------------------------
// Create-from-suggestion-card handler (spec §4.4 — user-explicit only)
// ---------------------------------------------------------------------------

const cardCreating = ref<Record<number, boolean>>({});

async function createFromCard(card: TrainingSuggestionCard, index: number): Promise<void> {
  if (cardCreating.value[index]) return;
  cardCreating.value = { ...cardCreating.value, [index]: true };
  try {
    switch (card.entity_kind) {
      case TrainingEntityKind.CHARACTER_SHEET:
        await appStore.createCharacter(props.projectId, {
          name: String(card.prefilled["name"] ?? ""),
          visual_anchors: (card.prefilled["visual_anchors"] as string[] | undefined) ?? [],
          trigger_words: (card.prefilled["trigger_words"] as string[] | undefined) ?? [],
          forbidden_features: (card.prefilled["forbidden_features"] as string[] | undefined) ?? [],
          reference_image_refs: (card.prefilled["reference_image_refs"] as string[] | undefined) ?? [],
        });
        break;
      case TrainingEntityKind.DATASET_PACK:
        await appStore.createDatasetPack(props.projectId, {
          source: String(card.prefilled["source"] ?? ""),
          cleaning_status: String(card.prefilled["cleaning_status"] ?? "raw"),
          tags: (card.prefilled["tags"] as string[] | undefined) ?? [],
          license: String(card.prefilled["license"] ?? ""),
          split_strategy: String(card.prefilled["split_strategy"] ?? ""),
        });
        break;
      case TrainingEntityKind.TRAINING_RECIPE:
        await appStore.createTrainingRecipe(props.projectId, {
          base_model: String(card.prefilled["base_model"] ?? ""),
          rank: Number(card.prefilled["rank"] ?? 16),
          epochs: Number(card.prefilled["epochs"] ?? 10),
          optimizer: String(card.prefilled["optimizer"] ?? "AdamW8bit"),
          caption_strategy: String(card.prefilled["caption_strategy"] ?? "wd14"),
        });
        break;
      case TrainingEntityKind.LORA_PRESET:
        await appStore.createLoraPreset(props.projectId, {
          name: String(card.prefilled["name"] ?? ""),
          layers: (card.prefilled["layers"] as LoraLayer[] | undefined) ?? [],
        });
        break;
      case TrainingEntityKind.I2V_RECIPE:
        await appStore.createI2vRecipe(props.projectId, {
          name: String(card.prefilled["name"] ?? ""),
          workflow_kind: String(card.prefilled["workflow_kind"] ?? "animatediff"),
          frames: Number(card.prefilled["frames"] ?? 16),
          fps: Number(card.prefilled["fps"] ?? 8),
          motion_strength: Number(card.prefilled["motion_strength"] ?? 1.0),
          notes: String(card.prefilled["notes"] ?? ""),
        });
        break;
    }
    activeTab.value = entityKindToTab(card.entity_kind);
  } finally {
    cardCreating.value = { ...cardCreating.value, [index]: false };
  }
}

function entityKindToTab(kind: string): TabKey {
  const map: Record<string, TabKey> = {
    [TrainingEntityKind.CHARACTER_SHEET]: "characters",
    [TrainingEntityKind.DATASET_PACK]: "dataset_packs",
    [TrainingEntityKind.TRAINING_RECIPE]: "training_recipes",
    [TrainingEntityKind.LORA_PRESET]: "lora_presets",
    [TrainingEntityKind.I2V_RECIPE]: "i2v_recipes",
  };
  return map[kind] ?? "characters";
}

// ---------------------------------------------------------------------------
// Character sheet CRUD
// ---------------------------------------------------------------------------

const showCharacterForm = ref(false);
const editingCharacterId = ref<string | null>(null);
const characterForm = ref<CharacterSheetCreatePayload & { visual_anchors_raw: string; trigger_words_raw: string; forbidden_features_raw: string; reference_image_refs_raw: string }>({
  name: "",
  visual_anchors: [],
  trigger_words: [],
  forbidden_features: [],
  reference_image_refs: [],
  visual_anchors_raw: "",
  trigger_words_raw: "",
  forbidden_features_raw: "",
  reference_image_refs_raw: "",
});

function parseLines(raw: string): string[] {
  return raw.split("\n").map((line) => line.trim()).filter(Boolean);
}

function openCreateCharacter(): void {
  editingCharacterId.value = null;
  characterForm.value = {
    name: "",
    visual_anchors: [],
    trigger_words: [],
    forbidden_features: [],
    reference_image_refs: [],
    visual_anchors_raw: "",
    trigger_words_raw: "",
    forbidden_features_raw: "",
    reference_image_refs_raw: "",
  };
  showCharacterForm.value = true;
}

function openEditCharacter(sheet: CharacterSheet): void {
  editingCharacterId.value = sheet.id;
  characterForm.value = {
    name: sheet.name,
    visual_anchors: sheet.visual_anchors,
    trigger_words: sheet.trigger_words,
    forbidden_features: sheet.forbidden_features,
    reference_image_refs: sheet.reference_image_refs,
    visual_anchors_raw: sheet.visual_anchors.join("\n"),
    trigger_words_raw: sheet.trigger_words.join("\n"),
    forbidden_features_raw: sheet.forbidden_features.join("\n"),
    reference_image_refs_raw: sheet.reference_image_refs.join("\n"),
  };
  showCharacterForm.value = true;
}

function cancelCharacterForm(): void {
  showCharacterForm.value = false;
  editingCharacterId.value = null;
}

async function submitCharacterForm(): Promise<void> {
  if (!characterForm.value.name.trim()) return;
  const payload: CharacterSheetCreatePayload = {
    name: characterForm.value.name.trim(),
    visual_anchors: parseLines(characterForm.value.visual_anchors_raw),
    trigger_words: parseLines(characterForm.value.trigger_words_raw),
    forbidden_features: parseLines(characterForm.value.forbidden_features_raw),
    reference_image_refs: parseLines(characterForm.value.reference_image_refs_raw),
  };
  if (editingCharacterId.value) {
    await appStore.updateCharacter(props.projectId, editingCharacterId.value, payload as CharacterSheetUpdatePayload);
  } else {
    await appStore.createCharacter(props.projectId, payload);
  }
  cancelCharacterForm();
}

async function handleDeleteCharacter(id: string): Promise<void> {
  if (!window.confirm(t("training.deleteConfirm"))) return;
  await appStore.deleteCharacter(props.projectId, id);
}

// ---------------------------------------------------------------------------
// DatasetPack CRUD
// ---------------------------------------------------------------------------

const showDatasetPackForm = ref(false);
const editingDatasetPackId = ref<string | null>(null);
const datasetPackForm = ref<DatasetPackCreatePayload & { tags_raw: string; members_raw: string }>({
  source: "",
  cleaning_status: "raw",
  tags: [],
  license: "",
  split_strategy: "",
  members: [],
  tags_raw: "",
  members_raw: "",
});

function openCreateDatasetPack(): void {
  editingDatasetPackId.value = null;
  datasetPackForm.value = { source: "", cleaning_status: "raw", tags: [], license: "", split_strategy: "", members: [], tags_raw: "", members_raw: "" };
  showDatasetPackForm.value = true;
}

function openEditDatasetPack(pack: DatasetPack): void {
  editingDatasetPackId.value = pack.id;
  datasetPackForm.value = {
    source: pack.source,
    cleaning_status: pack.cleaning_status,
    tags: pack.tags,
    license: pack.license,
    split_strategy: pack.split_strategy,
    members: pack.members,
    tags_raw: pack.tags.join("\n"),
    members_raw: pack.members.join("\n"),
  };
  showDatasetPackForm.value = true;
}

function cancelDatasetPackForm(): void {
  showDatasetPackForm.value = false;
  editingDatasetPackId.value = null;
}

async function submitDatasetPackForm(): Promise<void> {
  if (!datasetPackForm.value.source.trim()) return;
  const payload: DatasetPackCreatePayload = {
    source: datasetPackForm.value.source.trim(),
    cleaning_status: datasetPackForm.value.cleaning_status,
    tags: parseLines(datasetPackForm.value.tags_raw),
    license: datasetPackForm.value.license,
    split_strategy: datasetPackForm.value.split_strategy,
    members: parseLines(datasetPackForm.value.members_raw),
  };
  if (editingDatasetPackId.value) {
    await appStore.updateDatasetPack(props.projectId, editingDatasetPackId.value, payload as DatasetPackUpdatePayload);
  } else {
    await appStore.createDatasetPack(props.projectId, payload);
  }
  cancelDatasetPackForm();
}

async function handleDeleteDatasetPack(id: string): Promise<void> {
  if (!window.confirm(t("training.deleteConfirm"))) return;
  await appStore.deleteDatasetPack(props.projectId, id);
}

// ---------------------------------------------------------------------------
// TrainingRecipe CRUD
// ---------------------------------------------------------------------------

const showTrainingRecipeForm = ref(false);
const editingTrainingRecipeId = ref<string | null>(null);
const trainingRecipeForm = ref<TrainingRecipeCreatePayload>({
  base_model: "",
  rank: 16,
  epochs: 10,
  optimizer: "AdamW8bit",
  caption_strategy: "wd14",
});

function openCreateTrainingRecipe(): void {
  editingTrainingRecipeId.value = null;
  trainingRecipeForm.value = { base_model: "", rank: 16, epochs: 10, optimizer: "AdamW8bit", caption_strategy: "wd14" };
  showTrainingRecipeForm.value = true;
}

function openEditTrainingRecipe(recipe: TrainingRecipe): void {
  editingTrainingRecipeId.value = recipe.id;
  trainingRecipeForm.value = {
    base_model: recipe.base_model,
    rank: recipe.rank,
    epochs: recipe.epochs,
    optimizer: recipe.optimizer,
    caption_strategy: recipe.caption_strategy,
  };
  showTrainingRecipeForm.value = true;
}

function cancelTrainingRecipeForm(): void {
  showTrainingRecipeForm.value = false;
  editingTrainingRecipeId.value = null;
}

async function submitTrainingRecipeForm(): Promise<void> {
  if (!trainingRecipeForm.value.base_model.trim()) return;
  if (editingTrainingRecipeId.value) {
    await appStore.updateTrainingRecipe(props.projectId, editingTrainingRecipeId.value, trainingRecipeForm.value as TrainingRecipeUpdatePayload);
  } else {
    await appStore.createTrainingRecipe(props.projectId, trainingRecipeForm.value);
  }
  cancelTrainingRecipeForm();
}

async function handleDeleteTrainingRecipe(id: string): Promise<void> {
  if (!window.confirm(t("training.deleteConfirm"))) return;
  await appStore.deleteTrainingRecipe(props.projectId, id);
}

// ---------------------------------------------------------------------------
// LoraPreset CRUD
// ---------------------------------------------------------------------------

const showLoraPresetForm = ref(false);
const editingLoraPresetId = ref<string | null>(null);
const loraPresetForm = ref<LoraPresetCreatePayload>({ name: "", layers: [] });
const loraLayerDraft = ref<LoraLayer>({ kind: "character", lora_ref: "", weight: 1.0 });

function openCreateLoraPreset(): void {
  editingLoraPresetId.value = null;
  loraPresetForm.value = { name: "", layers: [] };
  loraLayerDraft.value = { kind: "character", lora_ref: "", weight: 1.0 };
  showLoraPresetForm.value = true;
}

function openEditLoraPreset(preset: LoraPreset): void {
  editingLoraPresetId.value = preset.id;
  loraPresetForm.value = { name: preset.name, layers: preset.layers.map((l) => ({ ...l })) };
  loraLayerDraft.value = { kind: "character", lora_ref: "", weight: 1.0 };
  showLoraPresetForm.value = true;
}

function cancelLoraPresetForm(): void {
  showLoraPresetForm.value = false;
  editingLoraPresetId.value = null;
}

function addLoraLayer(): void {
  if (!loraLayerDraft.value.lora_ref.trim()) return;
  loraPresetForm.value.layers = [...(loraPresetForm.value.layers ?? []), { ...loraLayerDraft.value }];
  loraLayerDraft.value = { kind: "character", lora_ref: "", weight: 1.0 };
}

function removeLoraLayer(index: number): void {
  loraPresetForm.value.layers = (loraPresetForm.value.layers ?? []).filter((_, i) => i !== index);
}

async function submitLoraPresetForm(): Promise<void> {
  if (!loraPresetForm.value.name.trim()) return;
  if (editingLoraPresetId.value) {
    await appStore.updateLoraPreset(props.projectId, editingLoraPresetId.value, loraPresetForm.value as LoraPresetUpdatePayload);
  } else {
    await appStore.createLoraPreset(props.projectId, loraPresetForm.value);
  }
  cancelLoraPresetForm();
}

async function handleDeleteLoraPreset(id: string): Promise<void> {
  if (!window.confirm(t("training.deleteConfirm"))) return;
  await appStore.deleteLoraPreset(props.projectId, id);
}

// ---------------------------------------------------------------------------
// ImageToVideoRecipe CRUD
// ---------------------------------------------------------------------------

const showI2vRecipeForm = ref(false);
const editingI2vRecipeId = ref<string | null>(null);
const i2vRecipeForm = ref<ImageToVideoRecipeCreatePayload>({
  name: "",
  workflow_kind: "animatediff",
  frames: 16,
  fps: 8,
  motion_strength: 1.0,
  notes: "",
});

function openCreateI2vRecipe(): void {
  editingI2vRecipeId.value = null;
  i2vRecipeForm.value = { name: "", workflow_kind: "animatediff", frames: 16, fps: 8, motion_strength: 1.0, notes: "" };
  showI2vRecipeForm.value = true;
}

function openEditI2vRecipe(recipe: ImageToVideoRecipe): void {
  editingI2vRecipeId.value = recipe.id;
  i2vRecipeForm.value = {
    name: recipe.name,
    workflow_kind: recipe.workflow_kind,
    frames: recipe.frames,
    fps: recipe.fps,
    motion_strength: recipe.motion_strength,
    notes: recipe.notes,
  };
  showI2vRecipeForm.value = true;
}

function cancelI2vRecipeForm(): void {
  showI2vRecipeForm.value = false;
  editingI2vRecipeId.value = null;
}

async function submitI2vRecipeForm(): Promise<void> {
  if (!i2vRecipeForm.value.name.trim()) return;
  if (editingI2vRecipeId.value) {
    await appStore.updateI2vRecipe(props.projectId, editingI2vRecipeId.value, i2vRecipeForm.value as ImageToVideoRecipeUpdatePayload);
  } else {
    await appStore.createI2vRecipe(props.projectId, i2vRecipeForm.value);
  }
  cancelI2vRecipeForm();
}

async function handleDeleteI2vRecipe(id: string): Promise<void> {
  if (!window.confirm(t("training.deleteConfirm"))) return;
  await appStore.deleteI2vRecipe(props.projectId, id);
}

// ---------------------------------------------------------------------------
// Date formatting
// ---------------------------------------------------------------------------

/**
 * Formats an ISO datetime string into a locale-aware representation.
 */
function formatDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}
</script>

<template>
  <section class="grid gap-5">
    <!-- Suggestion cards (spec §4.4 / §5.12.1): rendered only in training flows -->
    <section v-if="suggestionCards.length" class="app-panel grid gap-4">
      <h2 class="app-section-title">{{ $t("training.suggestionCardsTitle") }}</h2>
      <ul class="grid gap-3" :class="splitGridClass">
        <li
          v-for="(card, cardIndex) in suggestionCards"
          :key="`card-${cardIndex}`"
          class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class="app-chip">{{ entityKindLabel(card.entity_kind) }}</span>
          </div>
          <p class="mt-3 text-sm leading-7 text-app-text">
            <span class="font-semibold text-app-text">{{ $t("training.suggestionCardReason") }}: </span>
            {{ card.reason }}
          </p>
          <div v-if="Object.keys(card.prefilled).length" class="mt-3">
            <p class="app-kicker">{{ $t("training.suggestionCardPrefilled") }}</p>
            <dl class="mt-2 grid gap-1 text-sm text-app-muted">
              <div
                v-for="[key, val] in Object.entries(card.prefilled)"
                :key="key"
                class="flex flex-wrap gap-1"
              >
                <dt class="font-medium text-app-text">{{ key }}:</dt>
                <dd class="break-all">{{ Array.isArray(val) ? val.join(", ") : String(val) }}</dd>
              </div>
            </dl>
          </div>
          <!-- Resolve existing_id client-side (spec §4.4 deferred M4.b item) -->
          <div class="mt-4 flex justify-end">
            <span
              v-if="resolveExistingId(card)"
              class="rounded-xl border border-app-border px-3 py-2 text-sm text-app-muted"
            >
              {{ $t("training.suggestionCardUseExisting") }} — {{ resolveExistingId(card) }}
            </span>
            <button
              v-else
              class="app-button-secondary"
              type="button"
              :disabled="cardCreating[cardIndex]"
              @click="createFromCard(card, cardIndex)"
            >
              {{ $t("training.suggestionCardCreate") }}
            </button>
          </div>
        </li>
      </ul>
    </section>

    <!-- Entity management panel -->
    <section class="app-panel grid gap-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <h2 class="app-section-title">{{ $t("training.entitiesTitle") }}</h2>
      </div>
      <p class="app-muted">{{ $t("training.entitiesIntro") }}</p>

      <!-- Tab bar -->
      <div class="flex flex-wrap gap-2 border-b border-app-border pb-2">
        <button
          v-for="tab in ([
            { key: 'characters', label: $t('training.charactersTab') },
            { key: 'dataset_packs', label: $t('training.datasetPacksTab') },
            { key: 'training_recipes', label: $t('training.trainingRecipesTab') },
            { key: 'lora_presets', label: $t('training.loraPresetsTab') },
            { key: 'i2v_recipes', label: $t('training.i2vRecipesTab') },
          ] as const)"
          :key="tab.key"
          type="button"
          class="rounded-xl px-3 py-1.5 text-sm transition-colors"
          :class="activeTab === tab.key
            ? 'bg-app-primary/12 font-semibold text-app-text'
            : 'text-app-muted hover:text-app-text'"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <p v-if="loading" class="app-muted">{{ $t("training.loadingEntities") }}</p>

      <template v-else-if="snapshot">
        <!-- ─── Character sheets ─── -->
        <div v-if="activeTab === 'characters'" class="grid gap-4">
          <div class="flex justify-end">
            <button class="app-button-secondary" type="button" @click="openCreateCharacter">
              {{ $t("training.createCharacter") }}
            </button>
          </div>

          <!-- Create/edit form -->
          <div v-if="showCharacterForm" class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4">
            <div class="grid gap-3">
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldName") }}</span>
                <input v-model="characterForm.name" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldVisualAnchors") }}</span>
                <textarea v-model="characterForm.visual_anchors_raw" class="app-input min-h-16 resize-y" :placeholder="$t('training.listPlaceholder')" rows="3"></textarea>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldTriggerWords") }}</span>
                <textarea v-model="characterForm.trigger_words_raw" class="app-input min-h-16 resize-y" :placeholder="$t('training.listPlaceholder')" rows="3"></textarea>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldForbiddenFeatures") }}</span>
                <textarea v-model="characterForm.forbidden_features_raw" class="app-input min-h-16 resize-y" :placeholder="$t('training.listPlaceholder')" rows="3"></textarea>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldReferenceImageRefs") }}</span>
                <textarea v-model="characterForm.reference_image_refs_raw" class="app-input min-h-16 resize-y" :placeholder="$t('training.listPlaceholder')" rows="3"></textarea>
              </label>
              <div class="flex justify-end gap-2">
                <button class="app-button-secondary" type="button" @click="cancelCharacterForm">{{ $t("training.cancelAction") }}</button>
                <button class="app-button" type="button" :disabled="!characterForm.name.trim()" @click="submitCharacterForm">{{ $t("training.saveAction") }}</button>
              </div>
            </div>
          </div>

          <p v-if="!snapshot.characters.length && !showCharacterForm" class="app-muted">{{ $t("training.emptyCharacters") }}</p>
          <ul v-else class="grid gap-3">
            <li v-for="sheet in snapshot.characters" :key="sheet.id" class="app-panel-muted">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <strong class="text-sm text-app-text">{{ sheet.name }}</strong>
                <div class="flex gap-2">
                  <button class="app-button-secondary" type="button" @click="openEditCharacter(sheet)">{{ $t("training.editAction") }}</button>
                  <button class="app-button-secondary" type="button" @click="handleDeleteCharacter(sheet.id)">{{ $t("training.deleteAction") }}</button>
                </div>
              </div>
              <div v-if="sheet.trigger_words.length" class="mt-2 flex flex-wrap gap-1">
                <span v-for="word in sheet.trigger_words" :key="word" class="app-chip">{{ word }}</span>
              </div>
              <p class="mt-2 text-xs text-app-muted">{{ $t("training.updatedAt") }}: {{ formatDateTime(sheet.updated_at) }}</p>
            </li>
          </ul>
        </div>

        <!-- ─── Dataset packs ─── -->
        <div v-if="activeTab === 'dataset_packs'" class="grid gap-4">
          <div class="flex justify-end">
            <button class="app-button-secondary" type="button" @click="openCreateDatasetPack">
              {{ $t("training.createDatasetPack") }}
            </button>
          </div>

          <div v-if="showDatasetPackForm" class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4">
            <div class="grid gap-3">
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldSource") }}</span>
                <input v-model="datasetPackForm.source" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldCleaningStatus") }}</span>
                <select v-model="datasetPackForm.cleaning_status" class="app-input">
                  <option value="raw">{{ $t("training.cleaningStatusRaw") }}</option>
                  <option value="cleaned">{{ $t("training.cleaningStatusCleaned") }}</option>
                  <option value="tagged">{{ $t("training.cleaningStatusTagged") }}</option>
                </select>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldTags") }}</span>
                <textarea v-model="datasetPackForm.tags_raw" class="app-input min-h-16 resize-y" :placeholder="$t('training.listPlaceholder')" rows="2"></textarea>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldLicense") }}</span>
                <input v-model="datasetPackForm.license" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldSplitStrategy") }}</span>
                <input v-model="datasetPackForm.split_strategy" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldMembers") }}</span>
                <textarea v-model="datasetPackForm.members_raw" class="app-input min-h-16 resize-y" :placeholder="$t('training.listPlaceholder')" rows="3"></textarea>
              </label>
              <div class="flex justify-end gap-2">
                <button class="app-button-secondary" type="button" @click="cancelDatasetPackForm">{{ $t("training.cancelAction") }}</button>
                <button class="app-button" type="button" :disabled="!datasetPackForm.source.trim()" @click="submitDatasetPackForm">{{ $t("training.saveAction") }}</button>
              </div>
            </div>
          </div>

          <p v-if="!snapshot.dataset_packs.length && !showDatasetPackForm" class="app-muted">{{ $t("training.emptyDatasetPacks") }}</p>
          <ul v-else class="grid gap-3">
            <li v-for="pack in snapshot.dataset_packs" :key="pack.id" class="app-panel-muted">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <strong class="text-sm text-app-text">{{ pack.source }}</strong>
                <div class="flex gap-2">
                  <span class="app-chip">{{ pack.cleaning_status }}</span>
                  <button class="app-button-secondary" type="button" @click="openEditDatasetPack(pack)">{{ $t("training.editAction") }}</button>
                  <button class="app-button-secondary" type="button" @click="handleDeleteDatasetPack(pack.id)">{{ $t("training.deleteAction") }}</button>
                </div>
              </div>
              <div v-if="pack.tags.length" class="mt-2 flex flex-wrap gap-1">
                <span v-for="tag in pack.tags" :key="tag" class="app-chip">{{ tag }}</span>
              </div>
              <p v-if="pack.license" class="mt-2 text-xs text-app-muted">{{ $t("training.fieldLicense") }}: {{ pack.license }}</p>
              <p class="mt-2 text-xs text-app-muted">{{ $t("training.updatedAt") }}: {{ formatDateTime(pack.updated_at) }}</p>
            </li>
          </ul>
        </div>

        <!-- ─── Training recipes ─── -->
        <div v-if="activeTab === 'training_recipes'" class="grid gap-4">
          <div class="flex justify-end">
            <button class="app-button-secondary" type="button" @click="openCreateTrainingRecipe">
              {{ $t("training.createTrainingRecipe") }}
            </button>
          </div>

          <div v-if="showTrainingRecipeForm" class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4">
            <div class="grid gap-3" :class="splitGridClass">
              <label class="grid gap-1 text-sm text-app-text col-span-2">
                <span class="app-muted">{{ $t("training.fieldBaseModel") }}</span>
                <input v-model="trainingRecipeForm.base_model" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldRank") }}</span>
                <input v-model.number="trainingRecipeForm.rank" type="number" min="1" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldEpochs") }}</span>
                <input v-model.number="trainingRecipeForm.epochs" type="number" min="1" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldOptimizer") }}</span>
                <input v-model="trainingRecipeForm.optimizer" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldCaptionStrategy") }}</span>
                <input v-model="trainingRecipeForm.caption_strategy" class="app-input" />
              </label>
              <div class="flex justify-end gap-2 col-span-2">
                <button class="app-button-secondary" type="button" @click="cancelTrainingRecipeForm">{{ $t("training.cancelAction") }}</button>
                <button class="app-button" type="button" :disabled="!trainingRecipeForm.base_model.trim()" @click="submitTrainingRecipeForm">{{ $t("training.saveAction") }}</button>
              </div>
            </div>
          </div>

          <p v-if="!snapshot.training_recipes.length && !showTrainingRecipeForm" class="app-muted">{{ $t("training.emptyTrainingRecipes") }}</p>
          <ul v-else class="grid gap-3">
            <li v-for="recipe in snapshot.training_recipes" :key="recipe.id" class="app-panel-muted">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <strong class="text-sm text-app-text">{{ recipe.base_model }}</strong>
                <div class="flex gap-2">
                  <button class="app-button-secondary" type="button" @click="openEditTrainingRecipe(recipe)">{{ $t("training.editAction") }}</button>
                  <button class="app-button-secondary" type="button" @click="handleDeleteTrainingRecipe(recipe.id)">{{ $t("training.deleteAction") }}</button>
                </div>
              </div>
              <p class="mt-2 text-sm text-app-text">
                rank={{ recipe.rank }} · epochs={{ recipe.epochs }} · {{ recipe.optimizer }} · {{ recipe.caption_strategy }}
              </p>
              <p class="mt-1 text-xs text-app-muted">{{ $t("training.updatedAt") }}: {{ formatDateTime(recipe.updated_at) }}</p>
            </li>
          </ul>
        </div>

        <!-- ─── LoRA presets ─── -->
        <div v-if="activeTab === 'lora_presets'" class="grid gap-4">
          <div class="flex justify-end">
            <button class="app-button-secondary" type="button" @click="openCreateLoraPreset">
              {{ $t("training.createLoraPreset") }}
            </button>
          </div>

          <div v-if="showLoraPresetForm" class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4">
            <div class="grid gap-3">
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldName") }}</span>
                <input v-model="loraPresetForm.name" class="app-input" />
              </label>
              <!-- Layer builder -->
              <div class="grid gap-2">
                <p class="app-kicker">{{ $t("training.fieldLayers") }}</p>
                <ul v-if="loraPresetForm.layers?.length" class="grid gap-2">
                  <li
                    v-for="(layer, layerIndex) in loraPresetForm.layers"
                    :key="layerIndex"
                    class="flex flex-wrap items-center gap-2 rounded-xl border border-app-border px-3 py-2 text-sm text-app-text"
                  >
                    <span class="app-chip">{{ layer.kind }}</span>
                    <span class="flex-1 break-all">{{ layer.lora_ref }}</span>
                    <span class="text-app-muted">w={{ layer.weight }}</span>
                    <button class="text-app-warning text-xs" type="button" @click="removeLoraLayer(layerIndex)">✕</button>
                  </li>
                </ul>
                <!-- Add layer inline form -->
                <div class="grid gap-2" :class="isMobile ? 'grid-cols-1' : 'grid-cols-[auto_1fr_auto_auto] items-end'">
                  <label class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted sr-only">kind</span>
                    <select v-model="loraLayerDraft.kind" class="app-input">
                      <option value="character">{{ $t("training.loraKindCharacter") }}</option>
                      <option value="costume">{{ $t("training.loraKindCostume") }}</option>
                      <option value="style">{{ $t("training.loraKindStyle") }}</option>
                    </select>
                  </label>
                  <label class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted sr-only">lora_ref</span>
                    <input v-model="loraLayerDraft.lora_ref" class="app-input" placeholder="path/model.safetensors" />
                  </label>
                  <label class="grid gap-1 text-sm text-app-text">
                    <span class="app-muted sr-only">weight</span>
                    <input v-model.number="loraLayerDraft.weight" type="number" step="0.1" min="0" max="2" class="app-input w-24" />
                  </label>
                  <button class="app-button-secondary" type="button" :disabled="!loraLayerDraft.lora_ref.trim()" @click="addLoraLayer">+</button>
                </div>
              </div>
              <div class="flex justify-end gap-2">
                <button class="app-button-secondary" type="button" @click="cancelLoraPresetForm">{{ $t("training.cancelAction") }}</button>
                <button class="app-button" type="button" :disabled="!loraPresetForm.name.trim()" @click="submitLoraPresetForm">{{ $t("training.saveAction") }}</button>
              </div>
            </div>
          </div>

          <p v-if="!snapshot.lora_presets.length && !showLoraPresetForm" class="app-muted">{{ $t("training.emptyLoraPresets") }}</p>
          <ul v-else class="grid gap-3">
            <li v-for="preset in snapshot.lora_presets" :key="preset.id" class="app-panel-muted">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <strong class="text-sm text-app-text">{{ preset.name }}</strong>
                <div class="flex gap-2">
                  <button class="app-button-secondary" type="button" @click="openEditLoraPreset(preset)">{{ $t("training.editAction") }}</button>
                  <button class="app-button-secondary" type="button" @click="handleDeleteLoraPreset(preset.id)">{{ $t("training.deleteAction") }}</button>
                </div>
              </div>
              <ul v-if="preset.layers.length" class="mt-2 grid gap-1">
                <li v-for="(layer, li) in preset.layers" :key="li" class="flex flex-wrap items-center gap-2 text-sm text-app-text">
                  <span class="app-chip">{{ layer.kind }}</span>
                  <span class="break-all">{{ layer.lora_ref }}</span>
                  <span class="text-app-muted">w={{ layer.weight }}</span>
                </li>
              </ul>
              <p class="mt-2 text-xs text-app-muted">{{ $t("training.updatedAt") }}: {{ formatDateTime(preset.updated_at) }}</p>
            </li>
          </ul>
        </div>

        <!-- ─── I2V recipes ─── -->
        <div v-if="activeTab === 'i2v_recipes'" class="grid gap-4">
          <div class="flex justify-end">
            <button class="app-button-secondary" type="button" @click="openCreateI2vRecipe">
              {{ $t("training.createI2vRecipe") }}
            </button>
          </div>

          <div v-if="showI2vRecipeForm" class="rounded-2xl border border-app-border bg-app-surfaceAlt p-4">
            <div class="grid gap-3" :class="splitGridClass">
              <label class="grid gap-1 text-sm text-app-text col-span-2">
                <span class="app-muted">{{ $t("training.fieldName") }}</span>
                <input v-model="i2vRecipeForm.name" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldWorkflowKind") }}</span>
                <select v-model="i2vRecipeForm.workflow_kind" class="app-input">
                  <option value="animatediff">{{ $t("training.workflowKindAnimatediff") }}</option>
                  <option value="svd">{{ $t("training.workflowKindSvd") }}</option>
                  <option value="image-to-video">{{ $t("training.workflowKindI2v") }}</option>
                </select>
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldFrames") }}</span>
                <input v-model.number="i2vRecipeForm.frames" type="number" min="1" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldFps") }}</span>
                <input v-model.number="i2vRecipeForm.fps" type="number" min="1" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text">
                <span class="app-muted">{{ $t("training.fieldMotionStrength") }}</span>
                <input v-model.number="i2vRecipeForm.motion_strength" type="number" step="0.1" min="0" class="app-input" />
              </label>
              <label class="grid gap-1 text-sm text-app-text col-span-2">
                <span class="app-muted">{{ $t("training.fieldNotes") }}</span>
                <textarea v-model="i2vRecipeForm.notes" class="app-input min-h-16 resize-y" rows="2"></textarea>
              </label>
              <div class="flex justify-end gap-2 col-span-2">
                <button class="app-button-secondary" type="button" @click="cancelI2vRecipeForm">{{ $t("training.cancelAction") }}</button>
                <button class="app-button" type="button" :disabled="!i2vRecipeForm.name.trim()" @click="submitI2vRecipeForm">{{ $t("training.saveAction") }}</button>
              </div>
            </div>
          </div>

          <p v-if="!snapshot.i2v_recipes.length && !showI2vRecipeForm" class="app-muted">{{ $t("training.emptyI2vRecipes") }}</p>
          <ul v-else class="grid gap-3">
            <li v-for="recipe in snapshot.i2v_recipes" :key="recipe.id" class="app-panel-muted">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <strong class="text-sm text-app-text">{{ recipe.name }}</strong>
                <div class="flex gap-2">
                  <span class="app-chip">{{ recipe.workflow_kind }}</span>
                  <button class="app-button-secondary" type="button" @click="openEditI2vRecipe(recipe)">{{ $t("training.editAction") }}</button>
                  <button class="app-button-secondary" type="button" @click="handleDeleteI2vRecipe(recipe.id)">{{ $t("training.deleteAction") }}</button>
                </div>
              </div>
              <p class="mt-2 text-sm text-app-text">
                {{ recipe.frames }}f · {{ recipe.fps }}fps · motion={{ recipe.motion_strength }}
              </p>
              <p v-if="recipe.notes" class="mt-1 text-sm leading-7 text-app-muted">{{ recipe.notes }}</p>
              <p class="mt-2 text-xs text-app-muted">{{ $t("training.updatedAt") }}: {{ formatDateTime(recipe.updated_at) }}</p>
            </li>
          </ul>
        </div>
      </template>
    </section>
  </section>
</template>
