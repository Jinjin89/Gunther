#!/usr/bin/env node

/**
 * Browser smoke for the studio flow: material -> a deck made in a conversation -> slides on
 * screen -> a change to slide 2 -> PowerPoint download.
 *
 * Nothing real is called. A tiny OpenAI-compatible model server answers the studio's prompts
 * (memo, storyline, critic, deck, checker, the Artifacts agent's decisions); a disposable
 * backend (output method "studio") talks to it; the production web bundle is built and served
 * on loopback; Playwright's Chromium drives it. Everything is removed at the end.
 *
 * Run (from the repository root, about two minutes):
 *
 *   PATH=/tmp/uvbin:$PATH UV_OFFLINE=1 node scripts/smoke_studio_flow.mjs --allow-write \
 *     [--screenshots-dir=/tmp/studio-shots]
 *
 * Needs `uv` on PATH and a Chromium: the one Playwright installed (the default), or set
 * CHROME_BIN to a Chrome/Chromium executable. Prints JSON with "status": "passed".
 */

import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readdir, readFile, realpath, rm, stat } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const desktopRoot = path.join(root, "apps", "desktop");
const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");

class SmokeFailure extends Error {}

// What the fake model says ---------------------------------------------------------------

const PASSAGES = {
  "Trial design": "The trial followed 412 buildings in six districts over the summers 2021 to 2023.",
  "Savings": "Raw cooling electricity fell 18%; adjusted for summer heat the saving was 9%.",
  "Ageing": "The coating's albedo fell from 0.82 to 0.55 within 18 months.",
};

const DECK = `<!-- layout: title; design: v2; theme: paper -->
# Cool roofs work, but at half the headline saving
The 18% saving is 9% after weather adjustment [2]

---

<!-- layout: stats -->
## The trial was large enough to trust the direction
- **412** buildings in six districts [1]
- **3** summers, 2021–2023 [1]
Note: Size first.

---

<!-- layout: chart; chart: column -->
## Weather adjustment halves the saving
| Measure | Saving (%) |
| --- | --- |
| Raw | 18 [2] |
| **Adjusted** | 9 [2] |
Plan with the adjusted figure [2]
Note: The bars.
`;

const REWRITTEN = `<!-- layout: stats -->
## Three summers and 412 buildings back the result
- **412** buildings in six districts [1]
- **3** summers, 2021–2023 [1]
Note: Size.`;

const MEMO = {
  language: "English",
  answer: "Cool roofs save about 9% once weather is adjusted, not the 18% headline",
  insights: [
    { claim: "The saving halves after weather adjustment", so_what: "Budget with 9%, not 18%", refs: [2] },
    { claim: "The trial is large enough to trust the direction", so_what: "Size is not the weak point", refs: [1] },
  ],
  facts: [],
  tensions: [{ issue: "Long-term coating decay is only partly covered", refs: [3] }],
  questions: [],
  title: "Cool roofs",
};

const STORY = {
  title: "Cool roofs work, but at half the headline saving",
  subtitle: "What the trial shows",
  slides: [
    { title: "Cool roofs work, but at half the headline saving", layout: "title", job: "", refs: [] },
    { title: "The trial was large enough to trust the direction", layout: "stats", job: "Show the size", refs: [1] },
    { title: "Weather adjustment halves the saving", layout: "chart", job: "Show the two figures", refs: [2] },
  ],
};

const unknown = [];

/** The model's reply to one request, chosen from the system prompt the studio sends. */
function reply(request, delayed) {
  const system = String(request.messages[0]?.content ?? "");
  const last = String(request.messages.at(-1)?.content ?? "");
  if (system.startsWith("You are the orchestrator of Gunther's Artifacts")) {
    const change = JSON.stringify(request.messages).includes("Make slide 2");
    return JSON.stringify(change
      ? { actions: [{ do: "write", parts: [2], instruction: "Lead with the size" }, { do: "save", text: "Rewrote slide 2." }] }
      : { actions: [{ do: "select", artifact: "new:slides" }, { do: "make" }, { do: "save", text: "Made the deck." }] });
  }
  if (system.startsWith("You are Gunther, answering a question about")) return "It says so.";
  if (system.includes("lead analyst")) return delayed(JSON.stringify(MEMO), 1500);
  if (system.includes("presentation strategist")) return delayed(JSON.stringify(STORY), 3000);
  if (system.includes("senior partner")) {
    return JSON.stringify({ understood: "Savings halve once weather is adjusted", verdict: "ready", problems: [] });
  }
  if (system.includes("working on one slide")) return REWRITTEN;
  if (system.includes("presentation writer")) return delayed(DECK, 8000);
  if (system.includes("fact-checker")) return DECK.split("\n---\n")[2];
  if (system.includes("knowledge assertions")) return JSON.stringify({ assertions: [] });
  if (system.includes("checking step")) {
    const numbers = [...last.matchAll(/^\((\d+)\)/gm)].map((match) => Number(match[1]));
    return JSON.stringify({ sentences: numbers.map((n) => ({ n, verdict: "supported" })) });
  }
  unknown.push(system.slice(0, 120));
  return JSON.stringify({});
}

async function startModelServer() {
  const delayed = (text, pause) => ({ text, pause });
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", async () => {
      if (!request.url?.endsWith("/chat/completions")) {
        response.writeHead(404).end();
        return;
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      let answer = reply(body, delayed);
      if (typeof answer === "object") {
        await delay(answer.pause);
        answer = answer.text;
      }
      const chunk = (delta, finish = null) => `data: ${JSON.stringify({ id: "x", object: "chat.completion.chunk", created: 0, model: body.model, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`;
      if (body.stream) {
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        response.write(chunk({ role: "assistant", content: answer }));
        response.write(chunk({}, "stop"));
        response.end("data: [DONE]\n\n");
      } else {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ id: "x", object: "chat.completion", created: 0, model: body.model, choices: [{ index: 0, message: { role: "assistant", content: answer }, finish_reason: "stop" }] }));
      }
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, port: server.address().port };
}

// Plumbing -------------------------------------------------------------------------------

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}

function logsOf(child) {
  let text = "";
  const add = (data) => { text = (text + data).slice(-60_000); };
  child.stdout?.on("data", add);
  child.stderr?.on("data", add);
  return () => text;
}

function run(command, args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { ...options, stdio: ["ignore", "pipe", "pipe"] });
    const logs = logsOf(child);
    child.on("exit", (code) => (code === 0 ? resolve() : reject(new SmokeFailure(`${command} ${args.join(" ")} failed\n${logs()}`))));
  });
}

async function waitForHttp(url, child, logs) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child && child.exitCode !== null) throw new SmokeFailure(`The backend exited early\n${logs()}`);
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(1500) })).ok) return;
    } catch { /* not up yet */ }
    await delay(150);
  }
  throw new SmokeFailure(`Timed out waiting for ${url}\n${logs()}`);
}

async function serveStatic(directory) {
  const server = http.createServer(async (request, response) => {
    const name = decodeURIComponent(new URL(request.url ?? "/", "http://x").pathname).replace(/^\/+/, "");
    let file = path.resolve(directory, name || "index.html");
    if (!file.startsWith(path.resolve(directory)) || !(await stat(file).catch(() => null))?.isFile()) {
      file = path.join(directory, "index.html");
    }
    const types = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".json": "application/json" };
    response.writeHead(200, { "Content-Type": types[path.extname(file)] ?? "application/octet-stream" });
    response.end(await readFile(file));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, port: server.address().port };
}

const closing = (server) => new Promise((resolve) => (server ? server.close(() => resolve()) : resolve()));

/** Playwright's own Chromium, whichever revision is installed (its npm package may expect a newer one). */
async function installedChromium() {
  const cache = process.env.PLAYWRIGHT_BROWSERS_PATH ?? path.join(os.homedir(), ".cache", "ms-playwright");
  const names = (await readdir(cache).catch(() => [])).filter((name) => /^chromium-\d+$/.test(name)).sort().reverse();
  for (const name of names) {
    for (const rel of ["chrome-linux64/chrome", "chrome-linux/chrome", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"]) {
      const file = path.join(cache, name, rel);
      if ((await stat(file).catch(() => null))?.isFile()) return file;
    }
  }
  return undefined;
}

// The flow -------------------------------------------------------------------------------

async function main() {
  if (!process.argv.includes("--allow-write")) throw new SmokeFailure("--allow-write is required (the smoke writes a disposable data directory)");
  const shotsArg = process.argv.find((arg) => arg.startsWith("--screenshots-dir="));
  const shots = shotsArg ? path.resolve(shotsArg.slice("--screenshots-dir=".length)) : null;
  if (shots) await mkdir(shots, { recursive: true });
  const temp = await realpath(await mkdtemp(path.join(os.tmpdir(), "gunther-studio-smoke.")));
  const data = path.join(temp, "data");
  const dist = path.join(temp, "dist");
  await mkdir(data);

  let backend; let browser; let ui; let model; let page;
  let logs = () => "";
  try {
    // The model pauses on the long steps so the live view can be seen and photographed.
    model = await startModelServer();
    await mkdir(dist);
    ui = await serveStatic(dist);
    const backendPort = await freePort();
    const backendOrigin = `http://127.0.0.1:${backendPort}`;
    const uiOrigin = `http://127.0.0.1:${ui.port}`;
    backend = spawn("uv", ["run", "--project", "apps/backend", "uvicorn", "--app-dir", "apps/backend", "gunther.main:app", "--host", "127.0.0.1", "--port", String(backendPort), "--log-level", "warning", "--no-access-log"], {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        CORS_ORIGINS: JSON.stringify([uiOrigin]),
        DATABASE_URL: `sqlite+pysqlite:///${path.join(data, "gunther.sqlite")}`,
        SERVICE_SETTINGS_FILE: path.join(data, "service-settings.json"),
        LIBRARY_ROOT: path.join(data, "Gunther"),
        LLM_PROVIDER: "compatible",
        LLM_API_KEY: "fake-key",
        LLM_BASE_URL: `http://127.0.0.1:${model.port}/v1`,
        LLM_MODEL: "fake-model",
        AI_SUMMARIES: "off",
        SEED_DEMO: "false",
        UV_CACHE_DIR: path.join(root, ".uv-cache"),
      },
    });
    logs = logsOf(backend);
    await waitForHttp(`${backendOrigin}/api/health`, backend, logs);
    // Studio is the default output method; say so anyway.
    await fetch(`${backendOrigin}/api/developer/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ outputMethod: "studio" }) }).catch(() => {});

    // Material: a library with three notes.
    const post = async (url, body) => {
      const response = await fetch(`${backendOrigin}${url}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!response.ok) throw new SmokeFailure(`POST ${url} -> ${response.status} ${await response.text()}`);
      return response.json();
    };
    const library = await post("/api/knowledge-bases", { title: "Cool roofs", question: "Do cool roofs save energy?", description: "Trial notes." });
    for (const [title, content] of Object.entries(PASSAGES)) {
      await post("/api/sources", { title, kind: "note", knowledgeBaseId: library.id, content });
    }

    await run(process.execPath, [viteBin, "build", "--outDir", dist, "--emptyOutDir"], { cwd: desktopRoot, env: { ...process.env, VITE_API_URL: backendOrigin } });

    const executablePath = process.env.CHROME_BIN ?? await installedChromium();
    browser = await chromium.launch({
      ...(executablePath ? { executablePath } : {}),
      args: process.getuid?.() === 0 ? ["--no-sandbox"] : [],
    });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
    page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const shot = async (name) => { if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`) }); };

    await page.goto(uiOrigin);
    await page.getByRole("button", { name: "Libraries", exact: true }).click();
    await page.getByRole("button", { name: "Cool roofs", exact: true }).first().click();
    await page.getByRole("navigation", { name: "Library views" }).getByRole("button", { name: "Artifacts", exact: true }).click();

    // 1. A deck from a conversation.
    const message = page.getByRole("textbox", { name: "Artifact message" });
    await message.fill("A 10-minute deck for the facilities team on what the trial shows");
    await page.getByRole("button", { name: "Send message" }).click();

    // While it is made: the memo, the critic and the planned titles appear.
    await page.locator(".studio-live .studio-answer").waitFor({ timeout: 90_000 });
    if (!(await page.locator(".studio-live").innerText()).includes("Cool roofs save about 9%")) throw new SmokeFailure("The memo's answer was not shown while building");
    await page.locator(".studio-live .studio-insights li").first().waitFor();
    await shot("1-making-memo");
    await page.locator(".studio-critic").waitFor({ timeout: 90_000 });
    await page.locator(".studio-planned li").nth(2).waitFor();
    await shot("2-making-storyline");

    // 2. Slides render.
    await page.getByRole("button", { name: /^Open v1/ }).waitFor({ timeout: 180_000 });
    await page.locator(".aw-deck").waitFor();
    await page.getByText("Weather adjustment halves the saving").first().waitFor({ timeout: 30_000 });
    await shot("3-deck");

    // The saved artifact says how it was made.
    await page.getByRole("button", { name: "Show preview controls" }).click();
    await page.getByRole("button", { name: /How it was made/ }).click();
    await page.locator(".studio-brief .studio-insights li").first().waitFor();
    const brief = await page.locator(".studio-brief").innerText();
    if (!/quality report/i.test(brief)) throw new SmokeFailure("The saved studio artifact has no quality report");
    await page.locator(".studio-quality").scrollIntoViewIfNeeded();
    await shot("4-how-it-was-made");

    // 3. Change slide 2 by talking.
    await message.fill("Make slide 2 lead with the size");
    await page.getByRole("button", { name: "Send message" }).click();
    await page.getByText("Three summers and 412 buildings back the result").first().waitFor({ timeout: 120_000 });
    await page.getByRole("button", { name: /^Open v2/ }).waitFor({ timeout: 60_000 });
    await shot("5-slide-changed");

    // 4. PowerPoint.
    await page.getByRole("button", { name: "Export artifact" }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: 60_000 }),
      page.getByRole("menuitem", { name: "Download PowerPoint" }).click(),
    ]);
    const name = download.suggestedFilename();
    if (!name.endsWith(".pptx")) throw new SmokeFailure(`The download was ${name}, not a .pptx`);
    const file = path.join(temp, name);
    await download.saveAs(file);
    const head = (await readFile(file)).subarray(0, 2).toString("latin1");
    if (head !== "PK") throw new SmokeFailure("The .pptx is not a zip file");

    if (errors.length) throw new SmokeFailure(`The page threw: ${errors.join(" | ")}`);
    console.log(JSON.stringify({ status: "passed", download: name, bytes: (await stat(file)).size, unknownModelPrompts: unknown }, null, 2));
  } catch (error) {
    if (shots && page) await page.screenshot({ path: path.join(shots, "failed.png") }).catch(() => {});
    if (error instanceof SmokeFailure) throw error;
    throw new SmokeFailure(`${error instanceof Error ? error.stack : error}\n--- backend ---\n${logs()}\n--- unknown prompts ---\n${unknown.join("\n")}`);
  } finally {
    await browser?.close().catch(() => {});
    if (backend && backend.exitCode === null) {
      backend.kill("SIGINT");
      await delay(500);
      if (backend.exitCode === null) backend.kill("SIGKILL");
    }
    await closing(ui?.server);
    await closing(model?.server);
    await rm(temp, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
