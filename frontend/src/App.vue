<script setup lang="ts">
import { computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { RouterLink, RouterView, useRoute } from "vue-router";

import { useAppStore } from "@/stores/app";
import LocaleSwitcher from "@/components/LocaleSwitcher.vue";
import StatusBadge from "@/components/StatusBadge.vue";
import { useWindowSize } from "@/composables/useWindowSize";
import type { PageNavigationItem } from "@/types/api";
import { NetworkStatus, PageKey } from "@/types/enums";

const { t } = useI18n();
const route = useRoute();

const appStore = useAppStore();
const { isMobile } = useWindowSize();
const navigationItems = computed<PageNavigationItem[]>(() => [
  { key: PageKey.PROJECTS, labelKey: "app.nav.projects" },
  { key: PageKey.STUDIO, labelKey: "app.nav.studio" },
  { key: PageKey.ASSETS, labelKey: "app.nav.assets" },
  { key: PageKey.SETTINGS, labelKey: "app.nav.settings" },
]);
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
  <div class="app-shell">
    <div class="app-container px-4 py-6 md:px-6">
      <header class="mb-6 flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div class="flex flex-col gap-2">
          <h1 class="app-title">{{ $t("app.title") }}</h1>
          <p class="app-subtitle">{{ $t("app.subtitle") }}</p>
          <p class="app-muted">
            {{ $t("app.currentProject") }}:
            <span class="text-app-text">
              {{ appStore.currentProjectName ?? $t("app.noProjectSelected") }}
            </span>
          </p>
          <p v-if="appStore.lastMessageKey" class="app-muted text-app-primary">
            {{ $t(appStore.lastMessageKey) }}
          </p>
          <p v-if="appStore.errorMessageKey" class="app-muted text-app-warning">
            {{ $t(appStore.errorMessageKey) }}
          </p>
        </div>
        <div class="flex flex-col items-start gap-3 md:items-end">
          <LocaleSwitcher />
          <StatusBadge :label="networkLabel" :tone="appStore.networkTone" />
        </div>
      </header>

      <div class="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)]">
        <nav class="app-panel flex h-fit flex-col gap-2">
          <RouterLink
            v-for="item in navigationItems"
            :key="item.key"
            class="app-tab"
            :class="{ 'border-app-primary': route.name === item.key }"
            :to="{ name: item.key }"
          >
            {{ $t(item.labelKey) }}
          </RouterLink>
        </nav>

        <main class="app-panel min-w-0">
          <RouterView />
        </main>
      </div>
    </div>
    <div v-if="isMobile" class="pb-4 text-center text-xs text-app-muted">
      {{ $t("app.nextStep") }}
    </div>
  </div>
</template>
