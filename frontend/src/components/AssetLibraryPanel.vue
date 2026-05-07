<script setup lang="ts">
import { computed } from "vue";

import { useAppStore } from "@/stores/app";

const appStore = useAppStore();
const currentAssets = computed(() =>
  appStore.currentProjectId ? appStore.projectAssets[appStore.currentProjectId] ?? [] : [],
);
</script>

<template>
  <section class="grid gap-3">
    <div>
      <h2 class="app-section-title">{{ $t("assets.title") }}</h2>
      <p class="app-muted">{{ $t("assets.intro") }}</p>
    </div>
    <p class="text-sm text-app-text">{{ $t("assets.explain") }}</p>
    <ul v-if="currentAssets.length" class="grid gap-3">
      <li v-for="asset in currentAssets" :key="asset.id" class="rounded-xl border border-app-border bg-app-surfaceAlt p-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <strong class="text-app-text">{{ asset.title }}</strong>
          <span class="app-muted">{{ asset.asset_type }}</span>
        </div>
        <p class="mt-2 break-all app-muted">{{ asset.path }}</p>
        <p class="mt-2 text-sm text-app-text">{{ asset.description }}</p>
      </li>
    </ul>
    <ul class="grid gap-2 text-sm text-app-text">
      <li>{{ $t("assets.items.accepted") }}</li>
      <li>{{ $t("assets.items.dependency") }}</li>
      <li>{{ $t("assets.items.bundle") }}</li>
    </ul>
  </section>
</template>
