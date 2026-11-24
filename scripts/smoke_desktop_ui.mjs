#!/usr/bin/env node

/**
 * Exercise the production desktop web bundle against a disposable real backend.
 *
 * The smoke deliberately avoids the user's 8787 service and data directory. It
 * builds to a temporary output directory, serves only on loopback, drives a real
 * Chromium page over the DevTools protocol, and removes every temporary asset.
 */

import { spawn } from "node:child_process";
import { constants as fsConstants } from "node:fs";
import {
  access,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const desktopRoot = path.join(root, "apps", "desktop");
const viteBin = path.join(root, "node_modules", "vite", "bin", "vite.js");
const maxLogBytes = 96 * 1024;

class SmokeFailure extends Error {
  constructor(message) {
    super(message);
    this.name = "SmokeFailure";
  }
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function boundedLog(child) {
  let output = "";
  const append = (chunk) => {
    output += chunk.toString("utf8");
    if (Buffer.byteLength(output) > maxLogBytes) {
      output = output.slice(-maxLogBytes);
    }
  };
  child.stdout?.on("data", append);
  child.stderr?.on("data", append);
  return () => output;
}

async function waitForExit(child, timeoutMilliseconds) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    delay(timeoutMilliseconds),
  ]);
}

async function stopChild(child, firstSignal = "SIGINT") {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill(firstSignal);
  await waitForExit(child, 5_000);
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  await waitForExit(child, 2_000);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await waitForExit(child, 2_000);
  }
}

async function runCommand(command, arguments_, options) {
  const child = spawn(command, arguments_, {
    ...options,
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const logs = boundedLog(child);
  const code = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (exitCode, signal) => {
      if (signal) reject(new SmokeFailure(`${command} exited on ${signal}\n${logs()}`));
      else resolve(exitCode);
    });
  });
  if (code !== 0) {
    throw new SmokeFailure(`${command} exited ${code}\n${logs()}`);
  }
  return logs();
}

async function reserveLoopbackPort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new SmokeFailure("Could not reserve a loopback port");
  }
  const { port } = address;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".woff2", "font/woff2"],
]);

async function startStaticServer(distDirectory) {
  const rootWithSeparator = `${path.resolve(distDirectory)}${path.sep}`;
  const server = http.createServer(async (request, response) => {
    try {
      if (request.method !== "GET" && request.method !== "HEAD") {
        response.writeHead(405, { Allow: "GET, HEAD" }).end();
        return;
      }
      const requestUrl = new URL(request.url ?? "/", "http://127.0.0.1");
      let pathname;
      try {
        pathname = decodeURIComponent(requestUrl.pathname);
      } catch {
        response.writeHead(400).end();
        return;
      }
      const relative = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
      let candidate = path.resolve(distDirectory, relative);
      if (candidate !== path.resolve(distDirectory) && !candidate.startsWith(rootWithSeparator)) {
        response.writeHead(403).end();
        return;
      }
      let metadata = await stat(candidate).catch(() => null);
      if (!metadata?.isFile() && !path.extname(relative)) {
        candidate = path.join(distDirectory, "index.html");
        metadata = await stat(candidate).catch(() => null);
      }
      if (!metadata?.isFile()) {
        response.writeHead(404).end();
        return;
      }
      const body = await readFile(candidate);
      response.writeHead(200, {
        "Cache-Control": "no-store",
        "Content-Length": body.byteLength,
        "Content-Type": contentTypes.get(path.extname(candidate)) ?? "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
      });
      response.end(request.method === "HEAD" ? undefined : body);
    } catch {
      response.writeHead(500).end();
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new SmokeFailure("Static server did not publish a loopback port");
  }
  return { server, port: address.port };
}

async function closeServer(server) {
  if (!server) return;
  await new Promise((resolve) => server.close(() => resolve()));
}

async function waitForHttp(url, child, logs, timeoutMilliseconds = 20_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  let lastError = "not ready";
  while (Date.now() < deadline) {
    if (child && (child.exitCode !== null || child.signalCode !== null)) {
      throw new SmokeFailure(`Service exited before ${url} became ready\n${logs()}`);
    }
    try {
      const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(1_500) });
      if (response.ok) return response;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await delay(100);
  }
  throw new SmokeFailure(`Timed out waiting for ${url}: ${lastError}\n${logs()}`);
}

async function findChrome() {
  const candidates = [
    process.env.CHROME_BIN,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      await access(candidate, fsConstants.X_OK);
      return candidate;
    } catch {
      // Try the next explicit executable.
    }
  }
  throw new SmokeFailure("Chrome/Chromium was not found; set CHROME_BIN to an executable");
}

async function waitForChromePage(profileDirectory, chrome, logs) {
  const activePortFile = path.join(profileDirectory, "DevToolsActivePort");
  const deadline = Date.now() + 15_000;
  let askedForPage = false;
  while (Date.now() < deadline) {
    if (chrome.exitCode !== null || chrome.signalCode !== null) {
      throw new SmokeFailure(`Chrome exited before DevTools was ready\n${logs()}`);
    }
    try {
      const [portLine] = (await readFile(activePortFile, "utf8")).trim().split(/\r?\n/);
      const port = Number.parseInt(portLine, 10);
      if (Number.isInteger(port) && port > 0 && port <= 65_535) {
        const targets = await jsonFrom(`http://127.0.0.1:${port}/json/list`);
        const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
        if (page) return page.webSocketDebuggerUrl;
        // Chrome sometimes never opens its first tab; after a wait, ask DevTools for one, once.
        if (!askedForPage && Date.now() > deadline - 12_000) {
          askedForPage = true;
          const opened = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: "PUT" });
          const created = opened.ok ? await opened.json() : null;
          if (created?.type === "page" && created.webSocketDebuggerUrl) return created.webSocketDebuggerUrl;
        }
      }
    } catch {
      // The debugging socket and the page target can become ready separately.
    }
    await delay(100);
  }
  throw new SmokeFailure(`Chrome DevTools did not expose a page target\n${logs()}`);
}

class CdpClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new SmokeFailure("CDP connection timed out")), 10_000);
      this.socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
      this.socket.addEventListener("error", () => {
        clearTimeout(timer);
        reject(new SmokeFailure("CDP connection failed"));
      }, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new SmokeFailure(`CDP ${message.error.message}`));
        else pending.resolve(message.result ?? {});
        return;
      }
      const listeners = this.listeners.get(message.method) ?? [];
      for (const listener of listeners) listener(message.params ?? {});
    });
  }

  on(method, listener) {
    this.listeners.set(method, [...(this.listeners.get(method) ?? []), listener]);
  }

  send(method, params = {}) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new SmokeFailure("CDP socket is not open"));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      const description = result.exceptionDetails.exception?.description
        ?? result.exceptionDetails.text
        ?? "browser evaluation failed";
      throw new SmokeFailure(description);
    }
    return result.result?.value;
  }

  close() {
    this.socket?.close();
  }
}

async function waitForEvaluation(cdp, expression, description, timeoutMilliseconds = 15_000) {
  const deadline = Date.now() + timeoutMilliseconds;
  let lastError = "";
  while (Date.now() < deadline) {
    try {
      if (await cdp.evaluate(expression)) return;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await delay(100);
  }
  throw new SmokeFailure(`Timed out waiting for ${description}${lastError ? `: ${lastError}` : ""}`);
}

const clickExpression = (selector, text) => `(() => {
  const elements = [...document.querySelectorAll(${JSON.stringify(selector)})];
  const element = elements.find((candidate) => {
    const visible = candidate.getClientRects().length > 0;
    const ariaLabel = (candidate.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
    const label = (candidate.textContent || '').replace(/\\s+/g, ' ').trim();
    const strongLabel = (candidate.querySelector('strong')?.textContent || '').replace(/\\s+/g, ' ').trim();
    const headingLabel = (candidate.querySelector('h1, h2, h3')?.textContent || '').replace(/\\s+/g, ' ').trim();
    return visible && !candidate.disabled && [ariaLabel, label, strongLabel, headingLabel].includes(${JSON.stringify(text)});
  });
  if (!element) return false;
  element.click();
  return true;
})()`;

const setAriaValueExpression = (ariaLabel, value) => `(() => {
  const element = document.querySelector('[aria-label=${JSON.stringify(ariaLabel)}]');
  if (!element) return false;
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, ${JSON.stringify(value)});
  element.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`;

const setLabeledValueExpression = (labelText, value) => `(() => {
  const label = [...document.querySelectorAll('label')].find((candidate) => {
    const heading = candidate.querySelector(':scope > span');
    return heading && heading.textContent.trim() === ${JSON.stringify(labelText)};
  });
  const element = label && label.querySelector('input, textarea');
  if (!element) return false;
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, ${JSON.stringify(value)});
  element.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})()`;

async function jsonFrom(url) {
  const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(5_000) });
  if (!response.ok) throw new SmokeFailure(`${url} returned ${response.status}`);
  return response.json();
}

async function assertPortReleased(port) {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  await new Promise((resolve) => server.close(resolve));
}

async function run() {
  if (!process.argv.includes("--allow-write")) {
    throw new SmokeFailure("--allow-write is required for the disposable UI smoke");
  }
  await access(viteBin, fsConstants.R_OK);
  const chromeBinary = await findChrome();
  const temporaryRoot = await realpath(await mkdtemp(path.join(os.tmpdir(), "gunther-desktop-ui-smoke.")));
  const dataDirectory = path.join(temporaryRoot, "data");
  const libraryRoot = path.join(dataDirectory, "Gunther");
  const distDirectory = path.join(temporaryRoot, "dist");
  const chromeProfile = path.join(temporaryRoot, "chrome");
  const screenshotsArgument = process.argv.find((arg) => arg.startsWith("--screenshots-dir="));
  const screenshotsDirectory = screenshotsArgument ? path.resolve(screenshotsArgument.slice("--screenshots-dir=".length)) : null;
  const screenshot = async (name) => {
    if (!screenshotsDirectory) return;
    await mkdir(screenshotsDirectory, { recursive: true });
    await waitForEvaluation(cdp, "!document.querySelector('.atlas-toast')", "notifications settling before the layout screenshot");
    await cdp.evaluate("new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png" });
    await writeFile(path.join(screenshotsDirectory, `${name}.png`), Buffer.from(data, "base64"));
  };
  await Promise.all([mkdir(dataDirectory), mkdir(chromeProfile)]);

  let backend;
  let backendLogs = () => "";
  let chrome;
  let chromeLogs = () => "";
  let staticServer;
  let cdp;
  let backendPort;
  let uiPort;
  let result;
  let failure;

  try {
    const staticHandle = await startStaticServer(distDirectory);
    staticServer = staticHandle.server;
    uiPort = staticHandle.port;
    backendPort = await reserveLoopbackPort();
    const backendOrigin = `http://127.0.0.1:${backendPort}`;
    const uiOrigin = `http://127.0.0.1:${uiPort}`;

    const backendEnvironment = {
      ...process.env,
      CORS_ORIGINS: JSON.stringify([uiOrigin]),
      DATABASE_URL: `sqlite+pysqlite:///${path.join(dataDirectory, "gunther.sqlite")}`,
      ASSETS_DIR: path.join(dataDirectory, "assets"),
      RECORDINGS_DIR: path.join(dataDirectory, "recordings"),
      PREVIOUS_ASSETS_DIR: path.join(dataDirectory, "assets"),
      PREVIOUS_RECORDINGS_DIR: path.join(dataDirectory, "recordings"),
      SPEECH_DIR: path.join(dataDirectory, "speech"),
      SLIDE_RENDER_DIR: path.join(dataDirectory, "slide-render"),
      GUNTHER_DESKTOP_SIDECAR: "1",
      PYTHONPATH: path.join(root, "apps", "backend"),
      DEEPSEEK_API_KEY: "",
      LLM_API_KEY: "",
      TAVILY_API_KEY: "",
      // Keys saved in the developer's Settings stay out of the smoke.
      SERVICE_SETTINGS_FILE: path.join(dataDirectory, "service-settings.json"),
      // Originals and readable library folders live in a root inside the smoke's data.
      LIBRARY_ROOT: libraryRoot,
      UV_CACHE_DIR: path.join(root, ".uv-cache"),
    };
    backend = spawn("uv", [
      "run", "--project", "apps/backend", "uvicorn",
      "--app-dir", "apps/backend/tests", "--factory", "desktop_smoke_backend:create_smoke_app",
      "--host", "127.0.0.1", "--port", String(backendPort),
      "--log-level", "warning", "--no-access-log",
    ], {
      cwd: root,
      env: backendEnvironment,
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
    });
    backendLogs = boundedLog(backend);
    await waitForHttp(`${backendOrigin}/api/health`, backend, backendLogs);

    await runCommand("npm", ["run", "typecheck", "--workspace", "@gunther/desktop"], {
      cwd: root,
      env: process.env,
    });
    await runCommand(process.execPath, [
      viteBin,
      "build",
      "--outDir", distDirectory,
      "--emptyOutDir",
    ], {
      cwd: desktopRoot,
      env: { ...process.env, VITE_API_URL: backendOrigin },
    });
    await waitForHttp(uiOrigin, null, () => "");

    chrome = spawn(chromeBinary, [
      "--headless=new",
      // Containers commonly run the smoke as root, where Chrome requires this flag.
      ...(process.getuid?.() === 0 ? ["--no-sandbox"] : []),
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-gpu",
      "--disable-sync",
      "--no-default-browser-check",
      "--no-first-run",
      "--remote-debugging-address=127.0.0.1",
      "--remote-debugging-port=0",
      `--user-data-dir=${chromeProfile}`,
      "about:blank",
    ], {
      cwd: root,
      env: process.env,
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
    });
    chromeLogs = boundedLog(chrome);
    cdp = new CdpClient(await waitForChromePage(chromeProfile, chrome, chromeLogs));
    await cdp.connect();
    const browserErrors = [];
    cdp.on("Runtime.exceptionThrown", ({ exceptionDetails }) => {
      browserErrors.push(exceptionDetails?.exception?.description ?? exceptionDetails?.text ?? "Uncaught browser error");
    });
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const resizeViewport = (width, height) => cdp.send("Emulation.setDeviceMetricsOverride", {
      width, height, deviceScaleFactor: 1, mobile: false,
    });
    const wideViewport = () => resizeViewport(1280, 820);
    await wideViewport();
    await cdp.send("Page.navigate", { url: uiOrigin });
    await waitForEvaluation(
      cdp,
      "document.readyState === 'complete' && document.querySelector('[aria-label=\"Search your knowledge or the web\"]') !== null",
      "Gunther search-first Home",
    );

    const marker = `ui-smoke-${crypto.randomUUID().slice(0, 8)}`;
    const noteTitle = `Capture before organize ${marker}`;
    const libraryTitle = `Bioinformatics ${marker}`;
    const libraryChatOpen = `Boolean(
      document.querySelector('.library-workspace .contents[aria-label="Sources"]') &&
      document.querySelector('.library-workspace .library-chat:not([hidden]) [aria-label="Conversation"]') &&
      document.querySelector('.library-workspace [aria-label="Studio"]')
    )`;
    const libraryWelcome = `${libraryChatOpen} && document.querySelector('.chat-welcome h1')?.textContent.trim() === ${JSON.stringify(libraryTitle)}`;
    if (!await cdp.evaluate(clickExpression("button", "All capture options"))) {
      throw new SmokeFailure("All capture options was not clickable");
    }
    await waitForEvaluation(cdp, "document.querySelector('[role=dialog][aria-label=\"Capture something\"]') !== null", "Capture dialog");
    // Capture opens ready to write a note; the type bar still selects it explicitly.
    if (!await cdp.evaluate(clickExpression("[role=tab]", "Note"))) {
      throw new SmokeFailure("The Note capture type was not selectable");
    }
    if (!await cdp.evaluate(setAriaValueExpression("Title", noteTitle))) {
      throw new SmokeFailure("Quick note title input was not found");
    }
    if (!await cdp.evaluate(setAriaValueExpression("Source content", `BRCA1 regulates DNA repair.\nPreserved by ${marker}.`))) {
      throw new SmokeFailure("Quick note content input was not found");
    }
    await waitForEvaluation(cdp, "[...document.querySelectorAll('button')].some((button) => button.getAttribute('aria-label') === 'Save note' && !button.disabled)", "enabled Save note");
    if (!await cdp.evaluate(clickExpression("button", "Save note"))) {
      throw new SmokeFailure("Save note was not clickable");
    }
    await waitForEvaluation(cdp, "document.querySelector('[role=dialog][aria-label=\"Capture something\"]') === null", "closed Capture after note save");
    // Capture preserves the user's current workspace instead of forcing a
    // navigation. Open Inbox explicitly to verify the durable result.
    if (!await cdp.evaluate(clickExpression("button", "Inbox"))) {
      throw new SmokeFailure("Inbox navigation was not clickable after Capture save");
    }
    await waitForEvaluation(cdp, `document.body.innerText.includes(${JSON.stringify(noteTitle)}) && document.body.innerText.includes('Choose a home')`, "captured note in Inbox");

    if (!await cdp.evaluate(clickExpression("button", "Libraries"))) {
      throw new SmokeFailure("Library navigation was not clickable");
    }
    await waitForEvaluation(cdp, "document.querySelector('.gx-libraries h1')?.textContent.trim() === 'Libraries'", "Libraries view");
    if (!await cdp.evaluate(clickExpression("button", "New library"))) {
      throw new SmokeFailure("New library was not clickable");
    }
    await waitForEvaluation(cdp, "document.querySelector('[role=dialog][aria-label=\"Create library\"]') !== null", "library dialog");
    for (const [label, value] of [
      ["Name", libraryTitle],
      ["Guiding question", "How do computational methods explain biological systems?"],
      ["Description", `A disposable browser-driven knowledge base for ${marker}.`],
    ]) {
      if (!await cdp.evaluate(setLabeledValueExpression(label, value))) {
        throw new SmokeFailure(`${label} field was not found`);
      }
    }
    await waitForEvaluation(cdp, "[...document.querySelectorAll('button')].some((button) => button.textContent.trim() === 'Create library' && !button.disabled)", "enabled Create library");
    if (!await cdp.evaluate(clickExpression("button", "Create library"))) {
      throw new SmokeFailure("Create library was not clickable");
    }
    await waitForEvaluation(cdp, libraryWelcome, "new library with Sources, Chat and Studio");
    if (!await cdp.evaluate(clickExpression(".contents-add button", "Add source"))
      || !await cdp.evaluate(clickExpression('[role="menuitem"]', "Note or pasted text"))) {
      throw new SmokeFailure("Sources could not start a note capture");
    }
    await waitForEvaluation(
      cdp,
      `(() => {
        const capture = document.querySelector('[role="dialog"][aria-label="Capture something"]');
        return capture?.querySelector('#capture-kind-note[aria-selected="true"]')
          && capture.querySelector('.gx-picker-trigger')?.textContent.includes(${JSON.stringify(libraryTitle)});
      })()`,
      "note capture targeting the open library",
    );
    if (!await cdp.evaluate(clickExpression("button", "Close capture"))) {
      throw new SmokeFailure("The note capture could not be closed");
    }

    if (!await cdp.evaluate(clickExpression("button", "Inbox"))) {
      throw new SmokeFailure("Inbox navigation was not clickable");
    }
    await waitForEvaluation(cdp, `document.body.innerText.includes(${JSON.stringify(noteTitle)})`, "captured note after returning to Inbox");
    const noteCard = `[...document.querySelectorAll('article.state-unfiled')].find((article) => article.querySelector('h2')?.textContent === ${JSON.stringify(noteTitle)})`;
    // Inbox files through the library picker: open it on the note's row, choose the library, then file.
    const openPickerExpression = `(() => {
      const trigger = ${noteCard}?.querySelector('.gx-picker-trigger');
      if (!trigger || trigger.disabled) return false;
      trigger.click();
      return true;
    })()`;
    if (!await cdp.evaluate(openPickerExpression)) throw new SmokeFailure("The Inbox library picker was not available");
    await waitForEvaluation(
      cdp,
      `[...document.querySelectorAll('[role=option]')].some((option) => option.querySelector('strong')?.textContent === ${JSON.stringify(libraryTitle)})`,
      "library picker options",
    );
    const chooseLibraryExpression = `(() => {
      const option = [...document.querySelectorAll('[role=option]')].find((candidate) => candidate.querySelector('strong')?.textContent === ${JSON.stringify(libraryTitle)});
      if (!option) return false;
      option.click();
      return true;
    })()`;
    if (!await cdp.evaluate(chooseLibraryExpression)) throw new SmokeFailure("The new library could not be chosen");
    await waitForEvaluation(
      cdp,
      `(${noteCard}?.querySelector('.gx-picker-trigger')?.getAttribute('aria-label') || '').includes(${JSON.stringify(libraryTitle)})`,
      "chosen library on the Inbox row",
    );
    const fileExpression = `(() => {
      const card = ${noteCard};
      const button = card && [...card.querySelectorAll('button')].find((candidate) => candidate.textContent.replace(/\\s+/g, ' ').trim() === 'File source');
      if (!button || button.disabled) return false;
      button.click();
      return true;
    })()`;
    if (!await cdp.evaluate(fileExpression)) throw new SmokeFailure("File source action was not available");
    await waitForEvaluation(
      cdp,
      `![...document.querySelectorAll('article.state-unfiled')].some((article) => article.querySelector('h2')?.textContent === ${JSON.stringify(noteTitle)})`,
      "note filing completion",
    );

    const bases = await jsonFrom(`${backendOrigin}/api/knowledge-bases`);
    const knowledgeBase = bases.find((item) => item.title === libraryTitle);
    if (!knowledgeBase) throw new SmokeFailure("Created knowledge base was not persisted");
    const sources = await jsonFrom(`${backendOrigin}/api/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/sources`);
    const source = sources.find((item) => item.title === noteTitle);
    if (!source) throw new SmokeFailure("Filed note did not become a knowledge-base source");
    const filedNotes = await jsonFrom(`${backendOrigin}/api/notes?status=filed`);
    const filedNote = filedNotes.find((item) => item.title === noteTitle);
    if (!filedNote?.promotedSourceId || filedNote.promotedSourceId !== source.id) {
      throw new SmokeFailure("Filed note history did not retain its promoted source identity");
    }
    // The library is also a readable folder on disk, listing the filed source.
    const storage = await jsonFrom(`${backendOrigin}/api/storage`);
    if (!storage.foldersEnabled || storage.libraryRoot !== libraryRoot) {
      throw new SmokeFailure(`Library folders were not in use: ${JSON.stringify(storage)}`);
    }
    const libraryDirectory = path.join(libraryRoot, "Libraries", libraryTitle);
    let exportedSource = null;
    let exportPending = "The source was not listed in library.json";
    // The folder sync writes the library listing before the individual source files.
    for (let attempt = 0; attempt < 100 && !exportedSource; attempt += 1) {
      try {
        const library = JSON.parse(await readFile(path.join(libraryDirectory, "library.json"), "utf8"));
        const listed = library.sources.find((item) => item.id === source.id);
        if (listed) {
          const sourceFolder = path.join(libraryDirectory, listed.folder);
          exportedSource = await Promise.all([
            readFile(path.join(sourceFolder, "source.json"), "utf8"),
            readFile(path.join(sourceFolder, "content.md"), "utf8"),
          ]);
        }
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
        exportPending = error.message;
      }
      if (!exportedSource) await delay(100);
    }
    if (!exportedSource) throw new SmokeFailure(`The filed source export did not finish: ${exportPending}`);
    const described = JSON.parse(exportedSource[0]);
    const text = exportedSource[1];
    if (described.id !== source.id || !text.trim()) {
      throw new SmokeFailure("The source folder did not describe the filed source");
    }

    const queueEmpty = await cdp.evaluate("JSON.parse(localStorage.getItem('gunther:local-captures') || '[]').length === 0");
    if (!queueEmpty) throw new SmokeFailure("Successful capture remained in the local retry queue");

    // Home is the search surface: the filed source must be findable and routed to its library.
    if (!await cdp.evaluate(clickExpression("button", "Home"))) {
      throw new SmokeFailure("Home navigation was not clickable after filing");
    }
    await waitForEvaluation(cdp, "document.querySelector('[aria-label=\"Search your knowledge or the web\"]') !== null", "search-first Home after filing");
    if (!await cdp.evaluate(setAriaValueExpression("Search your knowledge or the web", marker))) {
      throw new SmokeFailure("Home search composer was not editable");
    }
    await waitForEvaluation(cdp, "document.querySelector('button[aria-label=\"Run search\"]')?.disabled === false", "enabled Home search");
    if (!await cdp.evaluate(clickExpression("button", "Run search"))) {
      throw new SmokeFailure("Home search could not be run");
    }
    await waitForEvaluation(
      cdp,
      `[...document.querySelectorAll('button.gx-result')].some((button) => button.querySelector('strong')?.textContent.trim() === ${JSON.stringify(noteTitle)} && button.textContent.includes(${JSON.stringify(libraryTitle)}))`,
      "filed source found from Home search with its library",
    );

    if (!await cdp.evaluate(clickExpression("button", "Libraries"))) {
      throw new SmokeFailure("Library navigation was not clickable after filing");
    }
    await waitForEvaluation(cdp, "document.querySelector('.gx-libraries h1')?.textContent.trim() === 'Libraries'", "Libraries view after filing");
    if (!await cdp.evaluate(clickExpression("button", libraryTitle))) {
      throw new SmokeFailure("The created knowledge base was not clickable");
    }
    await waitForEvaluation(cdp, libraryWelcome, "reopened library with Chat in the middle");

    const sourceRow = `.contents[aria-label="Sources"] [data-row=${JSON.stringify(`source:${source.id}`)}]`;
    await waitForEvaluation(
      cdp,
      `document.querySelector(${JSON.stringify(`${sourceRow} .contents-source strong`)})?.textContent.trim() === ${JSON.stringify(noteTitle)}
        && document.querySelector(${JSON.stringify(`${sourceRow} .contents-drag-handle`)})?.disabled === false`,
      "filed note and its drag handle in the Sources tree",
    );
    const sourceActions = clickExpression(`${sourceRow} button`, `Actions for ${noteTitle}`);
    if (!await cdp.evaluate(sourceActions)) throw new SmokeFailure("The source actions menu could not be opened");
    await waitForEvaluation(
      cdp,
      `[...document.querySelectorAll(${JSON.stringify(`${sourceRow} [role="menuitem"]`)})].map((button) => button.textContent.trim()).join('|') === 'Rename|Move to…|Move to Trash'`,
      "single-row menu for the unplaced note",
    );
    if (!await cdp.evaluate(sourceActions)) throw new SmokeFailure("The source actions menu could not be closed");
    if (!await cdp.evaluate(clickExpression(`${sourceRow} button.contents-source`, noteTitle))) {
      throw new SmokeFailure("The filed source summary could not be opened");
    }
    await waitForEvaluation(
      cdp,
      `document.querySelector('article[aria-label="Source summary"] h1')?.textContent.trim() === ${JSON.stringify(noteTitle)}
        && document.querySelector(${JSON.stringify(`${sourceRow} input.contents-check`)})?.checked === true`,
      "source summary opens with its scope tick intact",
    );
    if (!await cdp.evaluate(clickExpression('article[aria-label="Source summary"] button', "Open full page"))) {
      throw new SmokeFailure("Open full page was not clickable");
    }
    // Sources open into their own page with the title, preserved text, and Back.
    await waitForEvaluation(
      cdp,
      `document.querySelector('.gx-item-title')?.textContent.trim() === ${JSON.stringify(noteTitle)} && document.body.innerText.includes('BRCA1 regulates DNA repair.')`,
      "source page with preserved readable content",
    );
    const wentBack = await cdp.evaluate(`(() => {
      const back = document.querySelector('.gx-item-back');
      if (!back) return false;
      back.click();
      return true;
    })()`);
    if (!wentBack) throw new SmokeFailure("The source page could not go back to its library");
    await waitForEvaluation(cdp, libraryChatOpen, "library Chat after the source page");

    const askReadyStartedAt = Date.now();
    try {
      await waitForEvaluation(
        cdp,
        "(() => { const composer = document.querySelector('[aria-label=\"Message Gunther\"]'); const status = document.querySelector('.conversation-title'); return Boolean(composer && !composer.disabled && status?.textContent.includes('Saved to local knowledge service')); })()",
        "ready grounded Ask session",
        15_000,
      );
    } catch (error) {
      const diagnostics = await cdp.evaluate(`(() => {
        const composer = document.querySelector('[aria-label="Message Gunther"]');
        return {
          composer: composer ? { disabled: composer.disabled, placeholder: composer.getAttribute('placeholder') } : null,
          savedInBody: document.body.innerText.includes('Saved to local knowledge service'),
          readyExpression: Boolean(composer && !composer.disabled && document.body.innerText.includes('Saved to local knowledge service')),
          error: document.querySelector('.conversation-error')?.textContent.replace(/\\s+/g, ' ').trim() ?? null,
          heading: document.querySelector('.conversation-title')?.textContent.replace(/\\s+/g, ' ').trim() ?? null,
          note: document.querySelector('.composer-note')?.textContent.replace(/\\s+/g, ' ').trim() ?? null,
        };
      })()`);
      const sessions = await jsonFrom(`${backendOrigin}/api/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/sessions`).catch((reason) => ({ requestError: String(reason) }));
      throw new SmokeFailure(`${error instanceof Error ? error.message : String(error)}; diagnostics=${JSON.stringify(diagnostics)}; sessions=${JSON.stringify(sessions)}`);
    }
    const askReadyMilliseconds = Date.now() - askReadyStartedAt;
    // The fixture provider also exercises the model menu with a long display label.
    await waitForEvaluation(cdp, "document.querySelector('.composer [data-chip=model]')?.textContent.includes('A model with a long display name')", "long model menu fixture");
    // Chat keeps All sources and the two action buttons usable with Studio collapsed.
    await resizeViewport(900, 640);
    const studioRailAt900 = `window.innerWidth === 900 && document.querySelector('button[aria-label="Expand Studio"]') !== null`;
    await waitForEvaluation(cdp, studioRailAt900, "narrow Ask window with a Studio rail");
    const composerLayout = await cdp.evaluate(`(() => {
      const composer = document.querySelector('.composer').getBoundingClientRect();
      const options = document.querySelector('.composer-options').getBoundingClientRect();
      const actions = document.querySelector('.composer-actions').getBoundingClientRect();
      const mic = document.querySelector('.composer-actions .mic-button').getBoundingClientRect();
      const send = document.querySelector('.composer-actions .send-button').getBoundingClientRect();
      return { right: composer.right, bottom: composer.bottom, optionsRight: options.right, actionsLeft: actions.left, micTop: mic.top, sendTop: send.top, micRight: mic.right, sendLeft: send.left,
        hasSources: document.querySelector('.scope-chip')?.textContent.trim() === 'All sources', hasStyle: Boolean(document.querySelector('.composer [aria-label="Answer style"]')) };
    })()`);
    if (composerLayout.right > 900 || composerLayout.bottom > 640 || composerLayout.optionsRight > composerLayout.actionsLeft || Math.abs(composerLayout.micTop - composerLayout.sendTop) > 1 || composerLayout.micRight > composerLayout.sendLeft || !composerLayout.hasSources || composerLayout.hasStyle) {
      throw new SmokeFailure(`The narrow Ask layout is crowded: ${JSON.stringify(composerLayout)}`);
    }
    await screenshot("ask-900");
    await wideViewport();
    const question = "What does BRCA1 regulate?";
    if (!await cdp.evaluate(setAriaValueExpression("Message Gunther", question))) {
      throw new SmokeFailure("Ask composer was not editable");
    }

    // Arrange a source under a nested section and change the shared scope from its parent.
    const makeSection = async (title, parentId, sourceIds = []) => {
      const response = await fetch(`${backendOrigin}/api/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/topics`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, description: "", parentId, sourceIds }),
      });
      if (!response.ok) throw new SmokeFailure(`Could not arrange library sources (${response.status})`);
      return response.json();
    };
    const course = await makeSection("Course materials", null);
    await makeSection("Week 1", course.id, sources.map((item) => item.id));
    await cdp.evaluate("window.dispatchEvent(new Event('gunther:topics-updated'))");
    const courseTick = 'input[aria-label="Select sources in Course materials"]';
    await waitForEvaluation(
      cdp,
      `document.querySelector('.contents-list')?.textContent.includes('Week 1')
        && document.querySelector(${JSON.stringify(courseTick)})?.checked === true
        && document.querySelector('.contents-scope small')?.textContent.trim() === ${JSON.stringify(String(sources.length))}`,
      "Sources shows the course hierarchy and total",
    );
    const courseSelection = (checked) => `(() => {
      const sourceTicks = [...document.querySelectorAll('.contents-list .contents-source-row input.contents-check')];
      return document.querySelector(${JSON.stringify(courseTick)})?.checked === ${checked}
        && document.querySelector('.contents-scope input')?.checked === false
        && sourceTicks.length === ${sources.length}
        && sourceTicks.every((input) => input.checked === ${checked})
        && document.querySelector('.scope-chip')?.textContent.trim() === ${JSON.stringify(`${checked ? sources.length : 0} selected`)};
    })()`;
    if (!await cdp.evaluate(clickExpression("input", "Select sources in Course materials"))) {
      throw new SmokeFailure("The course could not be unticked");
    }
    await waitForEvaluation(
      cdp,
      `${courseSelection(false)}
        && document.querySelector('#empty-source-scope')?.textContent.includes('Select at least one source')
        && document.querySelector('button[aria-label="Send message"]')?.disabled === true
        && document.querySelector('.studio-tile > button')?.disabled === true`,
      "empty scope disables Chat Send and Studio builds",
    );
    if (!await cdp.evaluate(clickExpression("input", "Select sources in Course materials"))) {
      throw new SmokeFailure("The course could not be ticked");
    }
    await waitForEvaluation(
      cdp,
      `${courseSelection(true)} && !document.querySelector('.contents-picker-overlay')
        && document.querySelector('.studio-tile > button')?.disabled === false`,
      "section ticks choose its descendants for Chat and Studio",
    );

    await waitForEvaluation(
      cdp,
      `document.querySelector('button[aria-label="Send message"]')?.disabled === false`,
      "enabled Ask send",
    );
    if (!await cdp.evaluate(clickExpression("button", "Send message"))) {
      throw new SmokeFailure("Ask message could not be sent");
    }
    try {
      await waitForEvaluation(
        cdp,
        "document.querySelector('.conversation-message.role-assistant .message-sources-button') !== null && document.querySelector('.conversation-message.role-assistant .key-results-status') !== null",
        "grounded answer with a key results status",
        20_000,
      );
    } catch (error) {
      const diagnostics = await cdp.evaluate(`(() => ({
        answers: [...document.querySelectorAll('.conversation-message.role-assistant')].map((item) => item.textContent.replace(/\\s+/g, ' ').trim().slice(0, 600)),
        keyResults: [...document.querySelectorAll('.message-actions .key-results-status')].map((item) => item.textContent.trim()),
        errors: [...document.querySelectorAll('.conversation-error')].map((item) => item.textContent.replace(/\\s+/g, ' ').trim()),
      }))()`);
      // What the backend holds tells a stuck answer apart from one the screen did not show.
      const saved = await jsonFrom(`${backendOrigin}/api/knowledge-bases/${encodeURIComponent(knowledgeBase.id)}/sessions`)
        .then((sessions) => Promise.all(sessions.map((item) => jsonFrom(`${backendOrigin}/api/sessions/${encodeURIComponent(item.id)}`))))
        .then((details) => details.map((detail) => ({ id: detail.id, messages: detail.messages.map((message) => ({ role: message.role, content: String(message.content).slice(0, 80), citations: message.citations?.length ?? 0 })) })))
        .catch((reason) => ({ requestError: String(reason) }));
      const activity = await jsonFrom(`${backendOrigin}/api/activity`)
        .then((list) => (list.items ?? list).map((item) => ({ id: item.id, state: item.state, label: item.label })))
        .catch((reason) => ({ requestError: String(reason) }));
      throw new SmokeFailure(`${error instanceof Error ? error.message : String(error)}; diagnostics=${JSON.stringify({ ...diagnostics, saved, activity })}`);
    }
    if (!await cdp.evaluate(clickExpression(".message-sources-button", "1 source"))) {
      throw new SmokeFailure("The answer's sources could not be opened");
    }
    await waitForEvaluation(cdp, `document.querySelector('[role="tabpanel"][aria-label="Evidence"] .evidence-card > strong')?.getClientRects().length > 0`, "answer sources in Inspector");
    if (!await cdp.evaluate(clickExpression('[role="tabpanel"][aria-label="Evidence"] .evidence-card > strong', noteTitle))) {
      throw new SmokeFailure("The answer's source could not be opened");
    }
    await waitForEvaluation(
      cdp,
      `document.querySelector('[role="tabpanel"][aria-label="Evidence"] .gx-evidence-detail > header strong')?.textContent.trim() === ${JSON.stringify(noteTitle)}`,
      "answer source opens its evidence",
    );
    if (!await cdp.evaluate(clickExpression("button", "All 1 source"))) {
      throw new SmokeFailure("The evidence list could not be reopened");
    }
    await waitForEvaluation(
      cdp,
      "document.querySelector('.message-actions .key-results-status')?.textContent.includes('Key results need an Analysis model')",
      "key results needing an Analysis model",
    );

    const chatTitle = await cdp.evaluate("document.querySelector('.conversation-title > strong')?.textContent.trim()");
    if (!chatTitle || !await cdp.evaluate(clickExpression("button", "Chat actions"))) {
      throw new SmokeFailure("The Chat actions menu could not be opened");
    }
    await waitForEvaluation(
      cdp,
      `[...document.querySelectorAll('.conversation-header [role="menuitem"]')].map((button) => button.textContent.trim()).join('|') === 'New chat|Rename|Export Markdown|Archive'`,
      "library Chat actions",
    );
    if (!await cdp.evaluate(clickExpression('[role="menuitem"]', "New chat"))) {
      throw new SmokeFailure("A new chat could not be opened");
    }
    await waitForEvaluation(
      cdp,
      `document.querySelector('.conversation-title > strong') !== null && document.querySelector('.message-sources-button') === null`,
      "new chat before reopening the saved chat",
    );
    if (!await cdp.evaluate(clickExpression('[aria-label="Chats"] button', "Search chats"))) {
      throw new SmokeFailure("The Chats search could not be opened");
    }
    await waitForEvaluation(
      cdp,
      `document.querySelector('[aria-label="Chats"] input[aria-label="Search chats"]') !== null`,
      "Chats search field",
    );
    if (!await cdp.evaluate(`(() => {
      const input = document.querySelector('[aria-label="Chats"] input[aria-label="Search chats"]');
      if (!input) return false;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(chatTitle)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    })()`)) {
      throw new SmokeFailure("The saved chat could not be searched for");
    }
    await waitForEvaluation(
      cdp,
      `[...document.querySelectorAll('[aria-label="Chats"] .library-chat-open:not([aria-current="page"]) > span:first-child')].some((title) => title.textContent.trim() === ${JSON.stringify(chatTitle)})`,
      "saved chat in Chats search results",
    );
    if (!await cdp.evaluate(clickExpression('[aria-label="Chats"] .library-chat-open:not([aria-current="page"]) > span:first-child', chatTitle))) {
      throw new SmokeFailure("The saved chat could not be reopened from Chats");
    }
    await waitForEvaluation(
      cdp,
      `document.querySelector('.message-sources-button') !== null`,
      "saved answer after reopening from Chats",
    );

    // The saved answer stays available after removing its model from Settings.
    const { providers: [fixtureProvider] } = await jsonFrom(`${backendOrigin}/api/settings/models`);
    if (!fixtureProvider) throw new SmokeFailure("The fixture Ask provider was not configured");
    const removedProvider = await fetch(
      `${backendOrigin}/api/settings/providers/${encodeURIComponent(fixtureProvider.id)}`,
      { method: "DELETE", signal: AbortSignal.timeout(5_000) },
    );
    if (!removedProvider.ok) throw new SmokeFailure(`Could not remove the fixture model (${removedProvider.status})`);

    if (!await cdp.evaluate(clickExpression("button", "Libraries"))) {
      throw new SmokeFailure("Library navigation was not clickable before checking Studio");
    }
    await waitForEvaluation(cdp, "document.querySelector('.gx-libraries h1')?.textContent.trim() === 'Libraries'", "Libraries view before checking Studio");
    if (!await cdp.evaluate(clickExpression("button", libraryTitle))) {
      throw new SmokeFailure("The created knowledge base could not be reopened");
    }
    await waitForEvaluation(cdp, `${libraryChatOpen} && ${courseSelection(true)}`, "shared source selection persists after reopening the library");
    await waitForEvaluation(
      cdp,
      "document.querySelector('.message-sources-button') !== null && document.querySelector('.message-actions .key-results-status')?.textContent.includes('Key results need an Analysis model')",
      "saved answer and sources remain visible after the model is removed",
    );
    await waitForEvaluation(
      cdp,
      `(() => {
        const studio = document.querySelector('[aria-label="Studio"]');
        const buttons = [...(studio?.querySelectorAll('.studio-tile button') ?? [])];
        return studio?.querySelector('.studio-model-note [role="status"]')?.textContent.includes('Studio needs a model')
          && studio.querySelector('.studio-model-note button')?.disabled === false
          && buttons.length > 0 && buttons.every((button) => button.disabled);
      })()`,
      "Studio shows model setup with its tiles disabled",
    );

    await resizeViewport(900, 640);
    await waitForEvaluation(cdp, studioRailAt900, "narrow Sources window");
    await screenshot("sources-900");
    const sourcesFit = await cdp.evaluate(`(() => {
      const tree = document.querySelector('.library-workspace .contents');
      const box = tree.getBoundingClientRect();
      const column = tree.closest('.library-column-panel').getBoundingClientRect();
      const footer = document.querySelector('.library-sources .contents-add').getBoundingClientRect();
      return box.top >= 0 && box.bottom <= innerHeight + 1 && footer.bottom <= column.bottom + 1 && box.right <= innerWidth;
    })()`);
    if (!sourcesFit) throw new SmokeFailure("Source selection does not fit in a narrow window");
    if (!await cdp.evaluate(clickExpression("button", "Expand Studio"))) {
      throw new SmokeFailure("Studio could not be reopened from its rail");
    }
    await waitForEvaluation(cdp, "document.querySelector('.studio-column')?.getClientRects().length > 0", "Studio visible after reopening its rail");
    await resizeViewport(800, 640);
    await waitForEvaluation(
      cdp,
      `window.innerWidth === 800 && document.querySelector('button[aria-label="Expand Library"]') !== null`,
      "Library rail at the minimum desktop width",
    );
    if (!await cdp.evaluate(clickExpression("button", "Expand Library"))) {
      throw new SmokeFailure("Library could not be reopened from its rail");
    }
    await waitForEvaluation(cdp, "document.querySelector('.contents')?.getClientRects().length > 0", "Sources visible after reopening its rail");
    await wideViewport();

    // The answer preference remains available in General Settings and survives reopening.
    if (!await cdp.evaluate(clickExpression("button", "Settings"))) throw new SmokeFailure("Settings was not clickable");
    await waitForEvaluation(cdp, "document.querySelector('.settings-nav') !== null", "Settings topics");
    await cdp.evaluate(clickExpression("button", "General"));
    await waitForEvaluation(cdp, "document.querySelector('.settings-pane [aria-label=\"Answer style\"]') !== null", "Answer style in General Settings");
    await cdp.evaluate(`(() => { const select = document.querySelector('[aria-label="Answer style"]'); select.value = 'concise'; select.dispatchEvent(new Event('change', { bubbles: true })); })()`);
    await waitForEvaluation(cdp, "localStorage.getItem('gunther:answer-style') === 'concise'", "remembered concise answer preference");
    await resizeViewport(1000, 820);
    await screenshot("settings-answer-style");
    await cdp.evaluate(clickExpression("button", "Home"));
    await waitForEvaluation(cdp, "document.querySelector('.gx-home') !== null", "Home after choosing an answer style");
    if (await cdp.evaluate("Boolean(document.querySelector('.gx-home [aria-label=\"Answer style\"]'))")) throw new SmokeFailure("Home still crowds its composer with answer style");
    await cdp.evaluate(clickExpression("button", "Settings"));
    await waitForEvaluation(cdp, "document.querySelector('.settings-pane [aria-label=\"Answer style\"]')?.value === 'concise'", "answer preference after reopening Settings");
    await wideViewport();

    // Trash: move a capture there from Inbox, undo, restore from the Trash page, delete forever.
    const trashTitle = `Disposable capture ${marker}`;
    const trashCreated = await fetch(`${backendOrigin}/api/sources`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: trashTitle, kind: "note", content: `A capture for the Trash flow ${marker}.` }),
    });
    if (!trashCreated.ok) throw new SmokeFailure(`Disposable capture was not created (${trashCreated.status})`);
    const trashSourceId = (await trashCreated.json()).source.id;
    const inInbox = async () => (await jsonFrom(`${backendOrigin}/api/inbox`)).some((item) => item.sourceId === trashSourceId);
    const inTrash = async () => (await jsonFrom(`${backendOrigin}/api/trash`)).some((item) => item.id === trashSourceId);
    const trashFromInbox = async () => {
      if (!await cdp.evaluate(clickExpression("button", "Inbox"))) throw new SmokeFailure("Inbox navigation was not clickable for Trash");
      await cdp.evaluate("window.dispatchEvent(new CustomEvent('gunther:inbox-updated'))");
      // Wait for the row itself: an earlier toast may already show the title.
      await waitForEvaluation(
        cdp,
        `[...document.querySelectorAll('.gx-inbox-trash')].some((button) => !button.disabled && button.getAttribute('aria-label') === ${JSON.stringify(`Move “${trashTitle}” to Trash`)})`,
        "disposable capture's row in Inbox",
      );
      if (!await cdp.evaluate(clickExpression("button", `Move “${trashTitle}” to Trash`))) {
        throw new SmokeFailure("The Inbox row could not be moved to Trash");
      }
      await waitForEvaluation(
        cdp,
        `document.querySelector('.atlas-toast')?.textContent.includes(${JSON.stringify(`Moved “${trashTitle}” to Trash.`)}) && document.querySelector('.gx-toast-action') !== null`,
        "Trash toast offering Undo",
      );
      if (!await inTrash() || await inInbox()) throw new SmokeFailure("Moving to Trash did not leave Inbox for Trash");
    };

    await trashFromInbox();
    if (!await cdp.evaluate(clickExpression("button", "Undo"))) throw new SmokeFailure("Undo was not clickable");
    await waitForEvaluation(cdp, `document.querySelector('.atlas-toast')?.textContent.includes(${JSON.stringify(`Restored “${trashTitle}”.`)})`, "Undo confirmation");
    if (await inTrash() || !await inInbox()) throw new SmokeFailure("Undo did not return the capture to Inbox");

    await trashFromInbox();
    if (!await cdp.evaluate(clickExpression("button", "Trash"))) throw new SmokeFailure("Trash navigation was not clickable");
    await waitForEvaluation(cdp, `[...document.querySelectorAll('.gx-trash-row')].some((row) => row.textContent.includes(${JSON.stringify(trashTitle)}))`, "capture listed in Trash");
    if (!await cdp.evaluate(clickExpression("button", `Restore “${trashTitle}”`))) throw new SmokeFailure("Restore was not clickable in Trash");
    await waitForEvaluation(cdp, `![...document.querySelectorAll('.gx-trash-row')].some((row) => row.textContent.includes(${JSON.stringify(trashTitle)}))`, "restored capture leaving Trash");
    if (await inTrash() || !await inInbox()) throw new SmokeFailure("Restoring from the Trash page did not return the capture");

    await trashFromInbox();
    if (!await cdp.evaluate(clickExpression("button", "Trash"))) throw new SmokeFailure("Trash navigation was not clickable before deleting");
    await waitForEvaluation(cdp, `[...document.querySelectorAll('.gx-trash-row')].some((row) => row.textContent.includes(${JSON.stringify(trashTitle)}))`, "capture listed in Trash before deleting");
    if (!await cdp.evaluate(clickExpression("button", `Delete “${trashTitle}” forever`))) throw new SmokeFailure("Delete forever was not clickable");
    if (!await cdp.evaluate(clickExpression("button", "Delete forever"))) throw new SmokeFailure("Delete forever could not be confirmed");
    await waitForEvaluation(cdp, `document.querySelector('.atlas-toast')?.textContent.includes(${JSON.stringify(`Deleted “${trashTitle}” for good.`)})`, "permanent deletion confirmation");
    const deletedSource = await fetch(`${backendOrigin}/api/sources/${encodeURIComponent(trashSourceId)}`);
    if (deletedSource.status !== 404 || await inTrash()) throw new SmokeFailure("Delete forever left the capture behind");

    if (browserErrors.length) throw new SmokeFailure(`Browser errors: ${browserErrors.join(" | ")}`);

    result = {
      status: "passed",
      checks: 50,
      browser: path.basename(chromeBinary),
      knowledgeBaseId: knowledgeBase.id,
      noteId: filedNote.id,
      sourceId: source.id,
      askReadyMilliseconds,
      flows: [
        "production bundle rendered in real Chrome",
        "capture before organize",
        "Quick note persisted to Inbox",
        "library created through UI",
        "Add source opened Note capture for the open library",
        "Inbox item filed through UI",
        "filed source found from search-first Home",
        "note-to-source provenance preserved",
        "filed source written to its readable library folder",
        "successful local retry copy cleared",
        "filed note showed its row actions and drag handle and reopened through its summary with preserved content",
        "grounded Chat answer persisted with sources that open evidence",
        "Chats search reopened a saved answer",
        "grounded answer showed key results waiting for an Analysis model",
        "Studio showed model setup with disabled tiles after the saved answer was reopened",
        "Chat kept All sources and side-by-side voice/send actions at 900 × 640",
        "Sources showed the total and fit at 900 × 640, with Studio available from its rail",
        "Library collapsed at 800 × 640 and reopened from its rail",
        "section ticks set Chat and Studio scope, blocked empty selection, and persisted on library reopen",
        "Answer style moved to General Settings, persisted and stayed out of Ask/Home composers",
        "capture moved to Trash from Inbox and brought back with Undo",
        "capture restored from the Trash page",
        "capture deleted forever from Trash",
      ],
    };
  } catch (error) {
    failure = error;
  } finally {
    cdp?.close();
    await stopChild(chrome, "SIGTERM");
    await stopChild(backend);
    await closeServer(staticServer);
    try {
      if (backendPort) await assertPortReleased(backendPort);
      if (uiPort) await assertPortReleased(uiPort);
    } catch (error) {
      failure ??= new SmokeFailure(`A disposable smoke port was not released: ${error}`);
    }
    await rm(temporaryRoot, { recursive: true, force: true });
    try {
      await access(temporaryRoot);
      failure ??= new SmokeFailure("Temporary smoke directory was not removed");
    } catch {
      // Expected: the exact disposable directory no longer exists.
    }
  }

  if (failure) {
    const detail = failure instanceof Error ? failure.stack ?? failure.message : String(failure);
    throw new SmokeFailure(`${detail}\nBackend log:\n${backendLogs()}\nChrome log:\n${chromeLogs()}`);
  }
  return result;
}

try {
  console.log(JSON.stringify(await run(), null, 2));
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  console.error(JSON.stringify({ status: "failed", error: message }, null, 2));
  if (process.env.GITHUB_ACTIONS) {
    // An annotation shows the reason on the run page without opening the job log.
    // The start says what failed; the end of the backend log says what it was doing.
    const brief = message.length > 6000 ? `${message.slice(0, 2500)}\n…\n${message.slice(-3500)}` : message;
    const text = brief.replaceAll("%", "%25").replaceAll("\r", "%0D").replaceAll("\n", "%0A");
    console.log(`::error title=Chrome journey::${text}`);
  }
  process.exitCode = 1;
}
