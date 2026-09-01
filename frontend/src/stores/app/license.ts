import { ref } from "vue";
import { defineStore } from "pinia";

import { apiClient } from "@/api/client";
import type { ProjectLicenseReport } from "@/types/api";
import { MessageKey } from "@/types/enums";

import { useAppCoreStore } from "@/stores/app/core";

/** Per-project license report. Depends on `core` (shared message-key status) only. */
export const useLicenseStore = defineStore("app/license", () => {
  const coreStore = useAppCoreStore();

  const projectLicenseReports = ref<Record<string, ProjectLicenseReport>>({});

  async function loadProjectLicenseReport(projectId: string): Promise<ProjectLicenseReport> {
    const response = await apiClient.projectLicenseReport(projectId);
    projectLicenseReports.value = {
      ...projectLicenseReports.value,
      [projectId]: response,
    };
    coreStore.lastMessageKey = MessageKey.SUCCESS_FETCH0;
    coreStore.errorMessageKey = null;
    return response;
  }

  return {
    projectLicenseReports,
    loadProjectLicenseReport,
  };
});
