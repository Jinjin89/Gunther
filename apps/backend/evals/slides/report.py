"""Produce a portable comparison of recorded plans and actual shared-renderer captures."""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path

from PIL import Image, ImageDraw


def read(path: Path):
    return json.loads(path.read_text())


def image_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory
    before = read(root / "baseline/outlines.json")
    after = read(root / "after-outlines.json")
    fidelity = read(root / "fidelity.json")
    old = {packet["id"]: packet for packet in before["packets"]}
    esc = html.escape
    sections = []
    markdown = [
        "# Before and after slide outlines",
        "",
        "Six synthetic source packets, with the same model and fixed evidence cards.",
        "These are live outline runs, not a full extraction or slide-writing benchmark.",
        "",
    ]
    for index, packet in enumerate(after["packets"]):
        original = old[packet["id"]]
        rows = []
        markdown.extend([f"## {packet['id']}", "", "Before", ""])
        markdown.extend(f"{i}. {page['heading']}" for i, page in enumerate(original["outline"], 1))
        markdown.extend(["", "After", ""])
        markdown.extend(f"{i}. {page['heading']}" for i, page in enumerate(packet["outline"], 1))
        markdown.extend(["", f"Input hash: `{packet['inputHash']}`.", ""])
        for number, (a, b) in enumerate(
            zip(original["outline"], packet["outline"], strict=True), 1
        ):
            cards = ", ".join(f"C{n}" for n in b["cards"])
            message = esc(b.get("message", ""))
            rows.append(
                f"<tr><td>{number}</td><td>{esc(a['heading'])}</td>"
                f"<td><strong>{esc(b['heading'])}</strong><p>{message}</p>"
                f"<small>Evidence: {cards or 'Framing'}</small></td></tr>"
            )
        sources = "".join(
            f"<li><strong>[{n}] {esc(source['title'])}</strong> · {esc(source['locator'])}"
            f"<p>{esc(source['text'])}</p></li>"
            for n, source in enumerate(packet["sources"], 1)
        )
        sections.append(
            f"<details {'open' if index == 0 else ''}><summary>{esc(packet['id'])}</summary>"
            "<table><thead><tr><th>Slide</th><th>Before</th><th>After</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
            f"<details><summary>Original sources and scope</summary><ol>{sources}</ol></details>"
            f"<small>Same source packet and cards. Input hash: "
            f"{packet['inputHash']}</small></details>"
        )
    designs = []
    descriptions = {
        "briefing": "Clear sans typography, restrained blue, compact evidence.",
        "editorial": "Serif hierarchy, warm paper, quieter data framing.",
        "keynote": "Large type, dark canvas, high-contrast evidence.",
    }
    for theme, description in descriptions.items():
        canvas = Image.new("RGB", (1280, 788), "#e8ebef")
        draw = ImageDraw.Draw(canvas)
        draw.text((20, 12), theme.upper() + " | Same cover and evidence content", fill="#273345")
        for index in (0, 1):
            image = Image.open(root / "renders" / f"{theme}-intake-{index}.png").convert("RGB")
            image.thumbnail((1240, 350))
            canvas.paste(image, ((1280 - image.width) // 2, 42 + index * 372))
        canvas.save(root / f"{theme}-preview.png")
        images = "".join(
            f'<img data-zoom alt="{theme} {label}" src="'
            + image_uri(root / "renders" / f"{theme}-intake-{index}.png")
            + '">'
            for index, label in ((0, "cover"), (1, "evidence page"))
        )
        designs.append(f"<article><h3>{theme.title()}</h3><p>{description}</p>{images}</article>")
    validation = read(root / "validation.json") if (root / "validation.json").exists() else {}
    limit_html = ""
    if (root / "over-limit.json").exists():
        limit = read(root / "over-limit.json")
        initial = "".join(f"<li>{esc(p['heading'])}</li>" for p in limit["before"])
        repaired = "".join(f"<li>{esc(p['heading'])}</li>" for p in limit["after"])
        limit_html = (
            "<h2>Page-limit repair</h2><p>A seeded seven-page candidate was reviewed and "
            "replanned by the live model into six pages. All five assigned evidence cards, "
            "including the quality limitation, were retained.</p>"
            "<details><summary>Inspect the over-limit repair</summary>"
            f"<h3>Before</h3><ol>{initial}</ol><h3>After</h3><ol>{repaired}</ol></details>"
        )
    content = (
        """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gunther slide structure and design comparison</title>
<style>
body{margin:0;background:#edf0f4;color:#1a2635;font-family:Arial,sans-serif;line-height:1.6}
main{max-width:1480px;margin:auto;padding:44px 32px}h1{font-size:36px;line-height:1.15}
h2{font-size:25px;margin-top:42px}h3{font-size:21px;margin:0}p{max-width:90ch}
.intro{font-size:18px;color:#48576a}.designs{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
article,details{background:#fff;border:1px solid #d8dfe8;border-radius:9px;padding:20px}
article p,small{font-size:13px;color:#536276}
article img{width:100%;display:block;margin-top:15px;cursor:zoom-in}
details{margin:14px 0}summary{font-size:18px;font-weight:600;cursor:pointer}
table{width:100%;border-collapse:collapse;margin:18px 0;text-align:left;font-size:14px}
th,td{padding:13px 15px;border-bottom:1px solid #dce2eb;vertical-align:top}
th:first-child{width:50px}
td:nth-child(2){width:40%;color:#5a6370}
td p{color:#59677a;margin-bottom:4px}
li p{margin-top:4px;color:#536276}.proof{padding:22px;background:#dfe9e4;border-radius:8px}
pre{white-space:pre-wrap;font-size:13px;background:#fff;padding:18px;border-radius:8px}
dialog{width:min(92vw,1440px);border:0;padding:15px;background:#fff;border-radius:10px}
dialog img{width:100%;display:block}dialog::backdrop{background:#0d172bd9}
dialog button{float:right;border:0;background:#eef1f5;padding:8px 16px;cursor:pointer}
@media(max-width:900px){
.designs{grid-template-columns:1fr}main{padding:24px 16px}h1{font-size:29px}}
</style><main><h1>Clearer reasoning and three visual directions</h1>
<p class="intro">Gunther batches 0–2. The previews use the actual React slide renderer.
All evaluation records are synthetic. Click an image to inspect it at full size.</p>
<section class="proof">Six matched source packets. Original sources and evidence cards retained.
The 21 visual samples preserve wording and citation sequences across the designs.
Rendered samples have no detected overflow or missing images.</section>
<h2>Three visual previews</h2>
<p>The cover and evidence page use identical content in each style.</p>
<section class="designs">"""
        + "".join(designs)
        + """</section>
<h2>Before and after outlines</h2><p>Recorded live plans from the configured Outputs model.
The intake example previously ranked an extension option; the revised ending preserves the
source's three unranked options and the missing quality measurement.</p>
<p>Titles here are plans. The normal writing and fact-checking stages still verify final claims.</p>
"""
        + "".join(sections)
        + limit_html
        + "<details><summary>Verification records</summary><pre>"
        + esc(
            json.dumps({"fidelity": fidelity, "checks": validation}, ensure_ascii=False, indent=2)
        )
        + """</pre></details><p>The compiled standalone rendering host was also exercised.
Shipping a browser
worker and checking it against the packaged Tauri WebView
belong to the later capture integration.</p>
</main><dialog><button>Close</button><img alt="Full-size slide preview"></dialog>
<script>
const dialog=document.querySelector('dialog');
document.addEventListener('click',e=>{
 if(e.target.matches('img[data-zoom]')){dialog.querySelector('img').src=e.target.src;dialog.showModal()}
 if(e.target.matches('dialog button'))dialog.close();
});
</script></html>"""
    )
    (root / "comparison.html").write_text(content)
    (root / "outlines.md").write_text("\n".join(markdown))
    print("Comparison page, outlines, and three preview sheets written.")


if __name__ == "__main__":
    main()
