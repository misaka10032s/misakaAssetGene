<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute } from "vue-router";

import { useAppStore } from "@/stores/app";

const route = useRoute();
const appStore = useAppStore();

const projectId = computed<string>(() => String(route.params.projectId ?? ""));
const versionGraph = computed(() => appStore.projectVersionGraphs[projectId.value] ?? { nodes: [], edges: [] });

const edgeMap = computed<Record<string, string[]>>(() => {
  const map: Record<string, string[]> = {};
  for (const edge of versionGraph.value.edges) {
    map[edge.target] = [...(map[edge.target] ?? []), `${edge.relation}: ${edge.source}`];
  }
  return map;
});

watch(
  projectId,
  async (nextProjectId) => {
    if (!nextProjectId) {
      return;
    }
    await appStore.loadProjectVersionGraph(nextProjectId);
  },
  { immediate: true },
);
</script>

<template>
  <section class="grid gap-5">
    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("versions.title") }}</h2>
      <p class="app-muted">{{ $t("versions.intro") }}</p>
    </div>

    <div v-if="!versionGraph.nodes.length" class="app-panel">
      <p class="app-muted">{{ $t("versions.empty") }}</p>
    </div>

    <div v-else class="grid gap-4">
      <article v-for="node in versionGraph.nodes" :key="node.id" class="app-panel border-l-4 border-l-app-primary">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p class="app-kicker">{{ node.node_type }} · {{ node.modality }}</p>
            <h3 class="text-lg font-semibold text-app-text">{{ node.title }}</h3>
            <p class="mt-1 text-sm text-app-muted">{{ node.created_at }}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <span class="app-chip">{{ node.status }}</span>
            <span v-if="node.worker" class="app-chip">{{ node.worker }}</span>
          </div>
        </div>
        <div v-if="edgeMap[node.id]?.length" class="mt-4 grid gap-2">
          <p class="text-sm font-medium text-app-text">{{ $t("versions.dependencies") }}</p>
          <ul class="grid gap-1 text-sm text-app-muted">
            <li v-for="dependency in edgeMap[node.id]" :key="dependency">
              {{ dependency }}
            </li>
          </ul>
        </div>
      </article>
    </div>
  </section>
</template>
