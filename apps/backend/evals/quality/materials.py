"""The gold material: load ``materials.toml`` and seed one library per set.

Real pages are read through the app's own web capture (``POST /api/captures/web``), so
extraction is tested as the product does it. When a page cannot be captured, or is a PDF,
it is fetched here, turned into text and filed as a note, and the set's info says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import evals.ask.run as ask_run
from evals.ask.run import ExamError, make_app, wait_for_jobs
from gunther.llm import ClientFactory

BACKEND = Path(__file__).resolve().parents[2]
MATERIALS = Path(__file__).with_name("materials.toml")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Gunther-quality-eval"
# Real pages are long; reading and indexing them takes longer than the demo notes.
SEEDING_TIMEOUT_SECONDS = 3600
AUDIENCES = {"student": "student", "researcher": "scientist", "collaborator": "collaborator"}


@dataclass(frozen=True)
class Brief:
    kind: str  # "slides" (a talk deck) or "report"
    audience: str  # as the app names it
    brief: str
    use: str = ""  # decks: "talk"
    duration: str = ""

    @property
    def label(self) -> str:
        return "deck" if self.kind == "slides" else "report"

    def text(self) -> str:
        """The brief as a person would type it: the duration leads a talk's brief."""
        return self.brief


@dataclass(frozen=True)
class MaterialSet:
    id: str
    title: str
    question: str
    language: str
    sources: tuple[dict[str, Any], ...]
    deck: Brief
    report: Brief
    note: str = ""

    def brief(self, label: str) -> Brief:
        return self.deck if label == "deck" else self.report

    @property
    def hash(self) -> str:
        """Names the definition: any change to sources, question or briefs seeds again."""

        text = json.dumps(self.__dict__, default=lambda item: item.__dict__, sort_keys=True)
        digest = hashlib.sha256(text.encode())
        for source in self.sources:
            if source["kind"] == "notes":
                for path in sorted((BACKEND / source["path"]).glob("*.md")):
                    digest.update(path.name.encode())
                    digest.update(path.read_bytes())
        return digest.hexdigest()[:12]


def _brief(kind: str, raw: dict[str, Any]) -> Brief:
    audience = raw["audience"]
    if audience not in AUDIENCES:
        raise ValueError(f"Unknown audience {audience!r}; use student, researcher or collaborator")
    return Brief(
        kind=kind,
        audience=AUDIENCES[audience],
        brief=raw["brief"],
        use=raw.get("use", ""),
        duration=raw.get("duration", ""),
    )


def load_sets(path: Path = MATERIALS) -> list[MaterialSet]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    sets = [
        MaterialSet(
            id=raw["id"],
            title=raw["title"],
            question=raw["question"],
            language=raw.get("language", "en"),
            sources=tuple(raw["sources"]),
            deck=_brief("slides", raw["deck"]),
            report=_brief("report", raw["report"]),
            note=raw.get("note", ""),
        )
        for raw in data["set"]
    ]
    ids = [item.id for item in sets]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate set ids in materials.toml")
    return sets


# Fetching a page ourselves (the fallback) ------------------------------------------------


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "section", "article"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self.depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3"} and not self.depth:
            self.parts.append("\n## ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.depth:
            self.parts.append(data)


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read()


def html_to_text(raw: bytes) -> str:
    parser = _Text()
    parser.feed(raw.decode("utf-8", errors="replace"))
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def pdf_to_text(raw: bytes) -> str:
    with tempfile.TemporaryDirectory(prefix="gunther-quality-pdf-") as scratch:
        pdf = Path(scratch) / "in.pdf"
        pdf.write_bytes(raw)
        done = subprocess.run(  # noqa: S603
            ["pdftotext", "-layout", str(pdf), "-"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    if done.returncode != 0:
        raise ExamError(f"pdftotext failed: {done.stderr[:200]}")
    return re.sub(r"\n{3,}", "\n\n", done.stdout).strip()


# Seeding -----------------------------------------------------------------------------------


def _add_note(client: TestClient, headers: dict, base_id: str, title: str, text: str) -> str:
    created = client.post(
        "/api/sources",
        headers=headers,
        json={"title": title[:160], "kind": "note", "knowledgeBaseId": base_id,
              "content": text[:1_000_000]},
    )  # fmt: skip
    if created.status_code >= 300:
        raise ExamError(f"A note could not be added ({title}): {created.text[:200]}")
    return created.json()["source"]["id"]


def _seed_source(
    client: TestClient, headers: dict, base_id: str, source: dict[str, Any], say: Callable
) -> tuple[str | None, dict[str, Any]]:
    """Add one source; returns its id (or None) and what was actually done."""

    url, title = source["url"], source.get("title", source["url"])
    info: dict[str, Any] = {"url": url, "title": title, "kind": source["kind"]}
    if source["kind"] == "web":
        response = client.post(
            "/api/captures/web",
            headers=headers,
            json={"originalUrl": url, "knowledgeBaseId": base_id, "title": title[:160]},
        )
        if response.status_code < 300:
            result = response.json()["importResult"]["source"]
            info["how"] = "web capture"
            return result["id"], info
        info["captureError"] = f"HTTP {response.status_code}: {response.text[:200]}"
        say(f"  {title}: web capture failed ({info['captureError']}); fetching it directly")
        try:
            text = html_to_text(fetch_bytes(url))
        except Exception as error:  # noqa: BLE001
            info["how"] = "failed"
            info["error"] = f"{type(error).__name__}: {error}"
            return None, info
        info["how"] = "fallback: HTML fetched by the eval and filed as a note"
        return _add_note(client, headers, base_id, title, f"Source: {url}\n\n{text}"), info
    if source["kind"] == "pdf-note":
        try:
            text = pdf_to_text(fetch_bytes(url))
        except Exception as error:  # noqa: BLE001
            info["how"] = "failed"
            info["error"] = f"{type(error).__name__}: {error}"
            return None, info
        info["how"] = (
            "fallback: the web capture reads only HTML, so the PDF was turned into text with pdftotext and filed as a note"  # noqa: E501
        )
        info["chars"] = len(text)
        return _add_note(client, headers, base_id, title, f"Source: {url}\n\n{text}"), info
    raise ExamError(f"Unknown source kind {source['kind']!r}")


def seed_set(
    target: Path,
    material: MaterialSet,
    settings_file: Path,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable,
) -> dict[str, Any]:
    """Create the set's library in ``target`` with the real worker; returns its info."""

    token = secrets.token_hex(32)
    headers = {"X-Gunther-Token": token}
    ask_run.SETUP_TIMEOUT_SECONDS = max(ask_run.SETUP_TIMEOUT_SECONDS, SEEDING_TIMEOUT_SECONDS)
    say(
        f"Seeding set {material.id} ({len(material.sources)} sources); this reads and indexes them."
    )
    with TestClient(make_app(target, settings_file, token, factory, overrides)) as client:
        if not client.app.state.models.models:
            raise ExamError(ask_run.NO_MODEL)
        base_id = client.post(
            "/api/knowledge-bases",
            headers=headers,
            json={"title": material.title[:160], "question": material.question,
                  "description": f"Quality evaluation set {material.id}."},
        ).json()["id"]  # fmt: skip
        ids: set[str] = set()
        used: list[dict[str, Any]] = []
        for source in material.sources:
            if source["kind"] == "notes":
                for path in sorted((BACKEND / source["path"]).glob("*.md")):
                    text = path.read_text(encoding="utf-8")
                    title = text.splitlines()[0].removeprefix("# ").strip()
                    ids.add(_add_note(client, headers, base_id, title, text))
                    used.append({"title": title, "kind": "note", "how": "demo note",
                                 "url": str(path.relative_to(BACKEND))})  # fmt: skip
                continue
            source_id, info = _seed_source(client, headers, base_id, source, say)
            used.append(info)
            if source_id:
                ids.add(source_id)
            say(f"  {info['title'][:70]}: {info['how']}")
        if not ids:
            raise ExamError(f"Set {material.id}: no source could be added: {used}")
        failed = wait_for_jobs(client, headers, ids, say)
        broken = [job for job in failed if job["kind"] not in ask_run.TOLERATED_JOBS]
        if broken:
            listing = "; ".join(
                f"{job['kind']} of {job['sourceTitle']}: {job['error']}" for job in broken
            )
            raise ExamError(f"Reading set {material.id} failed: {listing}")
        listed = client.get(f"/api/knowledge-bases/{base_id}/sources", headers=headers).json()
        for item in used:
            match = next((s for s in listed if s["title"] == item["title"][:160]), None)
            if match:
                item["assertions"] = match.get("assertionCount")
        return {
            "knowledgeBaseId": base_id,
            "setHash": material.hash,
            "sources": used,
            "failedJobs": [f"{job['kind']}: {job['sourceTitle']}" for job in failed],
            "missing": [item for item in used if item["how"] == "failed"],
        }


def ensure_set(
    root: Path,
    material: MaterialSet,
    settings_file: Path,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable,
    reseed: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """The seeded library of a set, built once and reused while its definition is unchanged."""

    template = root / f"quality-template-{material.id}-{material.hash}"
    marker = template / "template.json"
    if reseed and template.exists():
        shutil.rmtree(template)
    if marker.exists():
        say(f"Reusing the seeded library {template.name}.")
        return template, json.loads(marker.read_text(encoding="utf-8"))
    partial = root / f"{template.name}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        info = seed_set(partial, material, settings_file, factory, overrides, say)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    (partial / "template.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    partial.rename(template)
    return template, info
