import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";

const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const output = path.resolve(option("--output", "../../artifacts/validation/presentation-batches-0-2/renders"));
const design = option("--design", "1");
const origin = option("--origin", "http://127.0.0.1:5184");
const packets = JSON.parse(await fs.readFile(new URL("../../../backend/evals/slides/packets.json", import.meta.url), "utf8"));
const expectedSlides = packets.reduce((total, p) => total + p.slides.length, 0);
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1344, height: 1600 }, deviceScaleFactor: 1 });
const cdp = await page.context().newCDPSession(page);
await cdp.send("DOM.enable");
await cdp.send("CSS.enable");
const errors = [];
page.on("pageerror", error => errors.push(String(error)));
const results = { design: Number(design), browser: browser.version(), slides: [], errors };
const started = performance.now();
let peakBrowserMiB = 0;
let completed = false;
try {
  for (const theme of ["briefing", "editorial", "keynote"]) {
    await page.goto(`${origin}/evals/slides/?theme=${theme}&design=${design}&all`, { waitUntil: "networkidle" });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map(i => i.decode().catch(() => undefined)));
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await page.waitForFunction(count => document.querySelectorAll(".outputs-slide-face").length === count, expectedSlides);
    if (args.includes("--picker") && theme === "briefing") {
      await page.getByRole("button", { name: "Slide style", exact: true }).click();
      await page.waitForFunction(() => {
        const elements = [...document.querySelectorAll(".outputs-theme-real-stage")];
        return elements.length === 8 && elements.every(element => {
          const frame = element.getBoundingClientRect();
          const canvas = element.querySelector(".outputs-slide-face").getBoundingClientRect();
          return frame.width > 30 && frame.height > 15 && Math.abs(frame.width - canvas.width) < 2 && Math.abs(frame.height - canvas.height) < 2;
        });
      });
      const tiles = await page.locator(".outputs-theme-real-stage").evaluateAll(elements => elements.map(element => {
        const frame = element.getBoundingClientRect();
        const canvas = element.querySelector(".outputs-slide-face").getBoundingClientRect();
        return { width: frame.width, height: frame.height, canvasWidth: canvas.width, canvasHeight: canvas.height };
      }));
      results.picker = { tiles, passed: false };
      if (tiles.length !== 8 || tiles.some(t => t.width < 30 || t.height < 15 || Math.abs(t.canvasWidth - t.width) > 2 || Math.abs(t.canvasHeight - t.height) > 2)) throw new Error("Real style previews are not scaled into their frames");
      results.picker = { tiles, passed: true };
      await page.screenshot({ path: path.join(output, "style-picker.png") });
      await page.keyboard.press("Escape");
    }
    const faces = page.locator(".sample-stage");
    for (let i = 0; i < await faces.count(); i++) {
      const face = faces.nth(i);
      const measured = await face.evaluate(stage => {
        const article = stage.closest("article");
        const canvas = stage.querySelector(".outputs-slide-face");
        const canvasBox = canvas.getBoundingClientRect();
        return { packet: article.dataset.packet, index: Number(article.dataset.slide), fit: Number(article.dataset.fit), overflow: article.dataset.overflow === "true", text: canvas.innerText,
          citations: [...canvas.querySelectorAll("sup")].map(e => e.textContent),
          size: { width: canvasBox.width, height: canvasBox.height },
          fonts: [...new Set([...canvas.querySelectorAll("h1,h2,p,td")].map(e => getComputedStyle(e).fontFamily))],
          textSizes: [...canvas.querySelectorAll("h1,h2,p,td")].map(e => parseFloat(getComputedStyle(e).fontSize)),
          missingImages: [...canvas.querySelectorAll("img")].filter(e => !e.complete || e.naturalWidth === 0).length + canvas.querySelectorAll(".outputs-s-missing,.outputs-composed-image-missing").length,
          assetImages: [...canvas.querySelectorAll("img")].map(e => ({ alt: e.alt, assetPath: new URL(e.src).pathname, width: e.naturalWidth, height: e.naturalHeight })),
          debugOverflow: [...canvas.querySelectorAll("*")].filter(e => !e.closest(".outputs-sr-only,[aria-hidden='true']") && getComputedStyle(e).display !== "none" && getComputedStyle(e).visibility !== "hidden").filter(e => {
            const r = e.getBoundingClientRect(); const s = getComputedStyle(canvas); const scale = canvasBox.width / 1280;
            return r.width > 0 && (r.left < canvasBox.left + parseFloat(s.paddingLeft) * scale - 2 || r.right > canvasBox.right - parseFloat(s.paddingRight) * scale + 2 || r.bottom > canvasBox.bottom - parseFloat(s.paddingBottom) * scale + 2 || r.top < canvasBox.top || e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 2 || e.clientHeight > 0 && e.scrollHeight > e.clientHeight + 2);
          }).slice(0,8).map(e => ({ tag: e.tagName, class: e.className?.baseVal ?? e.className, text: e.textContent?.slice(0,50), client: [e.clientWidth,e.clientHeight], scroll:[e.scrollWidth,e.scrollHeight] })) };
      });
      const filename = `${theme}-${measured.packet}-${measured.index}.png`;
      const packet = packets.find(p => p.id === measured.packet);
      const expectedImages = (packet.slides[measured.index].body.match(/!\[[^\]]*\]\(gunther-asset:/g) ?? []).length;
      if (measured.assetImages.length !== expectedImages || measured.missingImages) errors.push(`Source asset did not render in ${measured.packet}`);
      const { root } = await cdp.send("DOM.getDocument");
      const selector = `article[data-packet="${measured.packet}"][data-slide="${measured.index}"] .outputs-slide-face :is(h1,h2,p,td)`;
      const { nodeIds } = await cdp.send("DOM.querySelectorAll", { nodeId: root.nodeId, selector });
      const platformFonts = new Map();
      for (const nodeId of nodeIds.slice(0, 4)) {
        const { fonts } = await cdp.send("CSS.getPlatformFontsForNode", { nodeId });
        for (const font of fonts) platformFonts.set(font.postScriptName, font);
      }
      measured.platformFonts = [...platformFonts.values()];
      if (measured.platformFonts.some(font => /last.?resort/i.test(font.familyName))) errors.push(`Missing glyph font in ${measured.packet}`);
      await face.screenshot({ path: path.join(output, filename) });
      results.slides.push({ theme, ...measured, file: filename, textHash: crypto.createHash("sha256").update(measured.text).digest("hex") });
    }
    const processes = execFileSync("ps", ["-axo", "pid=,ppid=,rss="], { encoding: "utf8" }).trim().split("\n").map(line => line.trim().split(/\s+/).map(Number));
    const descendants = new Set([process.pid]);
    for (let generation = 0; generation < 8; generation++) for (const [pid, ppid] of processes) if (descendants.has(ppid)) descendants.add(pid);
    peakBrowserMiB = Math.max(peakBrowserMiB, processes.filter(([pid]) => pid !== process.pid && descendants.has(pid)).reduce((sum,row) => sum + row[2], 0) / 1024);
    if (args.includes("--pdf")) {
      await page.goto(`${origin}/evals/slides/?theme=${theme}&design=${design}&packet=intake&print`, { waitUntil: "networkidle" });
      await page.waitForFunction(() => document.querySelectorAll("#print-root .outputs-slide-face").length === 2);
      await page.emulateMedia({ media: "print" });
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
      await page.pdf({ path: path.join(output, `${theme}.pdf`), printBackground: true, preferCSSPageSize: true });
      await page.emulateMedia({ media: "screen" });
    }
  }
  completed = true;
} finally {
  await browser.close();
  results.seconds = Math.round((performance.now() - started) / 10) / 100;
  results.peakBrowserMiB = Math.round(peakBrowserMiB);
  await fs.writeFile(path.join(output, completed && !errors.length ? "measurements.json" : "measurements.incomplete.json"), JSON.stringify(results, null, 2) + "\n");
}
console.log(JSON.stringify({ slides: results.slides.length, overflows: results.slides.filter(s => s.overflow).length, missingImages: results.slides.filter(s => s.missingImages).length, errors }));
if (errors.length || results.slides.length !== expectedSlides * 3) process.exitCode = 1;
