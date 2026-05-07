import { defineConfig, presetUno } from "unocss";

export default defineConfig({
  presets: [presetUno()],
  theme: {
    colors: {
      app: {
        bg: "#08111f",
        surface: "#111b2e",
        surfaceAlt: "#182438",
        border: "#2a3850",
        text: "#eff6ff",
        muted: "#94a3b8",
        primary: "#7c88ff",
        success: "#14b8a6",
        warning: "#f59e0b",
      },
    },
  },
  shortcuts: {
    "app-shell": "min-h-screen bg-app-bg text-app-text",
    "app-container": "relative mx-auto w-full max-w-[1600px]",
    "app-panel":
      "min-w-0 overflow-hidden rounded-[28px] border border-app-border/75 bg-app-surface/92 p-5 shadow-xl shadow-black/20 backdrop-blur-sm",
    "app-panel-muted":
      "min-w-0 overflow-hidden rounded-[24px] border border-app-border/70 bg-app-surfaceAlt/88 p-4 shadow-lg shadow-black/10 backdrop-blur-sm",
    "app-input":
      "w-full rounded-2xl border border-app-border bg-app-surfaceAlt px-4 py-3 text-sm leading-6 text-app-text outline-none transition placeholder:text-app-muted/80 focus:border-app-primary focus:ring-2 focus:ring-app-primary/20",
    "app-button":
      "inline-flex cursor-pointer items-center justify-center rounded-2xl bg-app-primary px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-app-primary/20 transition hover:-translate-y-0.5 hover:opacity-95 disabled:cursor-not-allowed disabled:translate-y-0 disabled:opacity-50",
    "app-button-secondary":
      "inline-flex cursor-pointer items-center justify-center rounded-2xl border border-app-border bg-app-surfaceAlt px-4 py-2.5 text-sm font-semibold text-app-text transition hover:-translate-y-0.5 hover:border-app-primary disabled:cursor-not-allowed disabled:translate-y-0 disabled:opacity-50",
    "app-tab":
      "inline-flex items-center rounded-2xl border border-app-border bg-app-surfaceAlt px-4 py-2.5 text-sm font-medium text-app-text transition hover:border-app-primary hover:bg-app-surface",
    "app-tab-active": "border-app-primary bg-app-primary/12 text-white shadow-lg shadow-app-primary/15",
    "app-title": "text-3xl font-semibold tracking-tight text-app-text sm:text-4xl",
    "app-subtitle": "text-sm leading-6 text-app-muted",
    "app-section-title": "text-lg font-semibold tracking-tight text-app-text sm:text-xl",
    "app-muted": "text-sm leading-6 text-app-muted",
    "app-kicker": "text-xs font-semibold uppercase tracking-[0.24em] text-app-muted/80",
    "app-chip": "inline-flex items-center gap-2 rounded-full border border-app-border bg-app-surfaceAlt px-3 py-1 text-xs font-medium text-app-muted",
  },
});
