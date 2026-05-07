import { loadEnv, defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import Inspector from "vite-plugin-vue-inspector";
import UnoCSS from "unocss/vite";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, resolve(frontendRoot, ".."), "");
  const isDevelopment = mode === "development";

  return {
    root: resolve(frontendRoot),
    plugins: [
      vue(),
      ...(isDevelopment
        ? [
            Inspector({
              launchEditor: "code",
              toggleButtonVisibility: "always",
              toggleButtonPos: "bottom-right",
              toggleComboKey: "control-shift",
              disableInspectorOnEditorOpen: true,
            }),
          ]
        : []),
      UnoCSS(),
    ],
    server: {
      port: Number(env.MISAKA_FRONTEND_PORT || "7501"),
      strictPort: true,
    },
    build: {
      outDir: resolve(frontendRoot, "../dist"),
      emptyOutDir: true,
    },
    resolve: {
      alias: {
        "@": resolve(frontendRoot, "src"),
      },
    },
  };
});
