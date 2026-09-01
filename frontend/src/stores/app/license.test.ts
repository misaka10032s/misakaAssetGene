/**
 * Unit tests for useLicenseStore().loadProjectLicenseReport, ported
 * unchanged from the pre-split stores/app.ts.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useLicenseStore } from "@/stores/app/license";
import { useAppCoreStore } from "@/stores/app/core";
import type { ProjectLicenseReport } from "@/types/api";
import { MessageKey } from "@/types/enums";

function makeReport(overrides: Partial<ProjectLicenseReport> = {}): ProjectLicenseReport {
  return {
    project_id: "p1",
    project_name: "Project One",
    generated_at: new Date().toISOString(),
    entries: [],
    summary: {
      total_workers: 0,
      commercial_ok: 0,
      commercial_no: 0,
      commercial_unknown: 0,
      attribution_required: 0,
      attribution_not_required: 0,
      attribution_unknown: 0,
      nsfw_present: 0,
      nsfw_absent: 0,
      nsfw_unknown: 0,
      has_nsfw: false,
    },
    warnings: [],
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
});

describe("useLicenseStore().loadProjectLicenseReport", () => {
  it("stores the report keyed by project id, reports success, and returns it", async () => {
    const store = useLicenseStore();
    const coreStore = useAppCoreStore();
    const report = makeReport({ warnings: ["missing license for worker X"] });
    const spy = vi.spyOn(apiClient, "projectLicenseReport").mockResolvedValue(report);

    const result = await store.loadProjectLicenseReport("p1");

    expect(spy).toHaveBeenCalledWith("p1");
    expect(store.projectLicenseReports.p1).toEqual(report);
    expect(result).toEqual(report);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_FETCH0);
    expect(coreStore.errorMessageKey).toBeNull();
  });

  it("keeps per-project reports isolated", async () => {
    const store = useLicenseStore();
    vi.spyOn(apiClient, "projectLicenseReport")
      .mockResolvedValueOnce(makeReport({ project_id: "p1" }))
      .mockResolvedValueOnce(makeReport({ project_id: "p2" }));

    await store.loadProjectLicenseReport("p1");
    await store.loadProjectLicenseReport("p2");

    expect(store.projectLicenseReports.p1?.project_id).toBe("p1");
    expect(store.projectLicenseReports.p2?.project_id).toBe("p2");
  });
});
