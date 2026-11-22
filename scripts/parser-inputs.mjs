import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const specification = "tools/document-parser/manifest-inputs.json";
const sha256 = (data) => createHash("sha256").update(data).digest("hex");
const hashFile = (root, name) => sha256(readFileSync(join(root, name)));

export function parserInputKey(repoRoot) {
  const spec = readFileSync(join(repoRoot, specification));
  const { sources, lockfile, projectFile } = JSON.parse(spec.toString("utf8"));
  const hashes = [projectFile, lockfile, ...sources].map((name) => [name, hashFile(repoRoot, name)]);
  return sha256(JSON.stringify([[specification, sha256(spec)], ...hashes]));
}

export function parserManifestIsCurrent(repoRoot, manifest) {
  try {
    const { sources, lockfile } = JSON.parse(readFileSync(join(repoRoot, specification), "utf8"));
    const { lockHash, sourceHashes } = manifest ?? {};
    return lockHash === hashFile(repoRoot, lockfile)
      && Object.keys(sourceHashes ?? {}).length === sources.length
      && sources.every((name) => sourceHashes?.[name] === hashFile(repoRoot, name));
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(`${parserInputKey(resolve(import.meta.dirname, ".."))}\n`);
}
