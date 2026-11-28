// Real-browser report layout checks. Chromium uses the pinned slide browser.
// node apps/desktop/evals/deck/report-layout.mjs [--browser webkit] [--origin URL] [--out DIR]
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, webkit } from "playwright";
import { createServer } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../../../..");
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const engine = option("--browser", "chromium");
assert.ok(["chromium", "webkit"].includes(engine), "Use --browser chromium or webkit");
const out = path.resolve(option("--out", path.join(root, "artifacts/validation/studio-gallery/report-layout")));
await fs.mkdir(out, { recursive: true });
const table = [":::exhibit", "Recorded observations [1]", "| Group | Side | Components |", "| --- | --- | --- |", "| First structure | Left | Two components recorded together [1] |", "| Second structure | Right | None [1] |", "Source: Saved observations [1]", ":::"].join("\n");
const dense = [`| ${Array.from({ length: 12 }, (_, i) => `Measurement ${i + 1}`).join(" | ")} |`, `| ${Array(12).fill("---").join(" | ")} |`, `| ${Array.from({ length: 12 }, (_, i) => `Recorded value ${i + 1}`).join(" | ")} |`].join("\n");
const chart = [":::exhibit chart=column", "Measured values [1]", "| Period | Count |", "| --- | --- |", "| First | 4 |", "| Second | 7 |", ":::"].join("\n");
const documentFor = (count) => "# Recorded observations\n\n" + Array.from({ length: count }, (_, i) => `## Observation ${i + 1}\n\nThe evidence explains the observation and its context [1].\n\n${i === 0 ? [table, dense, chart].join("\n\n") : "The next observation follows the preceding evidence."}`).join("\n\n");
async function settled(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

let server;
let browser;
const results = [];
try {
  let origin = option("--origin");
  if (!origin) {
    server = await createServer({ configFile: path.join(here, "vite.config.ts"), logLevel: "error", server: { port: 0, strictPort: false } });
    await server.listen();
    origin = server.resolvedUrls.local[0].replace(/\/$/, "");
  }
  const manifest = engine === "chromium" ? JSON.parse(await fs.readFile(path.join(root, "tools/slide-browser/.cache/manifest.json"), "utf8")) : null;
  browser = await (engine === "webkit" ? webkit.launch() : chromium.launch({ executablePath: process.env.CHROME_BIN ?? path.join(root, "tools/slide-browser/.cache", manifest.executable) }));
  const cases = ["report", "briefing", "faq", "study_guide", "timeline"].flatMap((kind) => [360, 760, 1600].flatMap((width) => [1, 2].map((scale) => ({ kind, width, scale, count: 11 }))));
  cases.push({ kind: "timeline", width: 760, scale: 2, count: 120 });
  for (const current of cases) {
    const page = await browser.newPage({ viewport: { width: current.width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.addInitScript((value) => { window.__GALLERY__ = value; }, { kind: current.kind, content: documentFor(current.count), citations: [{ title: "Saved observations" }] });
      await page.goto(`${origin}/evals/deck/report.html`);
      await page.locator(".rp-exhibit[data-layout=table]").waitFor();
      await page.evaluate((scale) => document.documentElement.style.setProperty("--gx-fs", String(scale)), current.scale);
      await settled(page);
      const figure = page.locator(".rp-exhibit[data-layout=table]");
      const initialWidth = await figure.evaluate((el) => el.getBoundingClientRect().width);
      await figure.evaluate((el) => {
        el.querySelector("figcaption .answer-markdown").textContent = "A detailed description of the recorded components, their grouping and their original observations. ".repeat(6);
        el.querySelector(".rp-source .answer-markdown").textContent = "Source: Original field notes, detailed attribution and supporting measurements. ".repeat(4);
      });
      await page.locator(".rp-data").evaluateAll((elements) => elements.forEach((el) => { el.open = true; }));
      await settled(page);
      const contents = page.locator(".rp-contents");
      const navHeight = await page.locator(".rp-navigation").evaluate((el) => el.getBoundingClientRect().height);
      if (await contents.count()) await contents.locator("summary").click();
      const measured = await page.evaluate(() => {
        const pane = document.querySelector(".library-middle-page"), figure = document.querySelector(".rp-exhibit[data-layout=table]");
        const rail = document.querySelector(".rp-events > li"), list = document.querySelector(".rp-contents ol");
        let markerRoom = true;
        if (list) {
          const style = getComputedStyle(list), canvas = document.createElement("canvas").getContext("2d");
          canvas.font = style.font;
          markerRoom = parseFloat(style.paddingInlineStart) >= canvas.measureText(`${list.children.length}. `).width;
        }
        return {
          width: figure.getBoundingClientRect().width,
          paneOverflow: pane.scrollWidth - pane.clientWidth,
          captionOverflow: [...figure.querySelectorAll("figcaption,.rp-source")].some((el) => el.scrollWidth > el.clientWidth + 1),
          tables: [...document.querySelectorAll(".rp-table")].map((el) => ({ width: el.clientWidth, scroll: el.scrollWidth, height: el.getBoundingClientRect().height, role: el.getAttribute("role"), tabIndex: el.tabIndex, hiddenData: Boolean(el.closest("details:not([open])")) })),
          accessibleTables: [...document.querySelectorAll(".rp-table")].every((el) => el.scrollWidth <= el.clientWidth + 1 || el.getAttribute("role") === "region" && el.tabIndex === 0),
          contained: [...document.querySelectorAll(".rp-table,.rp-exhibit")].every((el) => { const r = el.getBoundingClientRect(), p = pane.getBoundingClientRect(); return r.left >= p.left - 1 && r.right <= p.right + 1; }),
          railClear: !rail || rail.getBoundingClientRect().left + parseFloat(getComputedStyle(rail, "::before").left) + parseFloat(getComputedStyle(rail, "::before").width) <= rail.querySelector(".rp-part-head").getBoundingClientRect().left,
          markerRoom,
          navHeight: document.querySelector(".rp-navigation").getBoundingClientRect().height,
        };
      });
      // One CSS pixel allows fractional DOMRect and integer clientWidth rounding.
      assert.ok(Math.abs(initialWidth - measured.width) <= 1, `Caption stretched the table: ${JSON.stringify({ current, initialWidth, measured })}`);
      assert.ok(measured.paneOverflow <= 1 && !measured.captionOverflow && measured.accessibleTables && measured.contained && measured.railClear && measured.markerRoom, JSON.stringify({ current, measured }));
      assert.ok(Math.abs(navHeight - measured.navHeight) <= 1, "Contents moved the report");
      if (await contents.count()) {
        await contents.locator("a").first().focus();
        await page.keyboard.press("Escape");
        assert.equal(await contents.getAttribute("open"), null);
        assert.equal(await contents.locator("summary").evaluate((el) => el === document.activeElement), true);
      }
      assert.deepEqual(errors, []);
      if (current.width === 1600 && current.scale === 1 && ["report", "timeline"].includes(current.kind)) {
        await figure.scrollIntoViewIfNeeded();
        await page.screenshot({ path: path.join(out, `${engine}-${current.kind}.png`) });
      }
      results.push({ ...current, ...measured });
      console.log(`PASS ${engine} ${current.kind} ${current.width}px ${current.scale}x ${current.count} sections`);
    } finally { await page.close(); }
  }
  await fs.writeFile(path.join(out, `${engine}.json`), JSON.stringify(results, null, 2));
  console.log(`${results.length} ${engine} report layout scenarios passed.`);
} finally {
  await browser?.close();
  await server?.close();
}
