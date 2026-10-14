#!/usr/bin/env node

import { execFile } from "node:child_process";
import { lstat, mkdtemp, mkdir, readdir, rename, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const exec = promisify(execFile);
const run = (command, args) => exec(command, args, { maxBuffer: 8 * 1024 * 1024 });
const exists = (file) => lstat(file).then(() => true, (error) => { if (error.code === "ENOENT") return false; throw error; });

/**
 * Tauri adds a volume icon alongside the app. Finder's Show Hidden Files makes it
 * a second, dimmed app icon. Remove that file from the installer, retaining the
 * app, Applications shortcut and Finder layout. Never change a user's Finder preference.
 */
export async function cleanDiskImage(image) {
  if (process.platform !== "darwin") return;
  image = path.resolve(image);
  const mountParent = await mkdtemp(path.join(tmpdir(), "gunther-installer-"));
  const mountPoint = path.join(mountParent, "volume");
  await mkdir(mountPoint);
  let mounted = false;
  let working = null;
  const withMount = async (file, readonly, inspect) => {
    await run("hdiutil", ["attach", "-nobrowse", "-noautoopen", "-mountpoint", mountPoint, ...(readonly ? ["-readonly"] : []), file]);
    mounted = true;
    try { return await inspect(mountPoint); }
    finally {
      await run("hdiutil", ["detach", mountPoint]);
      mounted = false;
    }
  };
  try {
    const hasIcon = await withMount(image, true, (volume) => exists(path.join(volume, ".VolumeIcon.icns")));
    if (!hasIcon) return;

    // Preserve a Developer ID signature on the DMG. The signed/notarized app
    // inside is copied by hdiutil without editing any of its bytes or tickets.
    let identity = null;
    try {
      const signature = await run("codesign", ["--display", "--verbose=2", image]);
      identity = signature.stderr.match(/^Authority=(.+)$/m)?.[1] ?? (signature.stderr.includes("Signature=adhoc") ? "-" : null);
    } catch (error) {
      if (!error.stderr?.includes("code object is not signed at all")) throw error;
    }

    // Work beside the output for an atomic final rename; failures keep the original.
    working = await mkdtemp(path.join(path.dirname(image), ".gunther-layout-"));
    const writable = path.join(working, "writable.dmg");
    const clean = path.join(working, "clean.dmg");
    process.stdout.write(`Removing the duplicate installer icon: ${path.basename(image)}\n`);
    await run("hdiutil", ["convert", image, "-format", "UDRW", "-o", writable]);
    await withMount(writable, false, async (volume) => {
      await rm(path.join(volume, ".VolumeIcon.icns"));
      // Clear only the volume's custom-icon flag, never the app's icon.
      await run("SetFile", ["-a", "c", volume]);
    });
    await run("hdiutil", ["convert", writable, "-format", "UDZO", "-imagekey", "zlib-level=6", "-o", clean]);
    await run("hdiutil", ["verify", clean]);
    await withMount(clean, true, async (volume) => {
      if (await exists(path.join(volume, ".VolumeIcon.icns"))) throw new Error("The installer still contains a duplicate volume icon.");
    });
    if (identity) {
      await run("codesign", ["--force", "--sign", identity, ...(identity === "-" ? [] : ["--timestamp"]), clean]);
      await run("codesign", ["--verify", "--strict", clean]);
    }
    await rename(clean, image);
    process.stdout.write(`Installer layout verified: ${image}\n`);
  } finally {
    // Never recursively remove a directory while an image is still mounted in it.
    if (!mounted) {
      await rm(mountParent, { recursive: true, force: true });
      if (working) await rm(working, { recursive: true, force: true });
    }
  }
}

/** Only freshly built DMGs, including cross-target builds and custom Cargo target dirs. */
export async function cleanBuiltDiskImages(targetDirectory, since) {
  const dirs = await readdir(targetDirectory, { withFileTypes: true });
  const candidates = [targetDirectory, ...dirs.filter((entry) => entry.isDirectory()).map((entry) => path.join(targetDirectory, entry.name))];
  for (const directory of candidates) {
    for (const profile of ["release", "debug"]) {
      const dmgDirectory = path.join(directory, profile, "bundle", "dmg");
      if (!await exists(dmgDirectory)) continue;
      for (const name of await readdir(dmgDirectory)) {
        if (!name.endsWith(".dmg")) continue;
        const image = path.join(dmgDirectory, name);
        const info = await lstat(image);
        if (info.isFile() && info.mtimeMs >= since) await cleanDiskImage(image);
      }
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw new Error("Usage: node scripts/clean-dmg.mjs <installer.dmg> [...]");
  for (const file of process.argv.slice(2)) await cleanDiskImage(file);
}
