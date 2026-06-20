import { apiClient } from "@/api/client";
import type {
  DatasetPack,
  DatasetPackCreatePayload,
  DatasetPackSingleResponse,
  DatasetPackUpdatePayload,
} from "@/types/api";

import { createEntityCrud, type EntityCrudActions, type EntityCrudContext } from "./createEntityCrud";

/** DatasetPack CRUD actions (spec §7.1.1 entity 2/5). */
export function createDatasetPackActions(
  context: EntityCrudContext,
): EntityCrudActions<DatasetPack, DatasetPackCreatePayload, DatasetPackUpdatePayload> {
  return createEntityCrud<
    DatasetPack,
    DatasetPackCreatePayload,
    DatasetPackUpdatePayload,
    DatasetPackSingleResponse
  >(
    {
      create: apiClient.createDatasetPack,
      update: apiClient.updateDatasetPack,
      remove: apiClient.deleteDatasetPack,
      pick: (response) => response.dataset_pack,
    },
    context,
  );
}
