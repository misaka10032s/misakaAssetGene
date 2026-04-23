import { createRouter, createWebHistory } from "vue-router";

import AssetsPage from "@/pages/Assets.vue";
import ChatPage from "@/pages/Chat.vue";
import IntegrationManagerPage from "@/pages/IntegrationManager.vue";
import ProjectsPage from "@/pages/Projects.vue";
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
      path: "/studio",
      name: PageKey.STUDIO,
      component: ChatPage,
    },
    {
      path: "/chat",
      redirect: { name: PageKey.STUDIO },
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
