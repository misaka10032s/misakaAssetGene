import { apiClient } from "@/api/client";
import type {
  TrainingRecipe,
  TrainingRecipeCreatePayload,
  TrainingRecipeSingleResponse,
  TrainingRecipeUpdatePayload,
} from "@/types/api";

import { createEntityCrud, type EntityCrudActions, type EntityCrudContext } from "./createEntityCrud";

/** TrainingRecipe CRUD actions (spec §7.1.1 entity 3/5). */
export function createTrainingRecipeActions(
  context: EntityCrudContext,
): EntityCrudActions<TrainingRecipe, TrainingRecipeCreatePayload, TrainingRecipeUpdatePayload> {
  return createEntityCrud<
    TrainingRecipe,
    TrainingRecipeCreatePayload,
    TrainingRecipeUpdatePayload,
    TrainingRecipeSingleResponse
  >(
    {
      create: apiClient.createTrainingRecipe,
      update: apiClient.updateTrainingRecipe,
      remove: apiClient.deleteTrainingRecipe,
      pick: (response) => response.training_recipe,
    },
    context,
  );
}
