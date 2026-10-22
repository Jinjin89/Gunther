# ruff: noqa: E501
"""Package actual renders, review outcomes, source checks and paired previews for inspection."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [json.loads(path.read_text()) for path in sorted(args.input.glob("*.json"))]
    if len(cases) != 15:
        raise SystemExit(f"Expected 15 comparison cases; found {len(cases)}")
    args.output.mkdir(parents=True, exist_ok=True)
    relative_input = os.path.relpath(args.input.resolve(), args.output.resolve())

    def picture(capture, index):
        return args.input / "captures" / capture["id"] / capture["slides"][index]["image"]

    def image(path):
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()

    def esc(text):
        return html.escape(str(text))

    statuses = {
        name: sum(case["review"]["status"] == name for case in cases)
        for name in ("clean", "repaired", "needs_review", "unavailable")
    }
    calls = sum(case["review"]["calls"] for case in cases)
    fidelity = all(all(case["fidelity"].values()) for case in cases)
    pdfs = all(
        case["afterCapture"].get("pdfCheck", {}).get("status") == "checked" for case in cases
    )
    rows = []
    outlines = [
        "# Before and after rendered-slide review",
        "",
        "Synthetic sources only. Layout repair preserves wording, notes, citation bindings,",
        "source images, slide identities, page count and order. It does not replan the story.",
        "",
    ]
    for case in cases:
        before, after = case["beforeCapture"], case["afterCapture"]
        review = case["review"]
        assert all(case["fidelity"].values()), case["id"]
        assert before["rendererHash"] == after["rendererHash"], case["id"]
        actual_errors = [f for f in review["findings"] if f["severity"] == "error"]
        if review["status"] in ("clean", "repaired"):
            assert not actual_errors, case["id"]
        rows.append(
            f'<tr><td><a href="#{esc(case["id"])}">{esc(case["id"])}</a></td>'
            f"<td>{esc(review['status'])}</td><td>{review['calls']}</td>"
            f"<td>{len(review['repairedSlides'])}</td><td>{len(actual_errors)}</td></tr>"
        )
        outlines += [
            f"## {case['id']}",
            "",
            f"Status: {review['status']}. Requests: {review['calls']}.",
            "",
        ]
        for i, (old, new) in enumerate(
            zip(case["before"]["outline"], case["after"]["outline"], strict=True)
        ):
            outlines.append(f"{i + 1}. {old['heading']} — {old['layout']} → {new['layout']}")
        outlines.append("")
    (args.output / "outlines.md").write_text("\n".join(outlines))
    sections = []
    ordered = sorted(cases, key=lambda case: (not case["id"].startswith("dense-"), case["id"]))
    for case in ordered:
        before, after, review = case["beforeCapture"], case["afterCapture"], case["review"]
        figures = []
        for i in range(len(before["slides"])):
            if i == 0 and case["id"].startswith("dense-"):
                continue
            figures.append(
                '<div class="pair">'
                + "".join(
                    f"<figure><figcaption>{label} · slide {i + 1}</figcaption>"
                    f'<img loading="lazy" src="{image(picture(capture, i))}" alt="{label} slide {i + 1}">'
                    f"<small>Fit {capture['slides'][i]['fit']:.2f} · "
                    f"{esc(capture['slides'][i]['drawn'])} · "
                    f"{len(capture['slides'][i]['smallText'])} text-size findings</small></figure>"
                    for label, capture in (
                        ("Before review", before),
                        ("After bounded review", after),
                    )
                )
                + "</div>"
            )
        findings = "".join(
            f"<li><strong>{esc(f['slideId'])} · {esc(f['severity'])}</strong> "
            f"{esc(f['problem'])}<p>{esc(f['correction'])} <em>Route: {esc(f['route'])}</em></p></li>"
            for f in review["findings"]
        )
        reason = f'<p class="reason">{esc(review["reason"])}</p>' if review.get("reason") else ""
        source_list = "".join(
            f"<li><strong>[{i + 1}] {esc(source['source_title'])}</strong>"
            f" · {esc(source['locator'])}<blockquote>{esc(source['quote'])}</blockquote></li>"
            for i, source in enumerate(case["after"]["citations"])
        )
        pdf = after.get("pdf")
        pdf_link = (
            f'<a href="{relative_input}/captures/{after["id"]}/{pdf}">Inspect the exported PDF</a>'
            if pdf
            else ""
        )
        sections.append(
            f'<section id="{esc(case["id"])}"><h2>{esc(case["id"])}</h2>'
            f"<p>{esc(case['theme'])} · {esc(case['use'])} · <b>{esc(review['status'])}</b> · "
            f"{review['calls']} requests · {review['repairCycles']} repair cycle · "
            f"{len(review['repairedSlides'])} pages changed.</p>{reason}"
            + "".join(figures)
            + pdf_link
            + f"<details><summary>Remaining findings ({len(review['findings'])})</summary><ul>{findings}</ul></details>"
            + f"<details><summary>Preserved sources and citations</summary><ol>{source_list}</ol></details>"
            + f'<p class="hash">Candidate {esc(review["candidateHash"])}<br>Renderer {esc(after.get("rendererHash", "unavailable"))}</p></section>'
        )
        if case["id"].startswith("dense-"):
            output = Image.new("RGB", (1280, 410), "#e7e9ec")
            draw = ImageDraw.Draw(output)
            for side, (label, capture) in enumerate(
                (("Before visual review", before), ("After bounded layout repair", after))
            ):
                with Image.open(picture(capture, 1)) as slide:
                    output.paste(slide.convert("RGB").resize((640, 360)), (side * 640, 32))
                draw.text((side * 640 + 14, 10), label, fill="#27364c")
            output.save(args.output / (case["theme"] + "-preview.png"))
    document = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gunther · rendered-slide review comparison</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef0f3;color:#1b2839;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:1440px;margin:auto;padding:36px 28px}h1{font-size:36px;letter-spacing:-.03em;line-height:1.15}h2{font-size:25px;margin:0}header{max-width:1000px;margin-bottom:30px}a{color:#1e5999}table{border-collapse:collapse;background:white;width:100%;margin:24px 0}th,td{text-align:left;padding:9px 14px;border-bottom:1px solid #e0e4e9}section{background:white;border-radius:8px;padding:24px;margin:28px 0;scroll-margin:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:18px 0}figure{margin:0;min-width:0}figcaption{font-size:13px;margin-bottom:7px;color:#435267}img{width:100%;box-shadow:0 0 0 1px #dbe0e5;cursor:zoom-in}small,.hash{font-size:11px;color:#667383}.hash{overflow-wrap:anywhere}summary{cursor:pointer;font-weight:600;padding:10px 0}li{margin:9px 0}li p{margin:4px 0}blockquote{margin:6px 0;color:#5f6977}dialog{padding:0;border:0;width:min(96vw,1500px);max-width:none}dialog img{display:block;width:100%;cursor:zoom-out}dialog::backdrop{background:#142030d9}.reason{padding:10px 14px;background:#fff1d5;border-left:3px solid #b47a17}nav{display:flex;gap:16px;flex-wrap:wrap}@media(max-width:760px){.pair{grid-template-columns:1fr}main{padding:24px 12px}section{padding:16px}h1{font-size:28px}}</style>
<main><header><p>GUNTHER · BATCHES 3–5</p><h1>Review the slide that will be saved.</h1>
<p>Actual 1280 × 720 renders from the compiled production renderer, before and after one bounded visual-review cycle. The configured Outputs model reviewed full-size images; source wording and citation identities were held constant.</p>"""
    document += f"<p><b>{sum(statuses.values())} matched cases</b> · {statuses['clean']} clean · {statuses['repaired']} repaired · {statuses['needs_review']} need review · {calls} final-run model requests.</p>"
    document += f"<p>Exact wording, notes, sources, citation bindings, slide identities and order preserved: <b>{fidelity}</b>. Exported PDF text/source checks: <b>{pdfs}</b>.</p>"
    document += "<p>The six packets run in both speaking and reading modes. Three dense-scene fixtures show the repair limit; the before images use the same renderer and theme as the after images. Failed repairs retain the previous usable deck. Diagnostics stay outside the slides.</p>"
    document += '<nav><a href="#dense-briefing">Briefing repair</a><a href="#dense-editorial">Editorial repair</a><a href="#dense-keynote">Keynote repair</a><a href="outlines.md">Before/after outlines</a></nav></header>'
    document += (
        "<table><thead><tr><th>Case</th><th>Final visual status</th><th>Requests</th><th>Pages changed</th><th>Severe findings</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    document += "".join(sections)
    document += '<p>Synthetic evaluation material. These comparisons isolate visual review and repair; free outline planning and source extraction are separate checks. Final-run request totals exclude earlier diagnostic probes. Click any slide to inspect it at full size.</p></main><dialog><img alt="Full-size inspected slide"></dialog><script>var dialog=document.querySelector("dialog");document.querySelectorAll("main img").forEach(function(img){img.onclick=function(){dialog.querySelector("img").src=img.src;dialog.showModal()}});dialog.onclick=function(){dialog.close()};</script></html>'
    (args.output / "comparison.html").write_text(document)
    (args.output / "comparison-summary.json").write_text(
        json.dumps(
            {
                "cases": len(cases),
                "statuses": statuses,
                "calls": calls,
                "fidelity": fidelity,
                "pdfs": pdfs,
                "model": cases[0]["model"],
                "effort": cases[0]["effort"],
                "rendererHashes": sorted({c["afterCapture"].get("rendererHash") for c in cases}),
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "cases": len(cases),
                "statuses": statuses,
                "calls": calls,
                "fidelity": fidelity,
                "pdfs": pdfs,
            }
        )
    )


if __name__ == "__main__":
    main()
