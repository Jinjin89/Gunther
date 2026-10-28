"""Exercise long-deck capture orchestration without launching sockets or a browser."""

import base64
import io
import json
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from test_slide_review import deck

from gunther import slide_render as render


@pytest.mark.parametrize("stale", ["none", "hash", "range", "indices", "print-indices"])
def test_capture_batches_keep_whole_candidate_and_merge_in_order(tmp_path, monkeypatch, stale):
    candidate = deck(pages=render.CAPTURE_BATCH * 2 + 1)
    host = tmp_path / "host"
    host.mkdir()
    (host / "manifest.json").write_text(
        json.dumps({"entry": "index.html", "files": {}, "rendererHash": "fixture"})
    )
    held = {}
    navigations = []
    screenshot_media = []
    png = io.BytesIO()
    Image.new("RGB", (16, 9), "white").save(png, format="PNG")

    @contextmanager
    def serve(_root, payload, _assets):
        held.update(payload)
        yield "http://capture.test", "token"

    class Process:
        def __init__(self, args, **_kwargs):
            profile = Path(
                next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir="))
            )
            profile.mkdir()
            (profile / "DevToolsActivePort").write_text("1234")

        def terminate(self):
            pass

        def wait(self, **_kwargs):
            pass

    class CDP:
        def __init__(self, *_):
            self.media = "screen"
            self.start, self.end = 0, 0

        def call(self, name, params=None):
            params = params or {}
            if name == "Emulation.setEmulatedMedia":
                self.media = params["media"]
            if name == "Page.navigate":
                assert self.media == "screen"
                query = parse_qs(urlsplit(params["url"]).query)
                self.start, self.end = int(query["from"][0]), int(query["to"][0])
                navigations.append((self.start, self.end))
            if name == "DOM.getDocument":
                return {"root": {"nodeId": 1}}
            if name == "DOM.querySelectorAll":
                return {"nodeIds": []}
            if name == "Page.captureScreenshot":
                screenshot_media.append(self.media)
                return {"data": base64.b64encode(png.getvalue()).decode()}
            if name == "Page.printToPDF":
                assert self.media == "print"
                pdf = PdfWriter()
                for _ in range(self.end - self.start):
                    pdf.add_blank_page(width=960, height=540)
                stream = io.BytesIO()
                pdf.write(stream)
                return {"data": base64.b64encode(stream.getvalue()).decode()}
            if name == "Browser.getVersion":
                return {"product": "fixture"}
            return {}

        def evaluate(self, expression):
            print_view = "ready(true)" in expression
            start, end = self.start, self.end
            if start and stale == "range":
                start, end = 0, render.CAPTURE_BATCH
            elif start and (
                (stale == "indices" and not print_view)
                or (stale == "print-indices" and print_view)
            ):
                start, end = 0, end - start
            return {
                "version": 1,
                "candidateHash": "stale" if stale == "hash" else held["candidateHash"],
                "slides": [
                    {"id": held["slideIds"][n], "index": n, "typography": []}
                    for n in range(start, end)
                ],
            }

    monkeypatch.setattr(render, "_host", serve)
    monkeypatch.setattr(render, "browser_location", lambda _: (Path("fixture"), "fixture"))
    monkeypatch.setattr(render.subprocess, "Popen", Process)
    monkeypatch.setattr(
        render,
        "urlopen",
        lambda *a, **k: io.BytesIO(b'[{"type":"page","webSocketDebuggerUrl":"ws://fixture"}]'),
    )
    monkeypatch.setattr(render, "connect", lambda *a, **k: nullcontext(None))
    monkeypatch.setattr(render, "_CDP", CDP)
    renderer = render.SlideRenderer(tmp_path / "cache", lambda _: None, host=host)
    if stale != "none":
        with pytest.raises(render.RenderError, match="out of date|renderer is stale"):
            renderer.capture(candidate)
        return
    snapshot = renderer.capture(candidate)
    count = len(candidate.outline)
    assert navigations == [(0, 8), (8, 16), (16, 17)]
    assert screenshot_media == ["screen"] * count
    assert held["artifact"]["content"] == candidate.content
    assert held["artifact"]["citations"] == [
        c.model_dump(mode="json", by_alias=True) for c in candidate.citations
    ]
    assert held["slideIds"] == [p["id"] for p in candidate.outline]
    assert [s["index"] for s in snapshot["slides"]] == list(range(count))
    assert [s["id"] for s in snapshot["pdfCheck"]["slides"]] == held["slideIds"]
    assert [s["image"] for s in snapshot["slides"]] == [
        f"slide-{n:02}.png" for n in range(1, count + 1)
    ]
    assert len(PdfReader(renderer.file(snapshot["id"], "deck.pdf")).pages) == count
