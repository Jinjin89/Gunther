"""Local, bounded capture of the production React slide renderer through Chromium CDP.

No Node or development server runs in the installed application. A private loopback
server exposes only this candidate, its referenced managed images and the compiled host.
Screenshots are caches, never replacement sources or citation identities.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable
from contextlib import contextmanager
from functools import cache
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore, Thread
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen
from uuid import uuid4

from PIL import Image, ImageDraw
from pypdf import PdfReader
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from gunther.outputs import BuiltOutput, split_document
from gunther.slide_scene import scene_marker, source_hash, split_blocks

CAPTURE_VERSION = 1
_CAPTURE_SLOT = BoundedSemaphore(1)
AssetReader = Callable[[str], tuple[Path, str, str]]


class RenderError(RuntimeError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@cache
def _equivalent_ideographs() -> dict[int, str]:
    """Official glyph equivalence for PDF comparison only; source text is never normalized."""
    path = Path(__file__).with_name("data") / "unicode/EquivalentUnifiedIdeograph-17.0.0.txt"
    result = {}
    for line in path.read_text().splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        codepoints, unified = (field.strip() for field in line.split(";"))
        bounds = codepoints.split("..")
        for point in range(int(bounds[0], 16), int(bounds[-1], 16) + 1):
            result[point] = chr(int(unified, 16))
    return result


def pdf_glyph_text(text: str) -> str:
    return "".join(
        unicodedata.normalize("NFKC", text.translate(_equivalent_ideographs())).casefold().split()
    )


def candidate_hash(content: str, outline: list, citations: list) -> str:
    # JSON-mode dumps have stable timestamp values, rather than Python datetime objects.
    return digest({"content": content, "outline": outline, "citations": citations})


def built_hash(built: BuiltOutput) -> str:
    return candidate_hash(
        built.content, built.outline, [item.model_dump(mode="json") for item in built.citations]
    )


def renderer_location() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    return (
        Path(frozen) / "slide-renderer"
        if frozen
        else (Path(__file__).resolve().parents[2] / "desktop/render-dist")
    )


def browser_location(explicit: Path | None = None) -> tuple[Path, str]:
    if explicit:
        if not explicit.is_file():
            raise RenderError("The configured slide browser is unavailable.")
        return explicit, "configured"
    frozen = getattr(sys, "_MEIPASS", None)
    root = (
        (
            Path(frozen).parent / "Resources/slide-browser"
            if sys.platform == "darwin"
            else Path(frozen) / "slide-browser"
        )
        if frozen
        else (Path(__file__).resolve().parents[3] / "tools/slide-browser/.cache")
    )
    manifest = root / "manifest.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text())
        executable = (root / data["executable"]).resolve()
        if (
            executable.is_relative_to(root.resolve())
            and executable.is_file()
            and hashlib.sha256(executable.read_bytes()).hexdigest() == data["sha256"]
        ):
            return executable, f"bundled:{data['revision']}"
        raise RenderError("The bundled slide browser failed its integrity check.")
    # An explicit fallback for developer/reduced installations. Never download at run time.
    for name in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ]:
        if name and Path(name).is_file():
            return Path(name), "system"
    raise RenderError(
        "Slide capture needs the bundled browser. Rebuild with prepare:slide-browser."
    )


class _CDP:
    def __init__(self, socket: Any, deadline: float):
        self.socket, self.deadline, self.number = socket, deadline, 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.number += 1
        self.socket.send(json.dumps({"id": self.number, "method": method, "params": params or {}}))
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise RenderError("Slide capture exceeded its time limit.")
            try:
                result = json.loads(self.socket.recv(timeout=remaining))
            except WebSocketException as error:
                raise RenderError("The local slide browser disconnected during capture.") from error
            if result.get("id") != self.number:
                continue
            if "error" in result:
                raise RenderError(f"The slide browser could not run {method}.")
            return result.get("result", {})

    def evaluate(self, expression: str) -> Any:
        value = self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        if value.get("exceptionDetails"):
            detail = value["exceptionDetails"].get("exception", {}).get("description", "")
            raise RenderError("Slide rendering did not settle. " + detail[:350])
        return value.get("result", {}).get("value")


@contextmanager
def _host(root: Path, payload: dict, assets: dict[str, tuple[bytes, str]]):
    token = secrets.token_urlsafe(32)
    content = json.dumps(payload, ensure_ascii=False).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_GET(self) -> None:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            held = cookie.get("gunther_capture")
            if not (
                secrets.compare_digest(self.headers.get("X-Gunther-Capture", ""), token)
                or held
                and secrets.compare_digest(held.value, token)
            ):
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path == "/render-slides/candidate.json":
                data, media = content, "application/json"
            elif path.startswith("/api/assets/") and path.rsplit("/", 1)[-1] in assets:
                data, media = assets[path.rsplit("/", 1)[-1]]
            else:
                target = (root / path.lstrip("/")).resolve()
                if not target.is_relative_to(root.resolve()) or not target.is_file():
                    self.send_error(404)
                    return
                data, media = (
                    target.read_bytes(),
                    mimetypes.guess_type(target.name)[0] or ("application/octet-stream"),
                )
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Set-Cookie", f"gunther_capture={token}; HttpOnly; SameSite=Strict; Path=/"
            )
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                "font-src 'self' data:; connect-src 'self'; object-src 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class SlideRenderer:
    def __init__(
        self,
        cache: Path,
        asset_reader: AssetReader,
        *,
        host: Path | None = None,
        browser: Path | None = None,
        timeout: float = 90,
    ):
        self.cache, self.asset_reader = cache, asset_reader
        self.host, self.browser, self.timeout = host or renderer_location(), browser, timeout

    def current(self, snapshot: dict) -> bool:
        try:
            manifest = json.loads((self.host / "manifest.json").read_text())
            if snapshot.get("rendererHash") != manifest["rendererHash"]:
                return False
            for identity, expected in snapshot.get("assetHashes", {}).items():
                path, _media, _name = self.asset_reader(identity)
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    return False
            return True
        except (OSError, ValueError, LookupError, KeyError):
            return False

    def capture(self, built: BuiltOutput, use: str = "talk") -> dict:
        started = time.monotonic()
        if not _CAPTURE_SLOT.acquire(timeout=min(self.timeout, 30)):
            raise RenderError("Another deck is being captured. Try this deck again shortly.")
        try:
            return self._capture(built, use, started)
        except RenderError:
            raise
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            StopIteration,
            TimeoutError,
            WebSocketException,
            subprocess.SubprocessError,
        ) as error:
            raise RenderError(f"Slide capture is unavailable: {error}") from error
        finally:
            _CAPTURE_SLOT.release()

    def _capture(self, built: BuiltOutput, use: str, started: float) -> dict:
        manifest_file = self.host / "manifest.json"
        if not manifest_file.is_file():
            raise RenderError("The slide renderer is missing. Run npm run build:renderer.")
        manifest = json.loads(manifest_file.read_text())
        for name, expected in manifest["files"].items():
            path = (self.host / name).resolve()
            if not path.is_relative_to(self.host.resolve()) or (
                hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                raise RenderError("The compiled slide renderer failed its integrity check.")
        browser, distribution = browser_location(self.browser)
        _preamble, sections = split_document(built.content, "slides")
        if not 1 <= len(sections) <= 14:
            raise RenderError("Capture supports one to fourteen slides.")
        ids = [
            str(built.outline[i].get("id") or f"slide-{i + 1}")
            if i < len(built.outline)
            else f"slide-{i + 1}"
            for i in range(len(sections))
        ]
        if len(set(ids)) != len(ids):
            raise RenderError("Slide identities must be unique before capture.")
        citations = [item.model_dump(mode="json", by_alias=True) for item in built.citations]
        payload = {
            "candidateHash": built_hash(built),
            "slideIds": ids,
            "use": use,
            "artifact": {
                "kind": "slides",
                "title": built.title,
                "content": built.content,
                "outline": built.outline,
                "citations": citations,
                "versionNumber": 1,
                "createdAt": "2026-01-01T00:00:00Z",
            },
        }
        assets, asset_hashes, missing = {}, {}, []
        for asset_id in sorted(set(re.findall(r"gunther-asset:([A-Za-z0-9_-]+)", built.content))):
            try:
                path, media, _name = self.asset_reader(asset_id)
                if not media.startswith("image/") or path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError("The source figure is not a supported image.")
                data = path.read_bytes()
                assets[asset_id] = (data, media)
                asset_hashes[asset_id] = hashlib.sha256(data).hexdigest()
            except (OSError, ValueError, LookupError):
                missing.append(asset_id)
                asset_hashes[asset_id] = "unavailable"
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        snapshot_id = uuid4().hex
        with tempfile.TemporaryDirectory(prefix="capture-", dir=self.cache) as scratch:
            scratch_path = Path(scratch)
            output = scratch_path / "output"
            output.mkdir(mode=0o700)
            profile = scratch_path / "browser-profile"
            deadline = started + self.timeout
            with _host(self.host, payload, assets) as (base, token):
                process = subprocess.Popen(
                    [
                        str(browser),
                        "--headless",
                        "--disable-gpu",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-sync",
                        "--hide-scrollbars",
                        "--remote-debugging-port=0",
                        f"--user-data-dir={profile}",
                        "about:blank",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    port_file = profile / "DevToolsActivePort"
                    while not port_file.is_file():
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RenderError("The local slide browser could not start.")
                        time.sleep(0.04)
                    port = int(port_file.read_text().splitlines()[0])
                    with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                        tabs = json.load(response)
                    address = next(
                        tab["webSocketDebuggerUrl"] for tab in tabs if tab.get("type") == "page"
                    )
                    with connect(address, open_timeout=5, max_size=32 * 1024 * 1024) as socket:
                        cdp = _CDP(socket, deadline)
                        cdp.call("Page.enable")
                        cdp.call("DOM.enable")
                        cdp.call("CSS.enable")
                        cdp.call("Network.enable")
                        cdp.call(
                            "Network.setExtraHTTPHeaders", {"headers": {"X-Gunther-Capture": token}}
                        )
                        cdp.call(
                            "Emulation.setDeviceMetricsOverride",
                            {"width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False},
                        )
                        cdp.call("Page.navigate", {"url": f"{base}/{manifest['entry']}"})
                        result = cdp.evaluate(
                            "(async()=>{const end=Date.now()+20000;"
                            "while(!window.guntherCapture&&Date.now()<end)"
                            "await new Promise(r=>setTimeout(r,50));"
                            "if(!window.guntherCapture)throw Error('Capture host did not start');"
                            "return await window.guntherCapture.ready()})()"
                        )
                        if result["candidateHash"] != built_hash(built):
                            raise RenderError("The rendered candidate is out of date.")
                        document = cdp.call("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
                        for i, slide in enumerate(result["slides"]):
                            shot = cdp.call(
                                "Page.captureScreenshot",
                                {
                                    "format": "png",
                                    "captureBeyondViewport": True,
                                    "clip": {
                                        "x": 0,
                                        "y": i * 720,
                                        "width": 1280,
                                        "height": 720,
                                        "scale": 1,
                                    },
                                },
                            )
                            filename = f"slide-{i + 1:02}.png"
                            (output / filename).write_bytes(base64.b64decode(shot["data"]))
                            slide["image"] = filename
                            fonts = []
                            nodes = cdp.call(
                                "DOM.querySelectorAll",
                                {
                                    "nodeId": document,
                                    "selector": f"#capture-slides article:nth-child({i + 1}) "
                                    ":is(h1,h2,p,td,svg text)",
                                },
                            )["nodeIds"]
                            for node in nodes[:12]:
                                fonts.extend(
                                    cdp.call("CSS.getPlatformFontsForNode", {"nodeId": node})[
                                        "fonts"
                                    ]
                                )
                            slide["resolvedFonts"] = sorted(
                                {font["postScriptName"] for font in fonts if font["glyphCount"] > 0}
                            )
                            slide["contentHash"] = source_hash(sections[i])
                            slide["sceneHash"] = digest(scene_marker(sections[i])[0])
                            slide["blockContent"] = [
                                {"id": block.id, "hash": digest(block.markdown)}
                                for block in split_blocks(sections[i])[1]
                            ]
                        cdp.call("Emulation.setEmulatedMedia", {"media": "print"})
                        print_result = cdp.evaluate("window.guntherCapture.ready(true)")
                        pdf = cdp.call(
                            "Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True}
                        )
                        (output / "deck.pdf").write_bytes(base64.b64decode(pdf["data"]))
                        browser_version = cdp.call("Browser.getVersion")["product"]
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            contact = Image.new("RGB", (640, ((len(sections) + 1) // 2) * 202), "#e7e9ec")
            draw = ImageDraw.Draw(contact)
            for i, slide in enumerate(result["slides"]):
                with Image.open(output / slide["image"]) as original:
                    contact.paste(
                        original.convert("RGB").resize((320, 180)), ((i % 2) * 320, (i // 2) * 202)
                    )
                draw.text(
                    ((i % 2) * 320 + 8, (i // 2) * 202 + 183), f"{i + 1}. {ids[i]}", fill="#28384c"
                )
            contact.save(output / "contact.png")
            pdf_reader = PdfReader(output / "deck.pdf")

            def flatten(text):
                return "".join(unicodedata.normalize("NFKC", text).casefold().split())

            raw_pdf_text = "\n".join(page.extract_text() for page in pdf_reader.pages)
            pdf_text = pdf_glyph_text(raw_pdf_text)
            strict_pdf_text = flatten(raw_pdf_text)
            missing_sources = [
                citation.source_title
                for citation in built.citations
                if citation.source_title and pdf_glyph_text(citation.source_title) not in pdf_text
            ]
            missing_text = [
                item["text"]
                for slide in print_result["slides"]
                for item in slide["typography"]
                if len(item["text"]) >= 12 and pdf_glyph_text(item["text"]) not in pdf_text
            ]
            pdf_ok = (
                len(pdf_reader.pages) >= len(sections)
                and all(bool(page.extract_text().strip()) for page in pdf_reader.pages)
                and not missing_sources
                and not missing_text
            )
            snapshot = {
                **result,
                "use": use,
                "id": snapshot_id,
                "status": "captured",
                "contentHash": hashlib.sha256(built.content.encode()).hexdigest(),
                "rendererHash": manifest["rendererHash"],
                "assetHashes": asset_hashes,
                "designHash": digest([section.splitlines()[0] for section in sections]),
                "missingAssets": missing,
                "contact": "contact.png",
                "pdf": "deck.pdf",
                "pdfCheck": {
                    "status": "checked" if pdf_ok else "incomplete",
                    "pages": len(pdf_reader.pages),
                    "slides": print_result["slides"],
                    "method": "print-dom-and-exported-pdf-text",
                    "missingSources": missing_sources,
                    "missingText": missing_text,
                    "glyphEquivalence": "Unicode Equivalent_Unified_Ideograph 17.0.0",
                    "extractionDifferences": [
                        item["text"]
                        for slide in print_result["slides"]
                        for item in slide["typography"]
                        if len(item["text"]) >= 12 and flatten(item["text"]) not in strict_pdf_text
                    ],
                    "extractedTextHash": hashlib.sha256(raw_pdf_text.encode()).hexdigest(),
                },
                "browser": browser_version,
                "distribution": distribution,
                "elapsedSeconds": round(time.monotonic() - started, 3),
            }
            snapshot["files"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in output.iterdir()
                if path.is_file()
            }
            (output / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False))
            os.replace(output, self.cache / snapshot_id)
            return snapshot

    def file(self, snapshot_id: str, name: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", snapshot_id) or not re.fullmatch(
            r"(?:slide-\d{2}\.png|contact\.png|deck\.pdf)", name
        ):
            raise LookupError("Slide render not found.")
        folder = self.cache / snapshot_id
        try:
            snapshot = json.loads((folder / "snapshot.json").read_text())
            path = folder / name
            if hashlib.sha256(path.read_bytes()).hexdigest() != snapshot["files"][name]:
                raise LookupError("The slide render failed its integrity check.")
            return path
        except (OSError, KeyError, ValueError) as error:
            raise LookupError(
                "The slide render is unavailable. Rebuild to capture it again."
            ) from error
