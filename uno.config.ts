import { defineConfig, presetUno } from "unocss";

export default defineConfig({
  presets: [presetUno()],
  theme: {
    colors: {
      app: {
        bg: "#111827",
        surface: "#0f172a",
        surfaceAlt: "#1f2937",
        border: "#374151",
        text: "#f3f4f6",
        muted: "#9ca3af",
        primary: "#2563eb",
        success: "#166534",
        warning: "#a16207",
      },
    },
  },
  shortcuts: {
    "app-shell": "min-h-screen bg-app-bg text-app-text",
    "app-container": "mx-auto max-w-7xl w-full",
    "app-panel": "min-w-0 rounded-2xl border border-app-border bg-app-surface p-4 shadow-sm shadow-black/20",
    "app-input":
      "w-full rounded-xl border border-app-border bg-app-surfaceAlt px-3 py-2 text-app-text outline-none transition focus:border-app-primary",
    "app-button":
      "inline-flex cursor-pointer items-center justify-center rounded-xl bg-app-primary px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
    "app-button-secondary":
      "inline-flex cursor-pointer items-center justify-center rounded-xl border border-app-border bg-app-surfaceAlt px-4 py-2 text-sm font-semibold text-app-text transition hover:border-app-primary disabled:cursor-not-allowed disabled:opacity-50",
    "app-tab":
      "rounded-xl border border-app-border bg-app-surfaceAlt px-4 py-2 text-sm font-medium text-app-text transition hover:border-app-primary",
    "app-title": "text-3xl font-bold text-app-text",
    "app-subtitle": "text-sm text-app-muted",
    "app-section-title": "text-xl font-semibold text-app-text",
    "app-muted": "text-sm text-app-muted",
  },
});
