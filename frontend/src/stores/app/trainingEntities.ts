import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import type { TrainingEntitiesSnapshot } from "@/types/api";
import { MessageKey } from "@/types/enums";
import type { EntityCrudContext } from "@/stores/entities/createEntityCrud";
import { createCharacterActions } from "@/stores/entities/character";
import { createDatasetPackActions } from "@/stores/entities/datasetPack";
import { createTrainingRecipeActions } from "@/stores/entities/trainingRecipe";
import { createLoraPresetActions } from "@/stores/entities/loraPreset";
import { createI2vRecipeActions } from "@/stores/entities/i2vRecipe";

import { useAppCoreStore } from "@/stores/app/core";

/**
 * §7.1.1 training entities (M4.c): the five-entity snapshot plus the
 * character / dataset-pack / training-recipe / LoRA-preset / i2v-recipe CRUD
 * families, delegated to the `stores/entities/*` composables exactly as
 * before. Depends on `core` (shared message-key status) only.
 */
export const useTrainingEntitiesStore = defineStore("app/trainingEntities", () => {
  const coreStore = useAppCoreStore();

  /** Per-project training entity snapshots (spec §7.1.1 / M4.c). */
  const projectTrainingEntities = ref<Record<string, TrainingEntitiesSnapshot>>({});

  /**
   * Loads (or reloads) all five training entities for a project in parallel.
   */
  async function loadProjectTrainingEntities(projectId: string): Promise<TrainingEntitiesSnapshot> {
    const snapshot = await apiClient.trainingEntities(projectId);
    projectTrainingEntities.value = {
      ...projectTrainingEntities.value,
      [projectId]: snapshot,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    return snapshot;
  }

  // Per-entity CRUD is delegated to dedicated composables under
  // stores/entities/* (spec §7.1.1). They share one context so each mutation
  // refreshes the project snapshot and sets the success message key exactly as
  // the previous inline implementation did — the store's public action names
  // and signatures below are unchanged.
  const entityCrudContext: EntityCrudContext = {
    refresh: loadProjectTrainingEntities,
    setMessageKey: (key) => {
      coreStore.lastMessageKey = key;
    },
  };
  const characterActions = createCharacterActions(entityCrudContext);
  const datasetPackActions = createDatasetPackActions(entityCrudContext);
  const trainingRecipeActions = createTrainingRecipeActions(entityCrudContext);
  const loraPresetActions = createLoraPresetActions(entityCrudContext);
  const i2vRecipeActions = createI2vRecipeActions(entityCrudContext);

  const createCharacter = characterActions.create;
  const updateCharacter = characterActions.update;
  const deleteCharacter = characterActions.remove;
  const createDatasetPack = datasetPackActions.create;
  const updateDatasetPack = datasetPackActions.update;
  const deleteDatasetPack = datasetPackActions.remove;
  const createTrainingRecipe = trainingRecipeActions.create;
  const updateTrainingRecipe = trainingRecipeActions.update;
  const deleteTrainingRecipe = trainingRecipeActions.remove;
  const createLoraPreset = loraPresetActions.create;
  const updateLoraPreset = loraPresetActions.update;
  const deleteLoraPreset = loraPresetActions.remove;
  const createI2vRecipe = i2vRecipeActions.create;
  const updateI2vRecipe = i2vRecipeActions.update;
  const deleteI2vRecipe = i2vRecipeActions.remove;

  return {
    projectTrainingEntities,
    loadProjectTrainingEntities,
    createCharacter,
    updateCharacter,
    deleteCharacter,
    createDatasetPack,
    updateDatasetPack,
    deleteDatasetPack,
    createTrainingRecipe,
    updateTrainingRecipe,
    deleteTrainingRecipe,
    createLoraPreset,
    updateLoraPreset,
    deleteLoraPreset,
    createI2vRecipe,
    updateI2vRecipe,
    deleteI2vRecipe,
  };
});
