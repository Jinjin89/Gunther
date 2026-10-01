import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const tauriHost = process.env.TAURI_DEV_HOST;
const apiProxyTarget =
  process.env.GUNTHER_API_PROXY_TARGET ?? "http://127.0.0.1:8787";

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
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return "react";
          if (id.includes("lucide-react")) return "icons";
          if (id.includes("@tauri-apps")) return "tauri";
          // The Markdown editor loads on first use; keep CodeMirror out of the startup chunk.
          if (/[\\/](@codemirror|@lezer|crelt|style-mod|w3c-keyname)[\\/]/.test(id)) return "editor";
          // The PDF viewer also loads on first use; left in "vendor", pdf.js would load at every start.
          if (/[\\/](pdfjs-dist|react-pdf|make-cancellable-promise|make-event-props|merge-refs)[\\/]/.test(id)) return "pdf";
          // The slide viewer loads when a deck is opened.
          if (/[\\/](reveal\.js|@revealjs)[\\/]/.test(id)) return "reveal";
          return "vendor";
        },
      },
    },
  },
});
