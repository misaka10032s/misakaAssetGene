import { MessageKey } from "@/types/enums";

/**
 * Shared dependencies the per-entity CRUD factories need from the app store.
 *
 * Keeping these as injected callbacks (rather than importing the store)
 * avoids a circular dependency and lets each entity module stay a pure,
 * independently testable composable.
 */
export interface EntityCrudContext {
  /** Reloads the five-entity training snapshot for a project (spec §7.1.1). */
  refresh: (projectId: string) => Promise<unknown>;
  /** Records the last success message key shown to the user. */
  setMessageKey: (key: MessageKey) => void;
}

/** The three apiClient calls that back one training entity's CRUD. */
export interface EntityApi<TEntity, TCreatePayload, TUpdatePayload, TSingleResponse> {
  create: (projectId: string, payload: TCreatePayload) => Promise<TSingleResponse>;
  update: (projectId: string, id: string, payload: TUpdatePayload) => Promise<TSingleResponse>;
  remove: (projectId: string, id: string) => Promise<void>;
  /** Picks the entity out of the single-item API response envelope. */
  pick: (response: TSingleResponse) => TEntity;
}

/** The uniform set of CRUD actions every training entity exposes on the store. */
export interface EntityCrudActions<TEntity, TCreatePayload, TUpdatePayload> {
  create: (projectId: string, payload: TCreatePayload) => Promise<TEntity>;
  update: (projectId: string, id: string, payload: TUpdatePayload) => Promise<TEntity>;
  remove: (projectId: string, id: string) => Promise<void>;
}

/**
 * Builds the create/update/delete actions for one training entity.
 *
 * Each mutation refreshes the project's training-entity snapshot and sets the
 * matching success message key — preserving the exact behaviour the app store
 * had inline before the per-entity split (no external API change).
 */
export function createEntityCrud<TEntity, TCreatePayload, TUpdatePayload, TSingleResponse>(
  api: EntityApi<TEntity, TCreatePayload, TUpdatePayload, TSingleResponse>,
  context: EntityCrudContext,
): EntityCrudActions<TEntity, TCreatePayload, TUpdatePayload> {
  return {
    async create(projectId: string, payload: TCreatePayload): Promise<TEntity> {
      const response = await api.create(projectId, payload);
      await context.refresh(projectId);
      context.setMessageKey(MessageKey.SUCCESS_ADD0);
      return api.pick(response);
    },
    async update(projectId: string, id: string, payload: TUpdatePayload): Promise<TEntity> {
      const response = await api.update(projectId, id, payload);
      await context.refresh(projectId);
      context.setMessageKey(MessageKey.SUCCESS_SWITCH0);
      return api.pick(response);
    },
    async remove(projectId: string, id: string): Promise<void> {
      await api.remove(projectId, id);
      await context.refresh(projectId);
      context.setMessageKey(MessageKey.SUCCESS_SWITCH0);
    },
  };
}
