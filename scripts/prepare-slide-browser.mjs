// Pin distribution to this checkout's locked Playwright revision; nothing downloads at run time.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { createRequire } from "node:module";
const root = resolve(import.meta.dirname, "..");
const require = createRequire(join(root, "apps/desktop/package.json"));
const playwright = join(dirname(require.resolve("playwright/package.json")), "cli.js");
const cache = join(root, "tools/slide-browser/.cache");
mkdirSync(cache, { recursive: true });
const result = spawnSync(process.execPath, [playwright, "install", "chromium", "--only-shell"], {
  env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: cache }, stdio: "inherit",
});
if (result.status !== 0) process.exit(result.status ?? 1);
const walk = folder => readdirSync(folder, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(join(folder, entry.name)) : [join(folder, entry.name)]);
const files = walk(cache);
const executable = files.find(path => /[\\/]chrome-headless-shell(?:\.exe)?$/.test(path));
if (!executable) throw new Error("The pinned Chromium headless shell is missing.");
const revision = relative(cache, executable).split(/[\\/]/)[0];
writeFileSync(join(cache, "manifest.json"), JSON.stringify({ version: 1, revision,
  executable: relative(cache, executable), sha256: createHash("sha256").update(readFileSync(executable)).digest("hex"),
  bytes: files.reduce((total, path) => total + statSync(path).size, 0),
  distribution: "Playwright Chromium headless shell; third-party notices remain with the runtime",
}, null, 2));
process.stdout.write(`Prepared slide browser ${revision}\n`);
