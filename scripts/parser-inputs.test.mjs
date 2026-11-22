import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { parserInputKey, parserManifestIsCurrent } from "./parser-inputs.mjs";

const specification = "tools/document-parser/manifest-inputs.json";
const hash = (data) => createHash("sha256").update(data).digest("hex");

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "gunther-parser-inputs-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const spec = { sources: ["src/reader.py"], lockfile: "uv.lock", projectFile: "pyproject.toml" };
  const write = (name, data) => {
    const path = join(root, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, data);
  };
  write(specification, JSON.stringify(spec));
  write(spec.projectFile, "[project]");
  write(spec.lockfile, "version = 1");
  write(spec.sources[0], "initial source");
  const manifest = {
    lockHash: hash("version = 1"),
    sourceHashes: { [spec.sources[0]]: hash("initial source") },
  };
  return { root, spec, write, manifest };
}

test("current parser manifest is accepted until a source changes", (t) => {
  const { root, spec, write, manifest } = fixture(t);
  const key = parserInputKey(root);
  assert.equal(parserManifestIsCurrent(root, manifest), true);

  write(spec.sources[0], "edited source");
  assert.equal(parserManifestIsCurrent(root, manifest), false);
  assert.notEqual(parserInputKey(root), key);
});

test("missing verification inputs reject the bundle and prevent a cache key", (t) => {
  const { root, spec, write, manifest } = fixture(t);
  for (const name of [spec.sources[0], spec.lockfile, specification]) {
    const path = join(root, name);
    const data = readFileSync(path);
    rmSync(path);
    assert.equal(parserManifestIsCurrent(root, manifest), false, name);
    assert.throws(() => parserInputKey(root), { code: "ENOENT" });
    write(name, data);
  }
});

test("a lockfile-only change rejects the bundle and changes the cache key", (t) => {
  const { root, spec, write, manifest } = fixture(t);
  const key = parserInputKey(root);
  write(spec.lockfile, "version = 2");
  assert.equal(parserManifestIsCurrent(root, manifest), false);
  assert.notEqual(parserInputKey(root), key);
  assert.equal(parserManifestIsCurrent(root, { ...manifest, lockHash: hash("version = 2") }), true);
});

test("the cache and preflight use the current source list", (t) => {
  const { root, spec, write, manifest } = fixture(t);
  const key = parserInputKey(root);
  write("extra.py", "new source");
  assert.equal(parserInputKey(root), key);
  assert.equal(parserManifestIsCurrent(root, manifest), true);

  spec.sources.push("extra.py");
  write(specification, JSON.stringify(spec));
  const addedKey = parserInputKey(root);
  assert.notEqual(addedKey, key);
  assert.equal(parserManifestIsCurrent(root, manifest), false);
  const updated = { ...manifest, sourceHashes: { ...manifest.sourceHashes, "extra.py": hash("new source") } };
  assert.equal(parserManifestIsCurrent(root, updated), true);

  spec.sources.shift();
  write(specification, JSON.stringify(spec));
  assert.notEqual(parserInputKey(root), addedKey);
  assert.equal(parserManifestIsCurrent(root, updated), false);
});

test("the cache key covers the specification bytes and project metadata", (t) => {
  const { root, spec, write } = fixture(t);
  const key = parserInputKey(root);
  write(specification, JSON.stringify(spec, null, 2));
  const formattedKey = parserInputKey(root);
  assert.notEqual(formattedKey, key);

  write(spec.projectFile, '[project]\nname = "changed"');
  assert.notEqual(parserInputKey(root), formattedKey);
});
