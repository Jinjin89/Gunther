// Development only: renders the studio deck/report gallery (or any deck/report file) to PNGs and measures them.
//
//   node apps/desktop/evals/deck/capture.mjs                       every fixture x theme -> artifacts/validation/studio-gallery
//   node apps/desktop/evals/deck/capture.mjs --input deck.md [--citations c.json] --out DIR [--kind deck|report] [--theme paper]
//
// Gallery mode exits 1 when a slide overflows, is scaled down, or shows visible text under 18px
// (not counting the source line, the page number and citation superscripts).
// Input mode writes DIR/slide-01.png ... (or page-01.png ... for a report) and DIR/measure.json.
import { chromium } from "playwright";
import { createServer } from "vite";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../../..");
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const input = option("--input");
const kind = option("--kind", "deck");
const out = path.resolve(option("--out", path.join(root, "artifacts/validation/studio-gallery")));
const citationsFile = option("--citations");
const wantTheme = option("--theme");
const MIN_FONT = 18;

let server = null;
let origin = option("--origin");
if (!origin) {
  server = await createServer({ configFile: path.join(here, "vite.config.ts"), logLevel: "error", server: { port: 5185, strictPort: false } });
  await server.listen();
  origin = server.resolvedUrls.local[0].replace(/\/$/, "");
}
/** A Chromium to drive: CHROME_BIN, the pinned slide browser, any Playwright download, or Playwright's own. */
async function findBrowser() {
  const exists = (file) => fs.access(file).then(() => true, () => false);
  if (process.env.CHROME_BIN && await exists(process.env.CHROME_BIN)) return process.env.CHROME_BIN;
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, path.join(root, "tools/slide-browser/.cache"), path.join(process.env.HOME ?? "", ".cache/ms-playwright"), path.join(process.env.HOME ?? "", "Library/Caches/ms-playwright")].filter(Boolean);
  for (const base of roots) {
    const entries = await fs.readdir(base).catch(() => []);
    for (const entry of entries.sort().reverse()) {
      for (const sub of ["chrome-linux64/chrome", "chrome-headless-shell-linux64/chrome-headless-shell", "chrome-linux/chrome", "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing", "chrome-mac/Chromium.app/Contents/MacOS/Chromium"]) {
        const file = path.join(base, entry, sub);
        if (/^chromium/.test(entry) && await exists(file)) return file;
      }
    }
  }
  return null;
}
const launch = async () => {
  const executablePath = await findBrowser();
  if (executablePath) return chromium.launch({ headless: true, executablePath });
  return chromium.launch({ headless: true });
};
const browser = await launch();

/** Measures every slide card on the page. Runs in the page. */
function measureSlides() {
  const cards = [...document.querySelectorAll("article[data-slide]")];
  return cards.map((card) => {
    const face = card.querySelector(".dk-face");
    const fit = Number(card.dataset.fit);
    const body = face.querySelector(".dk-body");
    const faceBox = face.getBoundingClientRect();
    const sceneScale = faceBox.width / 1280;
    const tiny = [];
    let minFont = Infinity;
    const marks = [];
    for (const el of face.querySelectorAll("*")) {
      if (el.closest(".dk-foot") || el.closest(".dk-cite")) continue;
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;
      const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      const r = el.getBoundingClientRect();
      if (own) {
        const px = parseFloat(style.fontSize) * fit;
        if (px < minFont) minFont = px;
        if (px < 17.95) tiny.push({ text: el.textContent.trim().slice(0, 40), px: Math.round(px * 10) / 10 });
      }
      if (r.width > 0 && r.height > 0 && (own || /^(rect|circle|line|polyline|path|img)$/i.test(el.tagName) || (style.backgroundColor !== "rgba(0, 0, 0, 0)" && !el.matches(".dk-canvas,.dk-face,.dk-body")))) marks.push(r);
    }
    // Empty area of the body region: share of 24px cells that no text, mark or filled box touches.
    const b = body.getBoundingClientRect();
    const cell = 24 * sceneScale;
    const cols = Math.max(1, Math.round(b.width / cell));
    const rows = Math.max(1, Math.round(b.height / cell));
    let empty = 0;
    let bottom = b.top;
    for (let y = 0; y < rows; y += 1) for (let x = 0; x < cols; x += 1) {
      const c = { l: b.left + x * cell, t: b.top + y * cell, r: b.left + (x + 1) * cell, b: b.top + (y + 1) * cell };
      if (!marks.some((m) => m.left < c.r && m.right > c.l && m.top < c.b && m.bottom > c.t)) empty += 1;
    }
    for (const m of marks) if (m.bottom > bottom && m.top >= b.top - 1) bottom = Math.min(m.bottom, b.bottom);
    return {
      index: Number(card.dataset.slide) + 1, why: card.dataset.why || null, layout: card.dataset.layout, overflow: card.dataset.overflow === "true", fit,
      minFontPx: minFont === Infinity ? null : Math.round(minFont * 10) / 10, tiny,
      emptyRatio: Math.round((empty / (cols * rows)) * 100) / 100,
      contentHeightRatio: Math.round(((bottom - b.top) / b.height) * 100) / 100,
    };
  });
}

async function ready(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map((i) => i.decode().catch(() => undefined)));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  await page.waitForTimeout(150);
}

async function deckPage(page, query, injected) {
  if (injected) await page.addInitScript((value) => { window.__GALLERY__ = value; }, injected);
  await page.goto(`${origin}/evals/deck/?${query}`, { waitUntil: "networkidle" });
  await page.waitForSelector("article[data-slide]");
  await ready(page);
}

const failures = [];
try {
  if (input) {
    await fs.mkdir(out, { recursive: true });
    const content = await fs.readFile(path.resolve(input), "utf8");
    const citations = citationsFile ? JSON.parse(await fs.readFile(path.resolve(citationsFile), "utf8")) : [];
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    if (kind === "report") {
      await page.addInitScript((value) => { window.__GALLERY__ = value; }, { content, citations, kind });
      await page.setViewportSize({ width: 1000, height: 1200 });
      await page.goto(`${origin}/evals/deck/report.html?${wantTheme ? `theme=${wantTheme}` : ""}`, { waitUntil: "networkidle" });
      await ready(page);
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      const step = 1300;
      const pages = [];
      for (let at = 0, n = 1; at < height; at += step, n += 1) {
        const file = `page-${String(n).padStart(2, "0")}.png`;
        await page.screenshot({ path: path.join(out, file), clip: { x: 0, y: at, width: 1000, height: Math.min(step, height - at) }, fullPage: true });
        pages.push(file);
      }
      const measure = await page.evaluate(() => ({
        exhibits: document.querySelectorAll(".rp-exhibit").length, callouts: document.querySelectorAll(".rp-callout").length,
        summary: !!document.querySelector(".rp-summary"), words: document.body.innerText.split(/\s+/).length,
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      }));
      await fs.writeFile(path.join(out, "measure.json"), JSON.stringify({ kind, height, pages, errors, ...measure }, null, 2) + "\n");
      console.log(JSON.stringify({ kind, pages: pages.length, errors }));
    } else {
      await deckPage(page, wantTheme ? `theme=${wantTheme}` : "", { content, citations, kind });
      const slides = await page.evaluate(measureSlides);
      const cards = page.locator("article[data-slide] .gallery-stage");
      for (let at = 0; at < await cards.count(); at += 1) await cards.nth(at).screenshot({ path: path.join(out, `slide-${String(at + 1).padStart(2, "0")}.png`) });
      await fs.writeFile(path.join(out, "measure.json"), JSON.stringify({ kind, slides, errors }, null, 2) + "\n");
      console.log(JSON.stringify({ kind, slides: slides.length, overflow: slides.filter((s) => s.overflow).length, errors }));
    }
  } else {
    await fs.mkdir(out, { recursive: true });
    const report = [];
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(String(e)));
    for (const variant of ["short", "max", "zh"]) for (const theme of ["paper", "ink", "editorial"]) {
      await deckPage(page, `fixture=deck.${variant}&theme=${theme}`);
      const slides = await page.evaluate(measureSlides);
      const cards = page.locator("article[data-slide] .gallery-stage");
      for (let at = 0; at < slides.length; at += 1) {
        const s = slides[at];
        const file = `${variant}-${theme}-${String(s.index).padStart(2, "0")}-${s.layout}.png`;
        await cards.nth(at).screenshot({ path: path.join(out, file) });
        if (s.overflow) failures.push(`${file}: overflows - ${s.why}`);
        if (s.fit < 1 && !s.overflow) failures.push(`${file}: scaled to ${s.fit}`);
        if (s.tiny.length) failures.push(`${file}: text under 18px (${s.tiny.slice(0, 3).map((t) => `${t.px}px "${t.text}"`).join(", ")})`);
        report.push({ variant, theme, file, ...s });
      }
      // Contact sheets: six slides to a picture, to see a whole deck at a glance.
      await deckPage(page, `fixture=deck.${variant}&theme=${theme}&sheet`);
      await page.setViewportSize({ width: 1280, height: 1080 });
      const total = slides.length;
      for (let from = 0, n = 1; from < total; from += 6, n += 1) {
        await page.screenshot({ path: path.join(out, `sheet-${variant}-${theme}-${n}.png`), fullPage: true, clip: { x: 0, y: (from / 2) * 360, width: 1280, height: Math.min(3, Math.ceil((total - from) / 2)) * 360 } });
      }
      await page.setViewportSize({ width: 1400, height: 900 });
    }
    // The report fixture, as the app shows it: full-page pictures.
    await page.setViewportSize({ width: 1000, height: 1200 });
    await page.goto(`${origin}/evals/deck/report.html`, { waitUntil: "networkidle" });
    await ready(page);
    const reportHeight = await page.evaluate(() => document.documentElement.scrollHeight);
    for (let at = 0, n = 1; at < reportHeight; at += 1300, n += 1) await page.screenshot({ path: path.join(out, `report-${String(n).padStart(2, "0")}.png`), clip: { x: 0, y: at, width: 1000, height: Math.min(1300, reportHeight - at) }, fullPage: true });
    const reportCheck = await page.evaluate(() => ({ exhibits: document.querySelectorAll(".rp-exhibit").length, callouts: document.querySelectorAll(".rp-callout").length, summary: document.querySelectorAll(".rp-summary").length, wide: document.documentElement.scrollWidth > document.documentElement.clientWidth }));
    if (reportCheck.exhibits !== 4 || reportCheck.callouts !== 1 || reportCheck.summary !== 1 || reportCheck.wide) failures.push(`report fixture did not render as expected: ${JSON.stringify(reportCheck)}`);
    if (pageErrors.length) failures.push(...pageErrors);
    await fs.writeFile(path.join(out, "measure.json"), JSON.stringify({ slides: report, failures }, null, 2) + "\n");
    console.log(JSON.stringify({ slides: report.length, failures: failures.length }));
    for (const f of failures) console.log("FAIL", f);
  }
} finally {
  await browser.close();
  await server?.close();
}
if (failures.length) process.exitCode = 1;
