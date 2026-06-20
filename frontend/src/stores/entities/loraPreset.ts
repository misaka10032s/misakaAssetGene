import { apiClient } from "@/api/client";
import type {
  LoraPreset,
  LoraPresetCreatePayload,
  LoraPresetSingleResponse,
  LoraPresetUpdatePayload,
} from "@/types/api";

import { createEntityCrud, type EntityCrudActions, type EntityCrudContext } from "./createEntityCrud";

/** LoraPreset CRUD actions (spec §7.1.1 entity 4/5). */
export function createLoraPresetActions(
  context: EntityCrudContext,
): EntityCrudActions<LoraPreset, LoraPresetCreatePayload, LoraPresetUpdatePayload> {
  return createEntityCrud<
    LoraPreset,
    LoraPresetCreatePayload,
    LoraPresetUpdatePayload,
    LoraPresetSingleResponse
  >(
    {
      create: apiClient.createLoraPreset,
      update: apiClient.updateLoraPreset,
      remove: apiClient.deleteLoraPreset,
      pick: (response) => response.lora_preset,
    },
    context,
  );
}
