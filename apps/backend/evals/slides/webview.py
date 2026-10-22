"""Compare compiled production slides in Chromium and macOS WKWebView (Tauri's engine)."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from gunther.slide_render import _host, renderer_location


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    artifact = {**case["after"], "kind": "slides", "versionNumber": 1, "createdAt": "2026-01-01"}
    # Fixture JSON uses internal field names; the real renderer receives the API contract.
    from gunther.schemas import ConversationCitationOut

    artifact["citations"] = [
        ConversationCitationOut.model_validate(c).model_dump(mode="json", by_alias=True)
        for c in artifact["citations"]
    ]
    capture = case["afterCapture"]
    payload = {
        "artifact": artifact,
        "candidateHash": capture["candidateHash"],
        "slideIds": [p["id"] for p in artifact["outline"]],
        "use": case["use"],
    }
    figure = Path(__file__).parents[3] / "desktop/evals/slides/source-figure.svg"
    with _host(
        renderer_location(), payload, {"eval-collection": (figure.read_bytes(), "image/svg+xml")}
    ) as (base, token):
        subprocess.run(
            [
                str(args.binary.resolve()),
                base + "/render-slides/index.html",
                token,
                str(args.output.resolve()),
            ],
            timeout=65,
            check=True,
        )
    native = json.loads((args.output / "webview.json").read_text())
    differences = []
    for a, b in zip(capture["slides"], native["slides"], strict=True):
        if a["drawn"] != b["drawn"] or abs(a["fit"] - b["fit"]) > 0.02:
            differences.append(
                {
                    "slideId": a["id"],
                    "field": "drawing/fit",
                    "chromium": [a["drawn"], a["fit"]],
                    "webview": [b["drawn"], b["fit"]],
                }
            )
        if len(a["typography"]) != len(b["typography"]):
            differences.append({"slideId": a["id"], "field": "text-elements"})
            continue
        for x, y in zip(a["typography"], b["typography"], strict=True):
            # One pixel rounding and small engine ink-metric differences are harmless.
            delta = max(
                abs(x["bounds"][key] - y["bounds"][key]) for key in ("x", "y", "width", "height")
            )
            if delta > 3 or abs(x["size"] - y["size"]) > 0.5:
                differences.append(
                    {
                        "slideId": a["id"],
                        "field": x["role"],
                        "text": x["text"][:70],
                        "maxPixelDifference": round(delta, 2),
                    }
                )
        if b["assetFailures"] or b["pending"]:
            differences.append({"slideId": a["id"], "field": "assets-or-graphics"})
    result = {
        "case": case["id"],
        "candidateHash": native["candidateHash"],
        "slides": len(native["slides"]),
        "status": "matched" if not differences else "differences",
        "tolerancePixels": 3,
        "scope": "macOS WKWebView engine probe, not a full packaged-app interaction test",
        "differences": differences,
    }
    (args.output / "parity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
