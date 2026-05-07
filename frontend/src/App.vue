<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, RouterView, useRoute } from "vue-router";

import AssetLibraryPanel from "@/components/AssetLibraryPanel.vue";
import LocaleSwitcher from "@/components/LocaleSwitcher.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import { useDocumentTitle } from "@/composables/useDocumentTitle";
import { useAppStore } from "@/stores/app";
import type { PageNavigationItem } from "@/types/api";
import { NetworkStatus, PageKey } from "@/types/enums";

const { t } = useI18n();
const route = useRoute();
const appStore = useAppStore();

const navigationItems = computed<PageNavigationItem[]>(() => [
  { key: PageKey.PROJECTS, labelKey: "app.nav.projects" },
  { key: PageKey.SETTINGS, labelKey: "app.nav.settings" },
]);

useDocumentTitle(route, computed(() => appStore.currentProject));

const networkLabel = computed<string>(() => {
  if (appStore.networkStatus === NetworkStatus.CORE_ONLINE) {
    return t("app.status.coreOnline");
  }
  if (appStore.networkStatus === NetworkStatus.CORE_OFFLINE) {
    return t("app.status.coreOffline");
  }
  return t("app.status.bootstrapping");
});

onMounted(() => {
  void appStore.bootstrap();
});
</script>

<template>
  <div class="app-shell relative overflow-hidden">
    <div class="pointer-events-none absolute inset-0">
      <div class="absolute left-[-10rem] top-[-10rem] h-[28rem] w-[28rem] rounded-full bg-app-primary/15 blur-3xl"></div>
      <div class="absolute right-[-8rem] top-20 h-[22rem] w-[22rem] rounded-full bg-app-success/10 blur-3xl"></div>
      <div class="absolute inset-x-0 top-0 h-72 bg-gradient-to-b from-app-surfaceAlt/55 to-transparent"></div>
    </div>

    <div class="app-container px-4 py-5 md:px-6 md:py-8 xl:px-8">
      <header class="grid gap-4">
        <section class="app-panel grid gap-4 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
          <div class="min-w-0 space-y-3">
            <p class="app-kicker">{{ $t("app.subtitle") }}</p>
            <div class="flex flex-wrap items-center gap-3">
              <h1 class="app-title">{{ $t("app.title") }}</h1>
              <StatusBadge :label="networkLabel" :tone="appStore.networkTone" />
            </div>
            <p class="app-subtitle max-w-3xl">
              {{ $t("app.currentProject") }}:
              <span class="font-medium text-app-text">
                {{ appStore.currentProjectName ?? $t("app.noProjectSelected") }}
              </span>
            </p>
            <div class="grid gap-1">
              <p v-if="appStore.lastMessageKey" class="text-sm text-app-primary">
                {{ $t(appStore.lastMessageKey) }}
              </p>
              <p v-if="appStore.errorMessageKey" class="text-sm text-app-warning">
                {{ $t(appStore.errorMessageKey) }}
              </p>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-3 xl:justify-end">
            <LocaleSwitcher />
            <button
              v-if="appStore.currentProjectId"
              class="app-button-secondary"
              type="button"
              @click="appStore.setAssetDrawerOpen(true)"
            >
              {{ $t("app.nav.assets") }}
            </button>
          </div>
        </section>

        <section class="app-panel-muted flex flex-wrap items-center justify-between gap-3">
          <nav class="flex flex-wrap items-center gap-2">
            <RouterLink
              v-for="item in navigationItems"
              :key="item.key"
              class="app-tab"
              :class="{ 'app-tab-active': route.name === item.key }"
              :to="{ name: item.key }"
            >
              {{ $t(item.labelKey) }}
            </RouterLink>
            <RouterLink
              v-if="appStore.currentProjectId"
              class="app-tab"
              :class="{ 'app-tab-active': route.name === PageKey.PROJECT }"
              :to="{ name: PageKey.PROJECT, params: { projectId: appStore.currentProjectId } }"
            >
              {{ $t("app.nav.studio") }}
            </RouterLink>
            <RouterLink
              v-if="appStore.currentProjectId"
              class="app-tab"
              :class="{ 'app-tab-active': route.name === PageKey.VERSIONS }"
              :to="{ name: PageKey.VERSIONS, params: { projectId: appStore.currentProjectId } }"
            >
              {{ $t("app.nav.versions") }}
            </RouterLink>
          </nav>
          <p class="hidden text-sm text-app-muted lg:block">{{ $t("app.nextStep") }}</p>
        </section>
      </header>

      <main class="mt-5 min-w-0">
        <RouterView />
      </main>
    </div>

    <div
      v-if="appStore.assetDrawerOpen"
      class="fixed inset-0 z-50 flex justify-end bg-black/50 p-4"
      @click.self="appStore.setAssetDrawerOpen(false)"
    >
      <div class="h-full w-full max-w-xl overflow-y-auto rounded-[28px] border border-app-border bg-app-surface p-5 shadow-2xl shadow-black/40">
        <div class="mb-4 flex items-center justify-between gap-3">
          <h2 class="app-section-title">{{ $t("assets.title") }}</h2>
          <button class="app-button-secondary" type="button" @click="appStore.setAssetDrawerOpen(false)">
            {{ $t("app.close") }}
          </button>
        </div>
        <AssetLibraryPanel />
      </div>
    </div>
  </div>
</template>
