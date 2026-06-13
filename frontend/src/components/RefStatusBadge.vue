<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";

import { CrossRefStatus } from "@/types/enums";

/**
 * Renders a cross-project reference status badge (spec §5.6.3 / M5.8).
 * broken and outdated are visually prominent because they require user action.
 */
const props = defineProps<{
  status: CrossRefStatus;
  /** Optional short message to display as a tooltip / title attribute. */
  message?: string;
}>();

const { t } = useI18n();

/** Maps each RefStatus to a UnoCSS class string for the badge background/border. */
const badgeClass = computed<string>(() => {
  switch (props.status) {
    case CrossRefStatus.LIVE:
      // Subdued success — live refs are OK, no urgency.
      return "bg-app-success/15 border-app-success/40 text-app-text";
    case CrossRefStatus.EXTERNAL:
      // Neutral info — served from local copy, no immediate action needed.
      return "bg-app-primary/15 border-app-primary/40 text-app-text";
    case CrossRefStatus.OUTDATED:
      // Warning — source has changed; user may want to refresh.
      return "bg-app-warning/20 border-app-warning text-app-text font-semibold";
    case CrossRefStatus.BROKEN:
      // Error / danger — file is unavailable; blocks generation.
      return "bg-red-500/20 border-red-400 text-red-200 font-semibold";
    default:
      return "bg-app-surfaceAlt border-app-border text-app-muted";
  }
});

/** Icon prefix for each status; broken/outdated carry a distinct warning glyph. */
const statusIcon = computed<string>(() => {
  switch (props.status) {
    case CrossRefStatus.LIVE:
      return "✓";
    case CrossRefStatus.EXTERNAL:
      return "⊙";
    case CrossRefStatus.OUTDATED:
      return "⚠";
    case CrossRefStatus.BROKEN:
      return "✗";
    default:
      return "?";
  }
});

const labelKey = computed<string>(() => `refs.status.${props.status}`);
</script>

<template>
  <span
    class="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs"
    :class="badgeClass"
    :title="message"
  >
    <span aria-hidden="true">{{ statusIcon }}</span>
    <span>{{ t(labelKey) }}</span>
  </span>
</template>
