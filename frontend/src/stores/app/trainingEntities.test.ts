/**
 * Unit tests for useTrainingEntitiesStore() — the §7.1.1 training-entity
 * snapshot plus the five CRUD families, which delegate to the
 * pre-existing `stores/entities/*` composables (createEntityCrud), ported
 * unchanged from the pre-split stores/app.ts.
 *
 * Per the dispatch brief, these tests target the DELEGATION CONTRACT (right
 * apiClient method, right arguments, snapshot refreshed, result plumbed
 * back, correct message key) — not the underlying entity-composable logic,
 * which is unchanged pre-existing code outside this refactor's scope.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useTrainingEntitiesStore } from "@/stores/app/trainingEntities";
import { useAppCoreStore } from "@/stores/app/core";
import type { TrainingEntitiesSnapshot } from "@/types/api";
import { MessageKey } from "@/types/enums";

function makeSnapshot(overrides: Partial<TrainingEntitiesSnapshot> = {}): TrainingEntitiesSnapshot {
  return {
    characters: [],
    dataset_packs: [],
    training_recipes: [],
    lora_presets: [],
    i2v_recipes: [],
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useTrainingEntitiesStore().loadProjectTrainingEntities", () => {
  it("stores the five-entity snapshot keyed by project id and reports success", async () => {
    const store = useTrainingEntitiesStore();
    const coreStore = useAppCoreStore();
    const snapshot = makeSnapshot({
      characters: [
        {
          id: "c1",
          project_id: "p1",
          name: "Hero",
          visual_anchors: [],
          trigger_words: [],
          forbidden_features: [],
          reference_image_refs: [],
          created_at: "",
          updated_at: "",
        },
      ],
    });
    const spy = vi.spyOn(apiClient, "trainingEntities").mockResolvedValue(snapshot);

    const result = await store.loadProjectTrainingEntities("p1");

    expect(spy).toHaveBeenCalledWith("p1");
    expect(store.projectTrainingEntities.p1).toEqual(snapshot);
    expect(result).toEqual(snapshot);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });
});

describe("useTrainingEntitiesStore().createCharacter — delegation contract", () => {
  it("calls apiClient.createCharacter with the right target/args, refreshes the snapshot, and returns the picked entity", async () => {
    const created = {
      id: "c1",
      project_id: "p1",
      name: "Hero",
      visual_anchors: ["blue eyes"],
      trigger_words: [],
      forbidden_features: [],
      reference_image_refs: [],
      created_at: "",
      updated_at: "",
    };
    // The entity CRUD composables capture `apiClient.<method>` at STORE
    // CONSTRUCTION time (createCharacterActions runs inside useTrainingEntitiesStore's
    // setup body), so the spy must be installed BEFORE the store is created —
    // spying afterward would leave the closure pointing at the real function.
    const createSpy = vi.spyOn(apiClient, "createCharacter").mockResolvedValue({ character: created });
    const refreshSpy = vi.spyOn(apiClient, "trainingEntities").mockResolvedValue(makeSnapshot({ characters: [created] }));
    const store = useTrainingEntitiesStore();
    const coreStore = useAppCoreStore();

    const result = await store.createCharacter("p1", { name: "Hero", visual_anchors: ["blue eyes"] });

    expect(createSpy).toHaveBeenCalledWith("p1", { name: "Hero", visual_anchors: ["blue eyes"] });
    expect(refreshSpy).toHaveBeenCalledWith("p1");
    expect(result).toEqual(created);
    expect(store.projectTrainingEntities.p1?.characters).toEqual([created]);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_ADD0);
  });
});

describe("useTrainingEntitiesStore().deleteLoraPreset — delegation contract", () => {
  it("calls apiClient.deleteLoraPreset with the right target/args, refreshes the snapshot, and sets SUCCESS_SWITCH0", async () => {
    const deleteSpy = vi.spyOn(apiClient, "deleteLoraPreset").mockResolvedValue(undefined);
    const refreshSpy = vi.spyOn(apiClient, "trainingEntities").mockResolvedValue(makeSnapshot());
    const store = useTrainingEntitiesStore();
    const coreStore = useAppCoreStore();

    await store.deleteLoraPreset("p1", "preset-1");

    expect(deleteSpy).toHaveBeenCalledWith("p1", "preset-1");
    expect(refreshSpy).toHaveBeenCalledWith("p1");
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_SWITCH0);
  });
});

describe("useTrainingEntitiesStore().updateTrainingRecipe — delegation contract", () => {
  it("calls apiClient.updateTrainingRecipe with the right target/args and returns the picked entity", async () => {
    const updated = {
      id: "recipe-1",
      project_id: "p1",
      base_model: "sd-xl",
      rank: 32,
      epochs: 10,
      optimizer: "adamw",
      caption_strategy: "auto",
      created_at: "",
      updated_at: "",
    };
    const updateSpy = vi.spyOn(apiClient, "updateTrainingRecipe").mockResolvedValue({ training_recipe: updated });
    vi.spyOn(apiClient, "trainingEntities").mockResolvedValue(makeSnapshot({ training_recipes: [updated] }));
    const store = useTrainingEntitiesStore();

    const result = await store.updateTrainingRecipe("p1", "recipe-1", { rank: 32 });

    expect(updateSpy).toHaveBeenCalledWith("p1", "recipe-1", { rank: 32 });
    expect(result).toEqual(updated);
  });
});
