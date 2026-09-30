import { spawnSync } from "node:child_process";
import { readdirSync, statSync, mkdirSync, chmodSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

// A folder, not --onefile: one-file helpers unpack ~200 MB on every launch.
const root = resolve(import.meta.dirname, "..");
const backendRoot = join(root, "apps", "backend");
const pyinstallerRoot = join(backendRoot, ".pyinstaller");
const isMac = process.platform === "darwin";
const frozenName = isMac ? "GuntherBackend" : "gunther-backend";
const completedOutput = join(pyinstallerRoot, "dist", isMac ? "GuntherBackend.app" : "gunther-backend");
const visionSource = join(backendRoot, "gunther", "vision_ocr.m");
const visionBuildDir = join(pyinstallerRoot, "vision");
const visionBinary = join(visionBuildDir, "gunther-vision-ocr");
const modelDir = join(backendRoot, "models", "multilingual-e5-small");
const helperEntitlements = join(root, "apps", "desktop", "src-tauri", "HelperEntitlements.plist");
// Same variable Tauri reads to sign the outer app; notarization needs both signed alike.
const signingIdentity = isMac ? process.env.APPLE_SIGNING_IDENTITY?.trim() : undefined;
const uvEnv = { ...process.env, UV_CACHE_DIR: join(root, ".uv-cache") };

const newestModifiedTime = (path) => {
  const stat = statSync(path);
  if (!stat.isDirectory()) return stat.mtimeMs;
  return readdirSync(path).reduce((latest, name) => {
    const child = join(path, name);
    if ([".venv", ".pyinstaller", "__pycache__"].includes(name)) return latest;
    return Math.max(latest, newestModifiedTime(child));
  }, stat.mtimeMs);
};

const inputs = [
  join(backendRoot, "gunther"),
  join(backendRoot, "pyproject.toml"),
  join(backendRoot, "uv.lock"),
  modelDir,
  fileURLToPath(import.meta.url),
];
const latestInput = Math.max(...inputs.filter(existsSync).map(newestModifiedTime));
// `tauri dev` runs the backend with uvicorn on 8787 and never starts the helper; it only has
// to exist for the bundle config. Rebuilding it after every backend edit cost a minute of
// PyInstaller output (and its harmless "Hidden import ... not found" warnings) per start.
if (process.argv.includes("--if-missing") && existsSync(completedOutput)) {
  process.stdout.write(`Gunther backend helper exists; development uses the live backend instead: ${completedOutput}\n`);
  process.exit(0);
}
// A signed release always rebuilds, so an ad-hoc helper from a dev build never ships.
if (
  !signingIdentity
  && existsSync(completedOutput)
  && statSync(completedOutput).mtimeMs >= latestInput
) {
  process.stdout.write(`Gunther backend helper is current: ${completedOutput}\n`);
  process.exit(0);
}

// Semantic search ships inside the helper; fetch the pinned model once.
if (!existsSync(join(modelDir, "model.onnx")) || !existsSync(join(modelDir, "tokenizer.json"))) {
  const fetched = spawnSync("uv", [
    "run",
    "--project", backendRoot,
    "python", join(root, "scripts", "fetch_embedding_model.py"),
  ], {
    cwd: root,
    env: uvEnv,
    stdio: "inherit",
  });
  if (fetched.status !== 0) process.exit(fetched.status ?? 1);
}

mkdirSync(pyinstallerRoot, { recursive: true });
if (isMac) {
  mkdirSync(visionBuildDir, { recursive: true });
  const visionResult = spawnSync("xcrun", [
    "clang",
    "-fobjc-arc",
    "-fblocks",
    "-mmacosx-version-min=11.0",
    "-framework", "AppKit",
    "-framework", "Foundation",
    "-framework", "ImageIO",
    "-framework", "PDFKit",
    "-framework", "Vision",
    visionSource,
    "-o", visionBinary,
  ], {
    cwd: root,
    stdio: "inherit",
  });
  if (visionResult.status !== 0) process.exit(visionResult.status ?? 1);
  chmodSync(visionBinary, 0o755);
}
const result = spawnSync("uv", [
  "run",
  "--project", backendRoot,
  "pyinstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  ...(isMac ? ["--windowed", "--osx-bundle-identifier", "com.gunther.knowledge.backend"] : []),
  // PyInstaller signs every collected binary with hardened runtime + timestamp.
  ...(signingIdentity
    ? ["--codesign-identity", signingIdentity, "--osx-entitlements-file", helperEntitlements]
    : []),
  "--name", frozenName,
  "--paths", backendRoot,
  "--distpath", join(pyinstallerRoot, "dist"),
  "--workpath", join(pyinstallerRoot, "build"),
  "--specpath", pyinstallerRoot,
  "--collect-submodules", "uvicorn",
  "--collect-all", "cryptography",
  "--collect-all", "onnxruntime",
  "--collect-all", "tokenizers",
  "--collect-all", "sqlite_vec",
  "--add-data", `${modelDir}:models/multilingual-e5-small`,
  ...(isMac ? ["--add-binary", `${visionBinary}:.`] : []),
  join(backendRoot, "gunther", "desktop_server.py"),
], {
  cwd: root,
  env: {
    ...uvEnv,
    PYINSTALLER_CONFIG_DIR: join(pyinstallerRoot, "cache"),
  },
  stdio: "inherit",
});

if (result.status !== 0) process.exit(result.status ?? 1);
process.stdout.write(`Built Gunther backend helper: ${completedOutput}\n`);
