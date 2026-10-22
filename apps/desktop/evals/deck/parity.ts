// Development only: writes the fixture decks as .pptx, converts them with LibreOffice and renders PNGs,
// to compare the PowerPoint export with the on-screen slides.
//   npx vite-node apps/desktop/evals/deck/parity.ts [--out DIR] [--variants short,max,zh]
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";
import { pptxBytes } from "../../src/outputs/deck/exportPptx";
import { docxBytes } from "../../src/outputs/deck/exportDocx";
import { withDeckMarker } from "../../src/outputs/deck/marker";
import type { DeckTheme } from "../../src/outputs/sections";

const root = path.resolve(import.meta.dirname, "../../../..");
const args = process.argv.slice(2);
const option = (name: string, fallback: string) => args.includes(name) ? args[args.indexOf(name) + 1]! : fallback;
const out = path.resolve(option("--out", path.join(root, "artifacts/validation/studio-gallery/pptx")));
const variants = option("--variants", "short,max,zh").split(",");
const themes = option("--themes", "paper,ink,editorial").split(",") as DeckTheme[];
const sources = ["Harlow 2024 · p. 3", "Okafor 2025", "Lindqvist 2023 · table 4", "City procurement data 2025", "Roofing survey 2024", "Met Office heatwave review 2019", "Climate plan 2026 · section 5", "Tenant interviews 2025", "Planning dept. memo 2025"];
mkdirSync(out, { recursive: true });
for (const variant of variants) for (const theme of themes) {
  const content = withDeckMarker(readFileSync(path.join(root, `apps/desktop/src/outputs/deck/fixtures/deck.${variant}.md`), "utf8"), { theme });
  const name = `${variant}-${theme}`;
  writeFileSync(path.join(out, `${name}.pptx`), await pptxBytes(content, { sources, resolveImage: async () => `image/svg+xml;base64,${readFileSync(path.join(root, "apps/desktop/evals/deck/figure.svg")).toString("base64")}` }));
  execFileSync("soffice", ["--headless", "--convert-to", "pdf", "--outdir", out, path.join(out, `${name}.pptx`)], { stdio: "ignore", timeout: 180000 });
  execFileSync("pdftoppm", ["-png", "-r", "50", path.join(out, `${name}.pdf`), path.join(out, name)]);
  console.log(name, readdirSync(out).filter((f) => f.startsWith(`${name}-`) && f.endsWith(".png")).length, "slides");
}
if (!existsSync(out)) process.exitCode = 1;

// The report fixture as a Word file.
const report = readFileSync(path.join(root, "apps/desktop/src/outputs/deck/fixtures/report.md"), "utf8");
writeFileSync(path.join(out, "report.docx"), await docxBytes(report, { sources }));
execFileSync("soffice", ["--headless", "--convert-to", "pdf", "--outdir", out, path.join(out, "report.docx")], { stdio: "ignore", timeout: 180000 });
execFileSync("pdftoppm", ["-png", "-r", "60", path.join(out, "report.pdf"), path.join(out, "report-page")]);
console.log("report", readdirSync(out).filter((f) => f.startsWith("report-page")).length, "pages");

// Contact sheets (six slides to a picture) so a deck can be reviewed at a glance.
try {
  execFileSync("python3", ["-c", `
import sys, glob, os
from PIL import Image
out = sys.argv[1]
names = sorted({os.path.basename(f).rsplit('-',1)[0] for f in glob.glob(os.path.join(out, '*-[0-9][0-9].png'))})
for name in names:
    files = sorted(glob.glob(os.path.join(out, name + '-[0-9][0-9].png')))
    for k in range(0, len(files), 6):
        group = [Image.open(f) for f in files[k:k+6]]
        w, h = group[0].size
        sheet = Image.new('RGB', (w*2+6, h*3+12), (150,155,162))
        for i, im in enumerate(group):
            sheet.paste(im, ((i%2)*(w+6), (i//2)*(h+6)))
        sheet.save(os.path.join(out, 'sheet-%s-%d.png' % (name, k//6+1)))
`, out], { stdio: "inherit" });
} catch { /* the sheets are a convenience */ }
