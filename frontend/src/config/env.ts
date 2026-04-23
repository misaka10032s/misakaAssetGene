import { AppLocale } from "@/types/enums";
import type { AppEnv } from "@/types/api";

const defaultLocale = (import.meta.env.VITE_MISAKA_DEFAULT_LOCALE as string | undefined) ?? AppLocale.ZH_TW;
const appMode = (import.meta.env.VITE_MISAKA_ENV as string | undefined) ?? "production";

export const appEnv: Readonly<AppEnv> = Object.freeze({
  apiBaseUrl: (import.meta.env.VITE_MISAKA_API_BASE as string | undefined) ?? "http://127.0.0.1:7500",
  appMode,
  diagnosticsEnabled: import.meta.env.DEV && appMode === "dev",
  defaultLocale,
});
