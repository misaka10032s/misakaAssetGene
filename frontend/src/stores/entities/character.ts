import { apiClient } from "@/api/client";
import type {
  CharacterSheet,
  CharacterSheetCreatePayload,
  CharacterSheetUpdatePayload,
  CharacterSingleResponse,
} from "@/types/api";

import { createEntityCrud, type EntityCrudActions, type EntityCrudContext } from "./createEntityCrud";

/** CharacterSheet CRUD actions (spec §7.1.1 entity 1/5). */
export function createCharacterActions(
  context: EntityCrudContext,
): EntityCrudActions<CharacterSheet, CharacterSheetCreatePayload, CharacterSheetUpdatePayload> {
  return createEntityCrud<
    CharacterSheet,
    CharacterSheetCreatePayload,
    CharacterSheetUpdatePayload,
    CharacterSingleResponse
  >(
    {
      create: apiClient.createCharacter,
      update: apiClient.updateCharacter,
      remove: apiClient.deleteCharacter,
      pick: (response) => response.character,
    },
    context,
  );
}
