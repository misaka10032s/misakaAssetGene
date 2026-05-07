import { computed, watch } from "vue";
import type { RouteLocationNormalizedLoaded } from "vue-router";

import type { ProjectSummary } from "@/types/api";
import { PageKey } from "@/types/enums";

/**
 * Keeps the browser title aligned with the active route and selected project.
 *
 * @param route - The current route instance.
 * @param currentProject - The selected project summary.
 */
export function useDocumentTitle(route: RouteLocationNormalizedLoaded, currentProject: Readonly<{ value: ProjectSummary | null }>) {
  const title = computed<string>(() => {
    if (route.name === PageKey.PROJECT) {
      const routeProjectId = String(route.params.projectId ?? "").trim();
      const projectLabel = currentProject.value?.name ?? routeProjectId;
      return projectLabel ? `${projectLabel} · MisakaAssetGene` : "Project · MisakaAssetGene";
    }
    if (route.name === PageKey.SETTINGS) {
      return "Settings · MisakaAssetGene";
    }
    if (route.name === PageKey.ASSETS) {
      return "Assets · MisakaAssetGene";
    }
    return "Projects · MisakaAssetGene";
  });

  watch(
    title,
    (nextTitle) => {
      document.title = nextTitle;
    },
    { immediate: true },
  );

  return {
    title,
  };
}
