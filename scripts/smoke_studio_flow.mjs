#!/usr/bin/env node

/**
 * Studio browser smoke checks local Setup, the planning action bar and preference fields,
 * approval, slide refinement and PowerPoint export. It restores an earlier deck version
 * after reloading the app and checks the counted summary in Report and Briefing doc.
 *
 * A loopback OpenAI-compatible fixture serves Studio's model replies. A disposable backend
 * and the production web bundle run locally, and Playwright's Chromium drives the UI.
 * The temporary backend data and web bundle are removed at the end.
 *
 * Run from the repository root with the project's dependencies installed.
 *
 *   UV_OFFLINE=1 node scripts/smoke_studio_flow.mjs --allow-write \
 *     [--screenshots-dir=/tmp/studio-shots]
 *
 * Needs `uv` on PATH and a Chromium: the one Playwright installed (the default), or set
 * CHROME_BIN to a Chrome/Chromium executable. Prints JSON with "status": "passed".
 */

import assert from "node:assert/strict";
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

const SOURCE = "The trial followed 412 buildings in six districts over the summers 2021 to 2023. " +
  "Raw cooling electricity fell 18%; adjusted for summer heat the saving was 9%. " +
  "The coating's albedo fell from 0.82 to 0.55 within 18 months.";
const FOCUS = "A 10-minute deck for the facilities team on what the trial shows";
const SUBTITLE = "The 18% saving is 9% after weather adjustment [1]";
const WRITER_NOTES = "Lead with the trial size.";
const PREFERENCE = "Explain the difference between the raw and weather-adjusted savings before " +
  "discussing the trial size. Keep the limits of the comparison visible when presenting each " +
  "figure, and connect the adjusted result to the decision the facilities team needs to make.";
const REFINEMENT = "Make slide 2 lead with the size";
const SECOND_HEADING = "The trial was large enough to trust the direction";
const REVISED_HEADING = "Three summers and 412 buildings back the result";

const DECK = `<!-- layout: title; design: v2; theme: paper -->
# Cool roofs work, but at half the headline saving
${SUBTITLE}

---

<!-- layout: stats -->
## ${SECOND_HEADING}
- **412** buildings in six districts [1]
- **3** summers, 2021–2023 [1]
Note: Size first.

---

<!-- layout: table -->
## Weather adjustment halves the saving
| Measure | Saving (%) |
| --- | --- |
| Raw | 18 [1] |
| Adjusted | 9 [1] |
Plan with the adjusted figure [1]
Note: The bars.
`;

const REWRITTEN = `<!-- layout: stats -->
## ${REVISED_HEADING}
- **412** buildings in six districts [1]
- **3** summers, 2021–2023 [1]
Note: Size.`;

const MEMO = {
  language: "English",
  answer: "Cool roofs save about 9% once weather is adjusted, not the 18% headline",
  insights: [
    { claim: "The saving halves after weather adjustment", so_what: "Budget with 9%, not 18%", refs: [1] },
    { claim: "The trial is large enough to trust the direction", so_what: "Size is not the weak point", refs: [1] },
  ],
  facts: [],
  tensions: [{ issue: "Long-term coating decay is only partly covered", refs: [1] }],
  questions: [],
  title: "Cool roofs",
};

const STORY = {
  title: "Cool roofs work, but at half the headline saving",
  subtitle: "What the trial shows",
  summary: "Explain the size and weather-adjusted results of the trial.",
  angle: "Use the measured saving for budget decisions.",
  coverage: [{ id: null, text: "Trial size and adjusted electricity savings" }],
  requested_minutes: 10,
  slides: [
    { title: "Cool roofs work, but at half the headline saving", layout: "title", job: "", refs: [] },
    { title: SECOND_HEADING, layout: "stats", job: "Show the size", refs: [1] },
    { title: "Weather adjustment halves the saving", layout: "table", job: "Show the two figures", refs: [1] },
  ],
};

const REPORT_STORY = {
  title: "Cool roof trial findings",
  summary_heading: "The weather-adjusted saving was 9%",
  summary_job: "State the result the facilities team can use",
  summary_notes: "Lead with the adjusted figure.",
  summary_refs: [1],
  summary_required_refs: [1],
  sections: [{ heading: "The trial covered six districts", job: "Describe the comparison", refs: [1] }],
};

const REPORT = `# ${REPORT_STORY.title}

## ${REPORT_STORY.summary_heading}
The weather-adjusted saving was 9% [1].

## ${REPORT_STORY.sections[0].heading}
The trial followed 412 buildings in six districts [1].`;

const unknown = [];
let deckDrafts = 0;
let reportDrafts = 0;

/** Match the role in the system message or Studio's task after the shared material. */
function reply(request) {
  const system = String(request.messages[0]?.content ?? "");
  const last = String(request.messages.at(-1)?.content ?? "");
  const task = last.split("\n\n── Your task now ──\n\n").at(-1);
  if (system.startsWith("You are the orchestrator of Gunther's Artifacts")) {
    const actions = last.includes("Mode: revise the active verified version.")
      ? [{ do: "write", parts: [2], instruction: "Lead with the size" }, { do: "save", text: "Rewrote slide 2." }]
      : [{ do: "plan", change: "whole" }];
    return JSON.stringify({ actions });
  }
  if (system.startsWith("You are Gunther, answering a question about")) return "It says so.";
  if (system.startsWith("You own the presentation purpose decision")) {
    return JSON.stringify({ purpose: "general", reason: "A briefing for the facilities team." });
  }
  if (task.startsWith("You are the lead analyst")) return JSON.stringify(MEMO);
  if (task.startsWith("You are a top presentation strategist")) return JSON.stringify(STORY);
  if (task.startsWith("You are the editor of a top consulting firm's research practice")) {
    return JSON.stringify(REPORT_STORY);
  }
  if (task.startsWith("You are a demanding senior partner")) {
    return JSON.stringify({ understood: "Savings halve once weather is adjusted", verdict: "ready", problems: [] });
  }
  if (system.includes("working on one slide of an existing")) return REWRITTEN;
  if (task.startsWith("You are the best presentation writer")) {
    deckDrafts += 1;
    return DECK;
  }
  if (task.startsWith("You are the best writer at a top consulting firm's research practice")) {
    reportDrafts += 1;
    return REPORT;
  }
  if (task.startsWith("A deck's subtitle is its core message")) return SUBTITLE;
  if (system.startsWith("Choose the visual style of a presentation")) return JSON.stringify({ style: "light", why: "Trial findings." });
  // The deck fixture uses Studio's Markdown layouts.
  if (system.startsWith("You are a world-class presentation designer")) return "<!-- Use the Markdown layout -->";
  if (system.includes("knowledge assertions")) return JSON.stringify({ assertions: [] });
  if (system.startsWith("Write a short library overview")) return JSON.stringify({ summary: SOURCE, suggested_questions: [] });
  if (system.startsWith("Keep up to five one-sentence findings")) return JSON.stringify({ items: [] });
  if (system.includes("sentence-checking step") || task.includes("sentence-checking step")) {
    const numbers = [...last.matchAll(/^\((\d+)\)/gm)].map((match) => Number(match[1]));
    return JSON.stringify({ sentences: numbers.map((n) => ({ n, verdict: "supported" })) });
  }
  unknown.push((task !== last ? task : system).slice(0, 160));
  return JSON.stringify({});
}

async function startModelServer() {
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      if (!request.url?.endsWith("/chat/completions")) {
        response.writeHead(404).end();
        return;
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      const answer = reply(body);
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

async function submitTurn(page, button, kind) {
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => {
      const request = candidate.request();
      return request.method() === "POST" && new URL(request.url()).pathname.endsWith("/messages/stream")
        && request.postDataJSON()?.kind === kind;
    }),
    button.click(),
  ]);
  if (!response.ok()) throw new SmokeFailure(`The ${kind} request failed (${response.status()}).`);
}

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
    model = await startModelServer();
    await mkdir(dist);
    ui = await serveStatic(dist);
    const backendPort = await freePort();
    const backendOrigin = `http://127.0.0.1:${backendPort}`;
    const uiOrigin = `http://127.0.0.1:${ui.port}`;
    backend = spawn("uv", ["run", "--project", "apps/backend", "uvicorn", "--app-dir", "apps/backend", "--factory", "gunther.main:create_app", "--host", "127.0.0.1", "--port", String(backendPort), "--log-level", "warning", "--no-access-log"], {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        CORS_ORIGINS: JSON.stringify([uiOrigin]),
        DATABASE_URL: `sqlite+pysqlite:///${path.join(data, "gunther.sqlite")}`,
        SERVICE_SETTINGS_FILE: path.join(data, "service-settings.json"),
        LIBRARY_ROOT: path.join(data, "Gunther"),
        ASSETS_DIR: path.join(data, "assets"),
        RECORDINGS_DIR: path.join(data, "recordings"),
        PREVIOUS_ASSETS_DIR: path.join(data, "assets"),
        PREVIOUS_RECORDINGS_DIR: path.join(data, "recordings"),
        SPEECH_DIR: path.join(data, "speech"),
        GUNTHER_DESKTOP_SIDECAR: "1",
        LLM_PROVIDER: "compatible",
        LLM_API_KEY: "fake-key",
        LLM_BASE_URL: `http://127.0.0.1:${model.port}/v1`,
        LLM_MODEL: "fake-model",
        AI_SUMMARIES: "off",
        SLIDE_REVIEW_ENABLED: "false",
        UV_CACHE_DIR: path.join(root, ".uv-cache"),
      },
    });
    logs = logsOf(backend);
    await waitForHttp(`${backendOrigin}/api/health`, backend, logs);

    const request = async (url, options = {}) => {
      const response = await fetch(`${backendOrigin}${url}`, { ...options, signal: AbortSignal.timeout(10_000) });
      if (!response.ok) throw new SmokeFailure(`${options.method ?? "GET"} ${url} -> ${response.status} ${await response.text()}`);
      return response;
    };
    const api = async (url, options) => (await request(url, options)).json();
    const { workspaceId } = await api("/api/workspace/bootstrap");
    const workspace = { "X-Gunther-Workspace-Id": workspaceId };
    const postOptions = (body) => ({
      method: "POST", headers: { ...workspace, "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const post = (url, body) => api(url, postOptions(body));
    const library = await post("/api/knowledge-bases", { title: "Cool roofs", question: "Do cool roofs save energy?", description: "Trial notes." });
    await post("/api/sources", { title: "Trial results", kind: "note", knowledgeBaseId: library.id, content: SOURCE });
    const libraryPath = `/api/knowledge-bases/${library.id}`;
    const versions = () => api(`${libraryPath}/artifacts`, { headers: workspace });
    const version = (id) => api(`${libraryPath}/artifacts/${id}`, { headers: workspace });
    const catalog = await api("/api/output-types");

    // A fresh first build must start with a Studio plan.
    const legacy = await request(`${libraryPath}/outputs/build/stream`, postOptions({
      kind: "slides", audience: "scientist", brief: FOCUS, clientRequestId: "smoke_legacy_first_build",
    }));
    const denied = await legacy.text();
    assert.match(denied, /^event: error\r?$/m);
    const deniedData = denied.split(/\r?\n/).find((line) => line.startsWith("data: "));
    assert.ok(deniedData, "The legacy build error did not include SSE data.");
    const { status, code } = JSON.parse(deniedData.slice("data: ".length));
    assert.deepEqual({ status, code }, { status: 409, code: "plan_required" });
    assert.equal(deckDrafts, 0, "The rejected legacy build called the writer.");

    await run(process.execPath, [viteBin, "build", "--outDir", dist, "--emptyOutDir"], { cwd: desktopRoot, env: { ...process.env, VITE_API_URL: backendOrigin } });

    const executablePath = process.env.CHROME_BIN ?? await installedChromium();
    browser = await chromium.launch({
      ...(executablePath ? { executablePath } : {}),
      args: process.getuid?.() === 0 ? ["--no-sandbox"] : [],
    });
    const viewport = { width: 1440, height: 900 };
    const context = await browser.newContext({ viewport, acceptDownloads: true });
    page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const shot = async (name) => { if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`) }); };

    await page.goto(uiOrigin);
    await page.getByRole("button", { name: "Libraries", exact: true }).click();
    await page.getByRole("button", { name: "Cool roofs", exact: true }).first().click();
    await page.getByRole("tab", { name: "Studio", exact: true }).click();

    const studioMutations = [];
    const turns = [];
    page.on("request", (request) => {
      const pathname = new URL(request.url()).pathname;
      if (!["POST", "PATCH", "DELETE"].includes(request.method()) || !pathname.startsWith(`${libraryPath}/`)) return;
      const route = pathname.slice(libraryPath.length);
      if (!/^\/(artifact-conversations|artifacts|outputs)(\/|$)/.test(route)) return;
      studioMutations.push(route);
      if (route.endsWith("/messages/stream")) turns.push({ pathname, body: request.postDataJSON() });
    });

    const studio = page.getByRole("complementary", { name: "Studio", exact: true });
    const planning = page.getByRole("article", { name: "Output planning" });
    const approve = planning.getByRole("button", { name: "Approve & Generate", exact: true });
    const reader = page.getByRole("article", { name: "Open output" });
    const planButton = planning.getByRole("button", { name: "Plan", exact: true });
    const readPlan = async ({ pathname, body }) => {
      const conversationRoute = pathname.slice(0, -"/messages/stream".length);
      const detail = await api(conversationRoute, { headers: workspace });
      const work = detail.works.find(({ plan }) => plan?.planId === body.planId);
      assert.equal(work?.plan.planStatus, "ready");
      return { detail, work, workRoute: `${conversationRoute}/works/${work.key}` };
    };

    // Setup stays on the device. Planning reads the evidence and proposes the outline.
    await studio.getByRole("button", { name: "Slides", exact: true }).click();
    await planning.getByRole("heading", { name: "Set up Slides", exact: true }).waitFor();
    await planning.getByRole("textbox", { name: "Focus", exact: true }).fill(FOCUS);
    const newPreference = planning.getByRole("textbox", { name: "New item for What to emphasize", exact: true });
    await newPreference.fill(PREFERENCE);
    await newPreference.press("Enter");
    assert.deepEqual(studioMutations, [], "Setup sent a Studio mutation.");
    assert.deepEqual(await versions(), [], "Setup published an output.");
    await shot("1-setup");

    await submitTurn(page, planButton, "plan");
    await approve.waitFor({ timeout: 90_000 });
    assert.equal(await planning.getByRole("textbox", { name: "Heading 2", exact: true }).inputValue(), SECOND_HEADING);
    const [initial] = turns;
    assert.deepEqual(turns.map(({ body }) => body.kind), ["plan"]);
    assert.deepEqual(studioMutations, [initial.pathname.slice(libraryPath.length)]);
    assert.equal(initial.body.setup.focus, FOCUS);
    assert.equal(initial.body.setup.emphasize[0].text, PREFERENCE);
    assert.deepEqual(await versions(), [], "Planning published an output.");
    assert.equal(deckDrafts, 0, "Planning called the deck writer.");
    await shot("2-plan");

    await page.setViewportSize({ width: 720, height: 580 });
    const preference = planning.getByRole("textbox", { name: "What to emphasize, item 1", exact: true });
    const dimensions = await preference.evaluate((field) => {
      const style = getComputedStyle(field);
      const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      return {
        lines: (field.clientHeight - padding) / parseFloat(style.lineHeight),
        horizontalOverflow: field.scrollWidth - field.clientWidth,
      };
    });
    assert.ok(dimensions.lines > 2, "The preference field did not grow to fit the wrapped text.");
    assert.ok(dimensions.horizontalOverflow <= 1, "The preference field scrolls horizontally.");
    const footer = await planning.locator(".plan-approval").evaluate((bar) => {
      const scroller = bar.closest(".library-middle-page");
      const bottom = scroller.getBoundingClientRect().bottom;
      scroller.scrollTop = 0;
      const atTop = bar.getBoundingClientRect().bottom;
      scroller.scrollTop = (scroller.scrollHeight - scroller.clientHeight) / 2;
      return { bottom, atTop, midScroll: bar.getBoundingClientRect().bottom, offset: scroller.scrollTop };
    });
    assert.ok(footer.offset > 0, "The plan did not scroll at the narrow viewport.");
    assert.ok(Math.abs(footer.bottom - footer.atTop) <= 2 && Math.abs(footer.bottom - footer.midScroll) <= 2,
      "The planning action bar did not stay at the bottom of the scroller.");
    await page.setViewportSize(viewport);

    const { detail: planned, work: { plan: firstPlan }, workRoute } = await readPlan(initial);
    const notes = planning.getByRole("textbox", { name: "Writer notes 2", exact: true });
    await notes.fill(WRITER_NOTES);
    const [patch] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "PATCH" && new URL(response.url()).pathname === workRoute),
      notes.press("Tab"),
    ]);
    assert.ok(patch.ok(), `Saving the writer notes failed (${patch.status()}).`);
    assert.equal(patch.request().postDataJSON().baseEditVersion, planned.editVersion);
    const work = (await patch.json()).works.find(({ plan }) => plan?.planId === initial.body.planId);
    assert.ok(work, "The edited plan was missing from the PATCH response.");
    assert.equal(work.plan.planStatus, "ready");
    assert.notEqual(work.plan.planRevision, firstPlan.planRevision);
    assert.equal(work.plan.outline[1].notes, WRITER_NOTES);
    await submitTurn(page, approve, "approve");

    await reader.getByText("Slide 1 of 3", { exact: true }).waitFor();
    assert.deepEqual(turns.map(({ body }) => body.kind), ["plan", "approve"]);
    assert.equal(turns[1].pathname, initial.pathname);
    assert.equal(turns[1].body.planId, initial.body.planId);
    assert.equal(turns[1].body.planRevision, work.plan.planRevision);
    assert.equal(deckDrafts, 1, "Approval should call the deck writer once.");
    const saved = await versions();
    assert.equal(saved.length, 1);
    assert.equal(saved[0].versionNumber, 1);
    const original = await version(saved[0].id);
    assert.equal(original.provenance.creationPlan.revision, work.plan.planRevision);
    assert.deepEqual(original.provenance.creationPlan.outline, work.plan.outline);
    await shot("3-deck");

    await reader.getByText("Approved plan", { exact: true }).click();
    await reader.getByText(WRITER_NOTES).waitFor();
    await shot("4-approved-plan");
    await reader.getByText("Approved plan", { exact: true }).click();

    // Refine the selected slide, then read back the new head of the same lineage.
    await reader.getByRole("button", { name: "Go to slide 2", exact: true }).click();
    await reader.getByText("Slide 2 of 3", { exact: true }).waitFor();
    await reader.getByRole("button", { name: "Ask about this slide", exact: true }).click();
    await reader.getByRole("textbox", { name: "Refine", exact: true }).fill(REFINEMENT);
    await submitTurn(page, reader.getByRole("button", { name: "Refine", exact: true }), "message");
    await reader.locator(".outputs-stage").getByText(REVISED_HEADING, { exact: true }).waitFor();
    assert.deepEqual(turns.map(({ body }) => body.kind), ["plan", "approve", "message"]);
    assert.equal(turns[2].pathname, initial.pathname);
    assert.equal(turns[2].body.targetLineageId, original.lineageId);
    assert.equal(turns[2].body.focus.section, 2);
    const revised = await versions();
    assert.equal(revised.length, 2);
    assert.equal(revised.find(({ versionNumber }) => versionNumber === 2)?.lineageId, original.lineageId);
    await shot("5-slide-changed");

    await reader.getByRole("button", { name: "Output actions", exact: true }).click();
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

    // Seed Trash with version 1 while version 2 is the head, then restore through the app.
    const moved = await post(`${libraryPath}/artifacts/${original.id}/trash`, {});
    assert.equal(moved.kind, "output");
    assert.equal(moved.versionCount, 2);
    assert.deepEqual(moved.open, {
      view: "library", tab: "outputs", libraryId: library.id, conversationId: original.conversationId,
      typeId: "slides", planId: initial.body.planId, lineageId: original.lineageId, artifactId: original.id,
    });
    assert.deepEqual(await versions(), []);

    await page.reload();
    const entries = await api("/api/trash");
    assert.deepEqual(entries.find(({ id }) => id === moved.id)?.open, moved.open);
    await page.getByRole("button", { name: "Trash", exact: true }).click();
    const restorePath = `/api/trash/output/${moved.id}/restore`;
    const [restoredResponse] = await Promise.all([
      page.waitForResponse((response) =>
        response.request().method() === "POST" && new URL(response.url()).pathname === restorePath),
      page.getByRole("button", { name: `Restore “${moved.title}”`, exact: true }).click(),
    ]);
    assert.ok(restoredResponse.ok(), `Restore failed (${restoredResponse.status()}).`);
    assert.deepEqual((await restoredResponse.json()).open, moved.open);
    await reader.getByRole("button", { name: "Go to slide 2", exact: true }).click();
    await reader.locator(".outputs-stage").getByText(SECOND_HEADING, { exact: true }).waitFor();
    assert.deepEqual(turns.map(({ body }) => body.kind), ["plan", "approve", "message"]);
    assert.equal((await versions()).length, 2);

    // Read the summary from the planned and sealed rows and from the decoded reader.
    await page.getByRole("tab", { name: "Studio", exact: true }).click();
    for (const typeId of ["report", "briefing"]) {
      const type = catalog.find(({ id }) => id === typeId);
      assert.equal(type?.firstPartRole, "Summary section");
      const before = (await versions()).length;
      const writesBefore = reportDrafts;
      await studio.getByRole("button", { name: type.label, exact: true }).click();
      await planning.getByRole("heading", { name: `Set up ${type.label}`, exact: true }).waitFor();
      await submitTurn(page, planButton, "plan");
      await approve.waitFor({ timeout: 90_000 });
      await planning.getByText(type.firstPartRole, { exact: true }).waitFor();
      const proposed = turns.at(-1);
      assert.equal(proposed.body.setup.type, typeId);
      const { detail, work: { plan } } = await readPlan(proposed);
      const [summary, body] = plan.outline;
      assert.deepEqual(plan.proposal.count, { mode: "exact", value: 2 });
      assert.ok(summary.id && summary.id !== body.id, "The summary needs its own row ID.");
      assert.equal(summary.heading, REPORT_STORY.summary_heading);
      assert.equal(summary.goal, REPORT_STORY.summary_job);
      assert.equal(summary.notes, REPORT_STORY.summary_notes);
      assert.equal((await versions()).length, before, "Planning published a report.");
      assert.equal(reportDrafts, writesBefore, "Planning called the report writer.");

      await submitTurn(page, approve, "approve");
      const report = reader.locator(`.rp[data-recipe="${typeId}"]`);
      const firstSection = report.locator(".rp-part").first();
      await firstSection.getByRole("heading", { name: summary.heading, exact: true }).waitFor();
      assert.ok(await firstSection.locator(".rp-summary").isVisible());
      assert.equal(await report.locator(".rp-part").count(), plan.proposal.count.value);
      const approved = turns.at(-1).body;
      assert.equal(approved.planId, proposed.body.planId);
      assert.equal(approved.planRevision, plan.planRevision);
      assert.equal(reportDrafts, writesBefore + 1, "Approval should call the report writer once.");
      const savedReport = (await versions()).find(({ conversationId }) => conversationId === detail.id);
      assert.ok(savedReport, `The ${type.label} was missing from the saved outputs.`);
      const artifact = await version(savedReport.id);
      const sealed = artifact.provenance.creationPlan;
      assert.equal(sealed.revision, plan.planRevision);
      assert.deepEqual(sealed.proposal.count, plan.proposal.count);
      assert.deepEqual(sealed.outline, plan.outline);
      assert.equal(artifact.outline[0].id, summary.id);
      assert.deepEqual(artifact.sections.map(({ heading }) => heading), plan.outline.map(({ heading }) => heading));
      assert.ok(artifact.content.includes(`## ${summary.heading}\n<!-- block: summary -->`));
    }

    if (errors.length) throw new SmokeFailure(`The page threw: ${errors.join(" | ")}`);
    assert.deepEqual(unknown, [], "The model fixture received unexpected prompts.");
    console.log(JSON.stringify({
      status: "passed", versions: (await versions()).length, download: name,
      bytes: (await stat(file)).size, unknownModelPrompts: unknown,
    }, null, 2));
  } catch (error) {
    if (shots && page) await page.screenshot({ path: path.join(shots, "failed.png") }).catch(() => {});
    const title = await page?.locator(".output-reader-heading > h1, .plan-heading > h1").allTextContents().catch(() => []);
    const alerts = await page?.getByRole("alert").allTextContents().catch(() => []);
    throw new SmokeFailure(`${error instanceof Error ? error.stack : error}\nReader: ${title?.join(" | ") ?? ""}\nAlerts: ${alerts?.join(" | ") ?? ""}\n--- backend ---\n${logs()}\n--- unknown prompts ---\n${unknown.join("\n")}`);
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
