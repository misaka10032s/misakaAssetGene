import { apiClient } from "@/api/client";
import type {
  I2vRecipeSingleResponse,
  ImageToVideoRecipe,
  ImageToVideoRecipeCreatePayload,
  ImageToVideoRecipeUpdatePayload,
} from "@/types/api";

import { createEntityCrud, type EntityCrudActions, type EntityCrudContext } from "./createEntityCrud";

/** ImageToVideoRecipe CRUD actions (spec §7.1.1 entity 5/5). */
export function createI2vRecipeActions(
  context: EntityCrudContext,
): EntityCrudActions<ImageToVideoRecipe, ImageToVideoRecipeCreatePayload, ImageToVideoRecipeUpdatePayload> {
  return createEntityCrud<
    ImageToVideoRecipe,
    ImageToVideoRecipeCreatePayload,
    ImageToVideoRecipeUpdatePayload,
    I2vRecipeSingleResponse
  >(
    {
      create: apiClient.createI2vRecipe,
      update: apiClient.updateI2vRecipe,
      remove: apiClient.deleteI2vRecipe,
      pick: (response) => response.i2v_recipe,
    },
    context,
  );
}
