import { defineConfig, mergeConfig, type PreviewServer, type ViteDevServer } from "vite";
import { fileURLToPath } from "node:url";
import { readFileSync } from "node:fs";
import app from "../../vite.config";

const figure = readFileSync(new URL("./source-figure.svg", import.meta.url));
function assets(server: ViteDevServer | PreviewServer) {
  server.middlewares.use("/api/assets/eval-collection", (_req, res) => {
    res.setHeader("Content-Type", "image/svg+xml");
    res.end(figure);
  });
}
export default mergeConfig(app, defineConfig({
  root: fileURLToPath(new URL("../../", import.meta.url)),
  define: { "import.meta.env.VITE_API_URL": JSON.stringify("") },
  server: { port: 5184 },
  build: { rollupOptions: { input: fileURLToPath(new URL("./index.html", import.meta.url)) } },
  plugins: [{ name: "slide-evaluation-assets", configureServer: assets, configurePreviewServer: assets }],
}));
