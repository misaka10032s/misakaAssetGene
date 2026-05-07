import { createRouter, createWebHistory } from "vue-router";

import AssetsPage from "@/pages/Assets.vue";
import IntegrationManagerPage from "@/pages/IntegrationManager.vue";
import ProjectWorkspacePage from "@/pages/ProjectWorkspace.vue";
import ProjectsPage from "@/pages/Projects.vue";
import VersionTreePage from "@/pages/VersionTree.vue";
import { PageKey } from "@/types/enums";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      redirect: { name: PageKey.PROJECTS },
    },
    {
      path: "/projects",
      name: PageKey.PROJECTS,
      component: ProjectsPage,
    },
    {
      path: "/project/:projectId",
      name: PageKey.PROJECT,
      component: ProjectWorkspacePage,
    },
    {
      path: "/project/:projectId/versions",
      name: PageKey.VERSIONS,
      component: VersionTreePage,
    },
    {
      path: "/chat",
      redirect: { name: PageKey.PROJECTS },
    },
    {
      path: "/studio",
      redirect: { name: PageKey.PROJECTS },
    },
    {
      path: "/assets",
      name: PageKey.ASSETS,
      component: AssetsPage,
    },
    {
      path: "/settings",
      name: PageKey.SETTINGS,
      component: IntegrationManagerPage,
    },
    {
      path: "/integration",
      redirect: { name: PageKey.SETTINGS },
    },
  ],
});
