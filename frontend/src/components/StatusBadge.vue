<script setup lang="ts">
import { computed } from "vue";

import { NetworkTone } from "@/types/enums";

const props = defineProps<{
  label: string;
  tone: NetworkTone;
}>();

const badgeClass = computed<Record<NetworkTone, string>>(() => ({
  [NetworkTone.NEUTRAL]: "bg-app-surfaceAlt border-app-border text-app-text",
  [NetworkTone.SUCCESS]: "bg-app-success/20 border-app-success text-app-text",
  [NetworkTone.WARNING]: "bg-app-warning/20 border-app-warning text-app-text",
}));
</script>

<template>
  <span class="inline-flex items-center rounded-full border px-3 py-2 text-sm" :class="badgeClass[tone]">
    {{ label }}
  </span>
</template>
