"""Verify source, citation, and wording fidelity in the saved comparison records."""

import argparse
import json
import re
from pathlib import Path

from pypdf import PdfReader


def read(path: Path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory
    baseline = read(root / "baseline/outlines.json")
    current = read(root / "after-outlines.json")
    assert baseline["model"] == current["model"]
    old = {p["id"]: p for p in baseline["packets"]}
    assert len(old) == len(current["packets"]) == 6
    for packet in current["packets"]:
        before = old[packet["id"]]
        assert packet["inputHash"] == before["inputHash"]
        assert packet["sources"] == before["sources"]
        assert packet["cards"] == before["cards"]
        assert len(packet["outline"]) == 6
        used_before = {n for p in before["outline"] for n in p["cards"]}
        used_after = {n for p in packet["outline"] for n in p["cards"]}
        assert used_before <= used_after, packet["id"]
    legacy = read(root / "baseline/renders/measurements.json")["slides"]
    rendered = read(root / "renders/measurements.json")["slides"]
    compiled = read(root / "compiled-renders/measurements.json")["slides"]

    def key(slide):
        return slide["theme"], slide["packet"], slide["index"]

    def words(text):
        return re.sub(r"\s+", " ", text).strip()

    old_renders = {key(s): s for s in legacy}
    compiled_renders = {key(s): s for s in compiled}
    assert len(rendered) == len(legacy) == len(compiled) == 21
    for slide in rendered:
        before = old_renders[key(slide)]
        built = compiled_renders[key(slide)]
        assert words(slide["text"]) == words(before["text"]) == words(built["text"])
        assert slide["citations"] == before["citations"] == built["citations"]
        assert (
            slide.get("assetImages", [])
            == before.get("assetImages", [])
            == built.get("assetImages", [])
        )
        if slide.get("platformFonts") and built.get("platformFonts"):
            assert {f["postScriptName"] for f in slide["platformFonts"]} == {
                f["postScriptName"] for f in built["platformFonts"]
            }
        assert not slide["overflow"] and not slide["missingImages"]
        assert not built["overflow"] and not built["missingImages"]
    pdf_checks = []
    for theme in ["briefing", "editorial", "keynote"]:
        pdf = PdfReader(root / "renders" / f"{theme}.pdf")
        assert len(pdf.pages) == 3
        text = "\n".join(p.extract_text() for p in pdf.pages)
        for required in [
            "40",
            "16",
            "Quality",
            "Intake pilot timing log",
            "Intake pilot scope note",
        ]:
            assert required in text, (theme, required)
        pdf_checks.append({"theme": theme, "pages": 3, "textAndSourcesPresent": True})
    limit = read(root / "over-limit.json")
    assert len(limit["before"]) == 7 and len(limit["after"]) == 6
    assert {n for p in limit["before"] for n in p["cards"]} <= {
        n for p in limit["after"] for n in p["cards"]
    }
    result = {
        "sourcePackets": 6,
        "model": current["model"],
        "baselineRequests": sum(p["calls"] for p in baseline["packets"]),
        "afterRequests": sum(p["calls"] for p in current["packets"]),
        "overLimitRequests": limit["calls"],
        "renderedSamples": 21,
        "unchangedWordingAndCitationMarkers": True,
        "layoutWhitespaceExcluded": True,
        "sourcePacketsAndAssignedEvidencePreserved": True,
        "compiledAndDevelopmentContentMatch": True,
        "compiledAndDevelopmentFontsMatch": True,
        "overflows": 0,
        "missingAssets": 0,
        "pdfChecks": pdf_checks,
    }
    (root / "fidelity.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
