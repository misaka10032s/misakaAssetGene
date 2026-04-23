<script setup lang="ts">
import { AppLocale } from "@/types/enums";

import { useI18n } from "vue-i18n";

const { locale } = useI18n();
const localeOptions = [AppLocale.ZH_TW, AppLocale.EN, AppLocale.JA] as const;
const localeLabels: Record<AppLocale, string> = {
  [AppLocale.ZH_TW]: "繁體中文",
  [AppLocale.EN]: "English",
  [AppLocale.JA]: "日本語",
};

/**
 * Changes the current interface locale.
 *
 * @param nextLocale - The locale selected by the user.
 */
function changeLocale(nextLocale: AppLocale): void {
  locale.value = nextLocale;
}
</script>

<template>
  <label class="flex items-center gap-2 text-sm text-app-muted">
    <span>{{ $t("app.locale") }}</span>
    <select :value="locale" class="app-input max-w-40" @change="changeLocale(($event.target as HTMLSelectElement).value as AppLocale)">
      <option v-for="localeOption in localeOptions" :key="localeOption" :value="localeOption">
        {{ localeLabels[localeOption] }}
      </option>
    </select>
  </label>
</template>
