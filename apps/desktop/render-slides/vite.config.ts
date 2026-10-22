import { defineConfig, mergeConfig } from "vite";
import { fileURLToPath } from "node:url";
import app from "../vite.config";

// A real production entry point, shipped with the Python helper. No evaluation fixtures,
// dev server, Node runtime, deployment API address or main Outputs screen is needed.
export default mergeConfig(app, defineConfig({
  base: "./",
  define: { "import.meta.env.VITE_API_URL": JSON.stringify("") },
  build: {
    outDir: "render-dist",
    emptyOutDir: true,
    rollupOptions: { input: fileURLToPath(new URL("./index.html", import.meta.url)) },
  },
}));
