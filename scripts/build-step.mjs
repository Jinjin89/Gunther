import { spawnSync } from "node:child_process";
import process from "node:process";

/**
 * @param {string} command
 * @param {readonly string[]} args
 * @param {Pick<import("node:child_process").SpawnSyncOptions, "cwd" | "env">} [options]
 */
export function runBuildStep(command, args, options) {
  const result = spawnSync(command, args, { ...options, stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
