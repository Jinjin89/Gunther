import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const tauriHost = process.env.TAURI_DEV_HOST;
const apiProxyTarget =
  process.env.GUNTHER_API_PROXY_TARGET ?? "http://127.0.0.1:8787";
const startupModules = new WeakMap<object, Set<string>>();

export default defineConfig({
  clearScreen: false,
  plugins: [react()],
  envDir: "../../",
  server: {
    host: tauriHost ?? "127.0.0.1",
    port: 5173,
    strictPort: true,
    ...(tauriHost
      ? { hmr: {
          protocol: "ws",
          host: tauriHost,
          port: 1421,
        } }
      : {}),
    watch: {
      ignored: ["**/src-tauri/**"],
    },
    proxy: {
      "/api": {
        target: apiProxyTarget,
        ws: true,
      },
    },
  },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    target: process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari13",
    minify: process.env.TAURI_ENV_DEBUG ? false : "esbuild",
    sourcemap: Boolean(process.env.TAURI_ENV_DEBUG),
    // Vite's 500 kB warning is sized for pages fetched over a network; the desktop app
    // reads its files from disk. Gunther's own code is one ~510 kB chunk; libraries that
    // are only needed sometimes (the Markdown editor, the PDF viewer) load on first use.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        // Vendor code changes rarely; keeping it apart keeps the app chunk small and cacheable.
        manualChunks(id, graph) {
          if (!id.includes("node_modules")) return undefined;
          // Follow static imports from the entry points. Let Rollup place modules
          // reached only through import(), including their transitive dependencies.
          let startup = startupModules.get(graph);
          if (!startup) {
            startup = new Set([...graph.getModuleIds()].filter((moduleId) => graph.getModuleInfo(moduleId)?.isEntry));
            for (const moduleId of startup) {
              for (const dependency of graph.getModuleInfo(moduleId)?.importedIds ?? []) startup.add(dependency);
            }
            startupModules.set(graph, startup);
          }
          if (!startup.has(id)) return undefined;
          if (/[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return "react";
          if (id.includes("lucide-react")) return "icons";
          if (id.includes("@tauri-apps")) return "tauri";
          return "vendor";
        },
      },
    },
  },
});
