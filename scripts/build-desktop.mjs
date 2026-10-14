#!/usr/bin/env node

import { execFile, spawn } from "node:child_process";
import path from "node:path";
import { createRequire } from "node:module";
import { promisify } from "node:util";
import { cleanBuiltDiskImages } from "./clean-dmg.mjs";

const root = path.resolve(import.meta.dirname, "..");
const desktop = path.join(root, "apps", "desktop");
const require = createRequire(path.join(desktop, "package.json"));
const cli = require.resolve("@tauri-apps/cli/tauri.js");
const args = process.argv.slice(2);
const started = Date.now();
// Pass every argument through to Tauri, preserving platform config and signing.
const child = spawn(process.execPath, [cli, "build", ...args], { cwd: desktop, stdio: "inherit" });
const code = await new Promise((resolve, reject) => {
  child.once("error", reject);
  child.once("exit", (exitCode) => resolve(exitCode ?? 1));
});
if (code !== 0) process.exit(code);
if (process.platform === "darwin" && !args.some((arg) => ["--help", "-h", "--version", "-V", "--no-bundle"].includes(arg))) {
  const { stdout } = await promisify(execFile)("cargo", ["metadata", "--no-deps", "--format-version", "1"], { cwd: path.join(desktop, "src-tauri") });
  await cleanBuiltDiskImages(JSON.parse(stdout).target_directory, started);
}
