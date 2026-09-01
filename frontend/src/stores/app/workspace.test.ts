/**
 * Unit tests for useWorkspaceStore() — a project's execution workspace
 * (generation jobs, assets, consultant plans), the asset-refine flow, and
 * the batch "execute ready jobs" path, ported unchanged from the pre-split
 * stores/app.ts.
 *
 * Priorities: job execution state transitions, the batch execute-ready path
 * (its response envelope was recently reshaped to `result.workspace` — spec
 * §5.14 — so this is exactly the kind of plumbing a refactor can silently
 * break), and asset import/refine result handling — each with its error
 * branch, since a rejected API call must leave the store in a sane state
 * and surface an error key rather than a partial/corrupt workspace.
 *
 * apiClient is mocked at the network boundary only.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useWorkspaceStore } from "@/stores/app/workspace";
import { useAppCoreStore } from "@/stores/app/core";
import type { AssetRecord, ConsultantPlanRecord, GenerationJob } from "@/types/api";
import { GenerationJobStatus, MessageKey, Modality } from "@/types/enums";

function makeJob(overrides: Partial<GenerationJob> = {}): GenerationJob {
  const now = new Date().toISOString();
  return {
    id: "job-1",
    project_id: "p1",
    title: "Job",
    modality: Modality.IMAGE,
    asset_type: "illustration",
    status: GenerationJobStatus.PLANNED,
    prompt: "a cat",
    summary: "",
    worker: null,
    variants: [],
    recipe: null,
    source_asset_id: null,
    mask_asset_id: null,
    blocking_reason: null,
    last_error: null,
    progress: 0,
    progress_label: null,
    search_queries: [],
    steps: [],
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

function makeAsset(overrides: Partial<AssetRecord> = {}): AssetRecord {
  return {
    id: "asset-1",
    job_id: "job-1",
    modality: Modality.IMAGE,
    asset_type: "illustration",
    title: "Asset",
    path: "/assets/1.png",
    description: "",
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function makePlan(overrides: Partial<ConsultantPlanRecord> = {}): ConsultantPlanRecord {
  return {
    id: "plan-1",
    title: "Plan",
    path: "/plans/1",
    summary: "",
    prompt: "",
    modalities: [Modality.IMAGE],
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

const workspaceSnapshot = (overrides: { jobs?: GenerationJob[]; assets?: AssetRecord[]; plans?: ConsultantPlanRecord[] } = {}) => ({
  jobs: overrides.jobs ?? [makeJob()],
  assets: overrides.assets ?? [makeAsset()],
  plans: overrides.plans ?? [makePlan()],
});

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useWorkspaceStore().loadProjectWorkspace", () => {
  it("loads jobs/assets/plans for a project and reports success", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    vi.spyOn(apiClient, "projectWorkspace").mockResolvedValue(workspaceSnapshot());

    await store.loadProjectWorkspace("p1");

    expect(store.projectJobs.p1).toHaveLength(1);
    expect(store.projectAssets.p1).toHaveLength(1);
    expect(store.projectPlans.p1).toHaveLength(1);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });
});

describe("useWorkspaceStore().executeProjectJob — job execution state transitions", () => {
  it("applies the refreshed workspace snapshot returned after execution", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    const runningJob = makeJob({ status: GenerationJobStatus.RUNNING, progress: 10 });
    vi.spyOn(apiClient, "executeProjectJob").mockResolvedValue(workspaceSnapshot({ jobs: [runningJob] }));

    await store.executeProjectJob("p1", "job-1");

    expect(store.projectJobs.p1?.[0].status).toBe(GenerationJobStatus.RUNNING);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_SWITCH0);
  });

  it("on failure, records the error key and rethrows without mutating the workspace", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    store.projectJobs = { p1: [makeJob({ status: GenerationJobStatus.PLANNED })] };
    vi.spyOn(apiClient, "executeProjectJob").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_409));

    await expect(store.executeProjectJob("p1", "job-1")).rejects.toBeInstanceOf(apiClient.ApiClientError);

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_409);
    expect(store.projectJobs.p1?.[0].status).toBe(GenerationJobStatus.PLANNED);
  });
});

describe("useWorkspaceStore().executeReadyProjectJobs — batch execute-ready path", () => {
  it("unwraps the result.workspace envelope, records the batch summary, and returns the full result", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    const executedJob = makeJob({ id: "job-1", status: GenerationJobStatus.COMPLETED });
    const batchResult = {
      workspace: workspaceSnapshot({ jobs: [executedJob] }),
      executed_count: 1,
      skipped: [{ job_id: "job-2", title: "Blocked job", reason: "missing_worker" }],
    };
    const spy = vi.spyOn(apiClient, "executeReadyProjectJobs").mockResolvedValue(batchResult);

    const result = await store.executeReadyProjectJobs("p1", ["job-1"]);

    expect(spy).toHaveBeenCalledWith("p1", ["job-1"]);
    expect(store.projectJobs.p1?.[0].status).toBe(GenerationJobStatus.COMPLETED);
    expect(store.lastBatchResult).toEqual({ executedCount: 1, skipped: batchResult.skipped });
    expect(result).toEqual(batchResult);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_SWITCH0);
  });

  it("defaults jobIds to an empty array when none are provided", async () => {
    const store = useWorkspaceStore();
    const spy = vi.spyOn(apiClient, "executeReadyProjectJobs").mockResolvedValue({
      workspace: workspaceSnapshot({ jobs: [] }),
      executed_count: 0,
      skipped: [],
    });

    await store.executeReadyProjectJobs("p1");

    expect(spy).toHaveBeenCalledWith("p1", []);
  });

  it("on failure, records the error key, rethrows, and leaves lastBatchResult untouched", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    vi.spyOn(apiClient, "executeReadyProjectJobs").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_500));

    await expect(store.executeReadyProjectJobs("p1")).rejects.toBeInstanceOf(apiClient.ApiClientError);

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_500);
    expect(store.lastBatchResult).toBeNull();
  });
});

describe("useWorkspaceStore() — asset import/refine result handling", () => {
  it("importProjectAsset applies the refreshed workspace on success", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    const newAsset = makeAsset({ id: "asset-imported", title: "Imported" });
    vi.spyOn(apiClient, "importProjectAsset").mockResolvedValue(workspaceSnapshot({ assets: [newAsset] }));

    await store.importProjectAsset("p1", {
      file: new File(["data"], "photo.png"),
      modality: "image",
      asset_type: "reference",
      title: "Imported",
    });

    expect(store.projectAssets.p1?.[0].id).toBe("asset-imported");
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_ADD0);
  });

  it("importProjectAsset on failure records the error key and rethrows", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    vi.spyOn(apiClient, "importProjectAsset").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_400));

    await expect(
      store.importProjectAsset("p1", {
        file: new File(["data"], "bad.png"),
        modality: "image",
        asset_type: "reference",
        title: "Bad",
      }),
    ).rejects.toBeInstanceOf(apiClient.ApiClientError);

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_400);
  });

  it("refineAsset applies the refreshed workspace on success", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    const refined = makeAsset({ id: "asset-refined" });
    vi.spyOn(apiClient, "refineAsset").mockResolvedValue(workspaceSnapshot({ assets: [refined] }));

    await store.refineAsset("p1", "asset-1", { instruction: "make it brighter" });

    expect(store.projectAssets.p1?.[0].id).toBe("asset-refined");
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_ADD0);
  });

  it("refineAsset on failure records the error key and rethrows", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    vi.spyOn(apiClient, "refineAsset").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_404));

    await expect(store.refineAsset("p1", "asset-1", { instruction: "x" })).rejects.toBeInstanceOf(
      apiClient.ApiClientError,
    );

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_404);
  });
});

describe("useWorkspaceStore().updateProjectJob", () => {
  it("applies the refreshed workspace on success", async () => {
    const store = useWorkspaceStore();
    const patchedJob = makeJob({ worker: "kohya-ss" });
    vi.spyOn(apiClient, "updateProjectJob").mockResolvedValue(workspaceSnapshot({ jobs: [patchedJob] }));

    await store.updateProjectJob("p1", "job-1", {
      worker: "kohya-ss",
      recipe: null,
      source_asset_id: null,
      mask_asset_id: null,
    });

    expect(store.projectJobs.p1?.[0].worker).toBe("kohya-ss");
  });

  it("on failure records the error key and rethrows", async () => {
    const store = useWorkspaceStore();
    const coreStore = useAppCoreStore();
    vi.spyOn(apiClient, "updateProjectJob").mockRejectedValue(new apiClient.ApiClientError(MessageKey.FAIL_400));

    await expect(
      store.updateProjectJob("p1", "job-1", { worker: null, recipe: null, source_asset_id: null, mask_asset_id: null }),
    ).rejects.toBeInstanceOf(apiClient.ApiClientError);

    expect(coreStore.errorMessageKey).toBe(MessageKey.FAIL_400);
  });
});

describe("useWorkspaceStore().setAssetDrawerOpen", () => {
  it("toggles the asset drawer UI flag", () => {
    const store = useWorkspaceStore();
    expect(store.assetDrawerOpen).toBe(false);

    store.setAssetDrawerOpen(true);
    expect(store.assetDrawerOpen).toBe(true);

    store.setAssetDrawerOpen(false);
    expect(store.assetDrawerOpen).toBe(false);
  });
});
