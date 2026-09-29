"""Summaries of captured sources, written after they are read.

Every capture gets a short summary that suits what it is: study notes for a
lecture, decisions and actions for a meeting, what a photo shows, what a
table records, the contributions of a paper. Each key point cites the
numbered passages it came from, so it can be checked against the original.

The original is never changed. A summary is derived from the source's current
revision, labelled with the model that wrote it, and can be written again.
OpenAI or DeepSeek writes it (a photo is shown to OpenAI's model as an image).
There is no model-free stand-in: without a key there are no summaries, and a
model that fails leaves a visible error to retry rather than a worse summary.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther.models import (
    Asset,
    ContentBlock,
    RecordingSession,
    Source,
    SourceDigest,
    utc_now,
)

Profile = Literal[
    "lecture", "meeting", "memo", "image", "table", "paper", "document", "web", "note"
]

# What each kind of capture is summarized as; the model is told this.
GUIDES: dict[str, str] = {
    "lecture": (
        "a recorded lecture or course. Write study notes: the overview says what the "
        "session taught; key points are the ideas and explanations in the order taught; "
        "actions are homework, reading or practice that was mentioned; questions are what "
        "was left open or asked; terms are the technical terms introduced."
    ),
    "meeting": (
        "a recorded meeting. The overview says what the meeting was about and what it "
        "settled; key points are decisions and important information; actions are the "
        "agreed next steps, with the person and date when they were said; questions are "
        "what was left unresolved."
    ),
    "memo": (
        "a voice memo, a thought spoken aloud. The overview restates the thought clearly "
        "and briefly; key points are its separate ideas; actions are any to-dos."
    ),
    "image": (
        "a photo or scan (a slide, whiteboard, page, diagram, chart or screenshot). The "
        "overview says what it shows; key points carry its content, taken from the "
        "recognized text; terms are its technical terms."
    ),
    "table": (
        "a table of data. The overview says what the table records (its rows and "
        "columns); key points say what stands out, using the column statistics given; "
        "questions are what the data could answer."
    ),
    "paper": (
        "a research paper. The overview gives its problem and approach in two or three "
        "sentences; key points are its contributions, methods, main findings and stated "
        "limitations; questions are what it leaves open; terms are its key concepts."
    ),
    "document": (
        "a document. The overview says what it is and what it is for; key points are its "
        "main content; actions are anything it asks the reader to do."
    ),
    "web": (
        "a saved web page. The overview says what the page is and its main message; key "
        "points are its main content. The person's note on why they saved it, when "
        "given, says what to pay attention to."
    ),
    "note": (
        "a personal note. The overview restates it briefly; key points are its separate "
        "ideas; actions are its to-dos."
    ),
}

# Shorter captures are their own summary.
MIN_TEXT_CHARS = 280
# How much of a source a model reads: the opening, then evenly spaced passages.
MAX_PROMPT_CHARS = 24_000
MAX_PASSAGE_CHARS = 1_600
# Photos shown to a vision model: formats it reads, and at most this size.
VISION_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
MAX_VISION_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class Passage:
    number: int
    block_id: str
    text: str
    locator: str


@dataclass(slots=True)
class DigestRequest:
    source_id: str
    revision_id: str
    title: str
    profile: Profile
    passages: list[Passage]
    note: str = ""
    image_path: Path | None = None
    image_type: str | None = None
    table: str | None = None
    file_name: str | None = None

    @property
    def text_chars(self) -> int:
        return sum(len(passage.text) for passage in self.passages)


@dataclass(slots=True)
class Point:
    text: str
    passages: list[int] = field(default_factory=list)


@dataclass(slots=True)
class DigestResult:
    method: str
    overview: str
    title: str = ""
    key_points: list[Point] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)


class DigestWriter(Protocol):
    method: str
    vision: bool

    def write(self, request: DigestRequest) -> DigestResult | None: ...


# Reading a source ----------------------------------------------------------------


def profile_for(source: Source, asset: Asset | None, recording_context: str | None) -> Profile:
    if source.kind in {"recording", "course"} or recording_context:
        return recording_context if recording_context in {"meeting", "memo"} else "lecture"
    media = asset.media_type if asset else ""
    if source.kind == "image" or media.startswith("image/"):
        return "image"
    if source.kind == "table":
        return "table"
    if source.kind == "paper":
        return "paper"
    if source.kind == "link":
        return "web"
    if source.kind == "note" and not asset:
        return "note"
    return "document"


def worth_digesting(source: Source, text_chars: int, vision: bool) -> bool:
    """Short notes are their own summary; a photo without text needs a model that sees."""

    return text_chars >= MIN_TEXT_CHARS or (source.kind == "image" and vision)


def _spread(passages: list[Passage], budget: int) -> list[Passage]:
    """The opening of a long source, then passages spread evenly through the rest."""

    if sum(len(p.text) for p in passages) <= budget:
        return passages
    chosen: list[Passage] = []
    used = 0
    opening = budget * 2 // 5
    rest = iter(passages)
    for passage in rest:
        if used + len(passage.text) > opening:
            remaining = [passage, *rest]
            break
        chosen.append(passage)
        used += len(passage.text)
    else:
        return chosen
    average = max(1, sum(len(p.text) for p in remaining) // len(remaining))
    slots = max(1, (budget - used) // average)
    step = max(1, len(remaining) // slots)
    for passage in remaining[::step]:
        if used + len(passage.text) > budget:
            break
        chosen.append(passage)
        used += len(passage.text)
    return chosen


def _user_note(source: Source) -> str:
    """What the person wrote when they saved a file or page (before its extracted text)."""

    match = re.search(r"(?:^|\n)## Your context\n\n(.+?)(?=\n## |\Z)", source.content, re.S)
    return match[1].strip()[:600] if match else ""


def prepare(
    session: Session,
    source: Source,
    revision_id: str,
    *,
    asset_path: Path | None = None,
) -> DigestRequest:
    blocks = session.scalars(
        select(ContentBlock)
        .where(ContentBlock.revision_id == revision_id, ContentBlock.kind != "heading")
        .order_by(ContentBlock.ordinal)
    ).all()
    passages: list[Passage] = []
    for block in blocks:
        payload = json.loads(block.payload_json or "{}")
        text = block.content.strip()
        if not text or payload.get("reference"):
            continue
        headings = json.loads(block.heading_path_json or "[]")
        passages.append(
            Passage(
                number=len(passages) + 1,
                block_id=block.id,
                text=text[:MAX_PASSAGE_CHARS],
                locator=block.locator or " / ".join(headings),
            )
        )
    asset = session.get(Asset, source.asset_id) if source.asset_id else None
    recording_context = None
    reference = re.search(r"Local recording:\s*(rec_[a-f0-9]{24})", source.content)
    if reference:
        recording = session.get(RecordingSession, reference[1])
        recording_context = recording.recording_context if recording else "lecture"
    profile = profile_for(source, asset, recording_context)
    request = DigestRequest(
        source_id=source.id,
        revision_id=revision_id,
        title=source.title,
        profile=profile,
        passages=passages,
        note=_user_note(source) if asset or source.kind == "link" else "",
        file_name=asset.original_name if asset else None,
    )
    if profile == "table":
        request.table = source.content
    if (
        profile == "image"
        and asset is not None
        and asset_path is not None
        and asset.media_type in VISION_TYPES
        and asset.size_bytes <= MAX_VISION_BYTES
    ):
        request.image_path, request.image_type = asset_path, asset.media_type
    return request


# Tables ------------------------------------------------------------------------


def _rows(content: str) -> list[list[str]]:
    lines = [line for line in content.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    delimiter = "\t" if "\t" in lines[0] else ","
    return [row for row in csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)]


def _number(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").rstrip("%")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _format(value: float) -> str:
    return f"{value:,.0f}" if value == int(value) else f"{value:,.3g}"


def table_facts(content: str) -> tuple[str, list[str]]:
    """A table's shape and, for each numeric column, its range and mean."""

    rows = _rows(content)
    if not rows:
        return "", []
    header, body = rows[0], rows[1:]
    shape = f"{len(body):,} rows × {len(header)} columns: {', '.join(h for h in header[:12])}"
    facts = []
    for index, name in enumerate(header[:24]):
        values = [_number(row[index]) for row in body if index < len(row)]
        numbers = [value for value in values if value is not None]
        if len(numbers) >= max(2, len(body) // 2):
            facts.append(
                f"{name}: {_format(min(numbers))} to {_format(max(numbers))}, "
                f"mean {_format(fmean(numbers))}"
            )
        else:
            distinct = Counter(row[index].strip() for row in body if index < len(row))
            if distinct and len(distinct) <= max(12, len(body) // 3):
                common = ", ".join(f"{k} ({n})" for k, n in distinct.most_common(4) if k)
                if common:
                    facts.append(f"{name}: {len(distinct)} values, most often {common}")
    return shape, facts


# Writing with a model ----------------------------------------------------------


class _Point(BaseModel):
    text: str
    passages: list[int] = Field(default_factory=list)


class _Digest(BaseModel):
    title: str = Field(description="A short, specific title, in the source's language")
    overview: str
    key_points: list[_Point]
    action_items: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)


INSTRUCTIONS = (
    "You summarize one thing a person saved into their knowledge library. It is {guide}\n\n"
    "Use only what the source says. Do not add outside facts or opinions. Keep uncertainty "
    "and disagreement visible. Write in the language of the source.\n"
    "- title: a short, specific title for it (at most 12 words).\n"
    "- overview: 1 to 3 sentences.\n"
    "- key_points: 3 to 8 points, each one sentence. For each, list the numbers of the "
    "passages it comes from in `passages` (the numbers in [brackets]); list none only for "
    "what is seen in the image rather than read in a passage.\n"
    "- action_items, open_questions, terms: only what the source contains; leave empty "
    "otherwise."
)


def _prompt(request: DigestRequest) -> str:
    parts = [f"Title: {request.title}"]
    if request.note:
        parts.append(f"Why it was saved: {request.note}")
    if request.profile == "table":
        shape, facts = table_facts(request.table or "")
        if shape:
            parts.append(f"Table: {shape}")
        if facts:
            parts.append("Column statistics:\n" + "\n".join(f"- {fact}" for fact in facts))
    passages = _spread(request.passages, MAX_PROMPT_CHARS)
    if passages:
        parts.append(
            "Passages:\n\n"
            + "\n\n".join(
                f"[{p.number}]{f' ({p.locator})' if p.locator else ''} {p.text}" for p in passages
            )
        )
    elif request.image_path:
        parts.append("No text was recognized in the image.")
    return "\n\n".join(parts)


class DigestError(RuntimeError):
    """The model did not write a usable summary."""


class ModelDigestWriter:
    def __init__(
        self,
        api_key: str,
        model: str,
        provider: Literal["openai", "deepseek"],
        base_url: str | None = None,
        *,
        vision: bool = False,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.method = f"{provider}:{model}"
        # Only OpenAI's models are shown photos; DeepSeek reads their recognized text.
        self.vision = vision and provider == "openai"

    def _input(self, request: DigestRequest) -> str | list[dict[str, object]]:
        prompt = _prompt(request)
        if not (self.vision and request.image_path and request.image_type):
            return prompt
        encoded = base64.b64encode(request.image_path.read_bytes()).decode()
        return [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{request.image_type};base64,{encoded}",
                    },
                ],
            }
        ]

    def write(self, request: DigestRequest) -> DigestResult | None:
        """The model's summary; ``None`` only when there is nothing to summarize.

        A failed call raises, so the job retries and then shows as failed.
        """

        if not request.passages and not (self.vision and request.image_path):
            return None
        response = self.client.responses.parse(
            model=self.model,
            store=False,
            instructions=INSTRUCTIONS.format(guide=GUIDES[request.profile]),
            input=self._input(request),
            text_format=_Digest,
        )
        parsed = response.output_parsed
        if parsed is None or not parsed.overview.strip():
            raise DigestError("The model returned no summary")
        known = {passage.number for passage in request.passages}
        return DigestResult(
            method=self.method,
            title=parsed.title.strip()[:160],
            overview=parsed.overview.strip(),
            # A citation to a passage that was not given is dropped, never trusted.
            key_points=[
                Point(p.text.strip(), sorted({n for n in p.passages if n in known})[:4])
                for p in parsed.key_points
                if p.text.strip()
            ][:8],
            action_items=[item.strip() for item in parsed.action_items if item.strip()][:8],
            open_questions=[item.strip() for item in parsed.open_questions if item.strip()][:6],
            terms=[term.strip() for term in parsed.terms if term.strip()][:12],
        )


def create_digest_writer(
    mode: Literal["auto", "off"],
    *,
    openai_api_key: str | None,
    openai_model: str,
    deepseek_api_key: str | None,
    deepseek_model: str,
    deepseek_base_url: str,
    images: bool = True,
) -> DigestWriter | None:
    """A model that writes summaries, or ``None``: turned off, or no key to use."""

    if mode == "off":
        return None
    if openai_api_key:
        return ModelDigestWriter(openai_api_key, openai_model, "openai", vision=images)
    if deepseek_api_key:
        return ModelDigestWriter(deepseek_api_key, deepseek_model, "deepseek", deepseek_base_url)
    return None


# Keeping it --------------------------------------------------------------------


def _engine(method: str) -> str:
    provider, _, model = method.partition(":")
    return f"{'OpenAI' if provider == 'openai' else 'DeepSeek'} {model}"


def markdown(title: str, result: DigestResult, passages: dict[int, Passage]) -> str:
    lines = [f"# {title}", "", result.overview, ""]

    def cite(numbers: list[int]) -> str:
        return "".join(f" [{n}]" for n in numbers)

    if result.key_points:
        lines += ["## Key points", ""]
        lines += [f"- {point.text}{cite(point.passages)}" for point in result.key_points]
        lines.append("")
    for heading, items in (
        ("Actions", [f"- [ ] {item}" for item in result.action_items]),
        ("Open questions", [f"- {item}" for item in result.open_questions]),
    ):
        if items:
            lines += [f"## {heading}", "", *items, ""]
    if result.terms:
        lines += [f"Terms: {' · '.join(result.terms)}", ""]
    cited = sorted({n for point in result.key_points for n in point.passages if n in passages})
    if cited:
        lines += ["## Passages cited", ""]
        for number in cited:
            passage = passages[number]
            quote = re.sub(r"\s+", " ", passage.text)[:280]
            where = f" ({passage.locator})" if passage.locator else ""
            lines.append(f"[{number}]{where} “{quote}{'…' if len(passage.text) > 280 else ''}”")
        lines.append("")
    lines += ["---", "", f"Summary written by {_engine(result.method)} from the saved original."]
    return "\n".join(lines) + "\n"


# Names a capture gets before anyone names it: Capture's defaults and file names.
PLACEHOLDER_TITLE = re.compile(
    r"^(?:untitled(?: source| note| capture)?|lecture recording|"
    r"(?:lecture|meeting|voice memo) · [\d/.\-\s]+|"
    r"(?:img|dsc|pxl|screenshot|screen shot|scan|photo|image)[\s_\-]?\d[\w\s.\-:]*)$",
    re.IGNORECASE,
)


def placeholder_title(title: str, file_name: str | None) -> bool:
    title = title.strip()
    if file_name and title in {file_name, Path(file_name).stem}:
        return True
    return not title or bool(PLACEHOLDER_TITLE.match(title))


def publish(
    session: Session, source: Source, request: DigestRequest, result: DigestResult | None
) -> SourceDigest | None:
    """Keep the summary; a source that yields none keeps any earlier one.

    A capture still called by a placeholder ("Untitled", "Lecture · 29/9/2026",
    IMG_2041.jpg) takes the title the model wrote; a name someone chose stays.
    """

    if result is None:
        return None
    if result.title and placeholder_title(source.title, request.file_name):
        source.title = result.title[:160]
    passages = {passage.number: passage for passage in request.passages}
    row = session.get(SourceDigest, source.id)
    if row is None:
        row = SourceDigest(source_id=source.id, revision_id=request.revision_id)
        session.add(row)
    row.revision_id = request.revision_id
    row.profile = request.profile
    row.method = result.method
    row.suggested_title = result.title
    row.overview = result.overview
    row.payload_json = json.dumps(
        {
            "keyPoints": [
                {
                    "text": point.text,
                    "citations": [
                        {
                            "number": n,
                            "blockId": passages[n].block_id,
                            "quote": passages[n].text[:600],
                            "locator": passages[n].locator,
                        }
                        for n in point.passages
                        if n in passages
                    ],
                }
                for point in result.key_points
            ],
            "actionItems": result.action_items,
            "openQuestions": result.open_questions,
            "terms": result.terms,
        },
        ensure_ascii=False,
    )
    row.markdown = markdown(source.title, result, passages)
    row.updated_at = utc_now()
    session.flush()
    return row


def digest_out(row: SourceDigest) -> dict[str, object]:
    payload = json.loads(row.payload_json or "{}")
    return {
        "sourceId": row.source_id,
        "revisionId": row.revision_id,
        "profile": row.profile,
        "method": row.method,
        "engine": row.method.partition(":")[0],
        "model": row.method.partition(":")[2] or None,
        "suggestedTitle": row.suggested_title or None,
        "overview": row.overview,
        "keyPoints": payload.get("keyPoints", []),
        "actionItems": payload.get("actionItems", []),
        "openQuestions": payload.get("openQuestions", []),
        "terms": payload.get("terms", []),
        "markdown": row.markdown,
        "updatedAt": row.updated_at.isoformat(),
    }
