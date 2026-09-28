import { execFileSync, spawnSync } from "node:child_process";
import { readdirSync, statSync, mkdirSync, copyFileSync, chmodSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const root = resolve(import.meta.dirname, "..");
const backendRoot = join(root, "apps", "backend");
const binariesDir = join(root, "apps", "desktop", "src-tauri", "binaries");
const target = execFileSync("rustc", ["--print", "host-tuple"], { encoding: "utf8" }).trim();
const extension = process.platform === "win32" ? ".exe" : "";
const destination = join(binariesDir, `gunther-backend-${target}${extension}`);
const pyinstallerRoot = join(backendRoot, ".pyinstaller");
const useMacHelperApp = process.platform === "darwin";
const frozenName = useMacHelperApp ? "GuntherBackend" : "gunther-backend";
const helperApp = join(pyinstallerRoot, "dist", "GuntherBackend.app");
const rawBinary = join(pyinstallerRoot, "dist", `gunther-backend${extension}`);
const completedOutput = useMacHelperApp ? helperApp : destination;
const visionSource = join(backendRoot, "gunther", "vision_ocr.m");
const visionBuildDir = join(pyinstallerRoot, "vision");
const visionBinary = join(visionBuildDir, "gunther-vision-ocr");
const modelDir = join(backendRoot, "models", "multilingual-e5-small");

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
if (
  existsSync(completedOutput)
  && statSync(completedOutput).mtimeMs >= latestInput
) {
  process.stdout.write(`Gunther backend helper is current: ${completedOutput}\n`);
  process.exit(0);
}

// Semantic search ships inside the helper; fetch the pinned model once.
if (!existsSync(join(modelDir, "model.onnx")) || !existsSync(join(modelDir, "tokenizer.json"))) {
  const fetched = spawnSync("python3", [join(root, "scripts", "fetch_embedding_model.py")], {
    cwd: root,
    stdio: "inherit",
  });
  if (fetched.status !== 0) process.exit(fetched.status ?? 1);
}

mkdirSync(binariesDir, { recursive: true });
mkdirSync(pyinstallerRoot, { recursive: true });
if (useMacHelperApp) {
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
  ...(useMacHelperApp ? ["--onedir", "--windowed"] : ["--onefile"]),
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
  ...(useMacHelperApp ? ["--add-binary", `${visionBinary}:.`] : []),
  join(backendRoot, "gunther", "desktop_server.py"),
], {
  cwd: root,
  env: {
    ...process.env,
    UV_CACHE_DIR: join(root, ".uv-cache"),
    PYINSTALLER_CONFIG_DIR: join(pyinstallerRoot, "cache"),
  },
  stdio: "inherit",
});

if (result.status !== 0) process.exit(result.status ?? 1);
if (!useMacHelperApp) {
  copyFileSync(rawBinary, destination);
  if (process.platform !== "win32") chmodSync(destination, 0o755);
}
process.stdout.write(`Built Gunther backend helper: ${completedOutput}\n`);
