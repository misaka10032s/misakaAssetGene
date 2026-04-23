import { createI18n } from "vue-i18n";

import { appEnv } from "@/config/env";
import { AppLocale } from "@/types/enums";
import { messages } from "@/i18n/messages";

export const i18n = createI18n({
  legacy: false,
  locale: appEnv.defaultLocale,
  fallbackLocale: AppLocale.EN,
  messages,
});
