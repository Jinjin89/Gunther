import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import process from "node:process";
import test from "node:test";

function invoke(command, args, options = {}) {
  const runner = `
    import { runBuildStep } from ${JSON.stringify(new URL("./build-step.mjs", import.meta.url).href)};
    runBuildStep(...${JSON.stringify([command, args, options])});
    console.log("finished");
  `;
  const result = spawnSync(process.execPath, ["--input-type=module", "--eval", runner], {
    encoding: "utf8", timeout: 5000,
  });
  assert.ifError(result.error);
  assert.equal(result.signal, null);
  return result;
}

function temporaryDirectory(t) {
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "gunther-build-step-")));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  return cwd;
}

test("build step passes arguments, working directory, environment, and child output", (t) => {
  const cwd = temporaryDirectory(t);
  const args = ["--target", "two words", "--", "$unchanged"];
  const child = `
    console.log(JSON.stringify([process.argv.slice(1), process.cwd(), process.env.GUNTHER_BUILD_TEST]));
    console.error("child stderr");
  `;
  const result = invoke(process.execPath, ["--eval", child, "--", ...args], {
    cwd, env: { GUNTHER_BUILD_TEST: "configured" },
  });

  assert.equal(result.status, 0);
  assert.deepEqual(result.stdout.trimEnd().split(/\r?\n/), [
    JSON.stringify([args, cwd, "configured"]), "finished",
  ]);
  assert.equal(result.stderr.trim(), "child stderr");
});

test("build step forwards a nonzero exit code", () => {
  const result = invoke(process.execPath, ["--eval", "process.exit(23)"]);
  assert.equal(result.status, 23);
  assert.equal(result.stdout, "");
});

test("build step exits with one after a child is killed", () => {
  const result = invoke(process.execPath, ["--eval", "process.kill(process.pid, 'SIGTERM')"]);
  assert.equal(result.status, 1);
  assert.equal(result.stdout, "");
});

test("build step exits with one when the executable is missing", (t) => {
  const result = invoke(join(temporaryDirectory(t), "missing-command"), []);
  assert.equal(result.status, 1);
  assert.equal(result.stdout, "");
});
