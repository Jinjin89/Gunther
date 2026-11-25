"""Turn an answer written for the eye into a script written for the ear.

Plain paragraphs, lists and headings are cleaned by rule: the marks that mean
nothing aloud go, and lines get a full stop so the voice pauses. Parts that
cannot be read as written (a table, a picture, code, a formula) are described
by a model in the answer's own language instead: reading cells one by one, or a
file path, would only be noise. If such a part cannot be described, that is an
error to show, never a made-up stand-in.
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from gunther import usage
from gunther.citations import strip_citations
from gunther.llm import Image, ModelError, ModelGateway, ModelInfo
from gunther.markup import parse_markup
from gunther.tts_providers import TtsError

# Bump when the rules below change what a message is read as, so old clips are remade.
NARRATION_VERSION = 3
MAX_DESCRIBED_PARTS = 12

PartKind = Literal["table", "image", "code", "math", "diagram"]


@dataclass(frozen=True)
class Part:
    kind: PartKind
    text: str
    # A picture that came inside the answer itself; other addresses are never fetched.
    image: Image | None = None


@dataclass(frozen=True)
class Script:
    text: str
    # A model described some part of the answer.
    described: bool
    model: str | None = None


_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")

_INSTRUCTIONS = {
    "table": (
        "Explain this table as it would be said to a listener who cannot see it. Say what it "
        "compares and what stands out: the highest, the lowest, trends, differences. Do not "
        "read it cell by cell or row by row unless it has only a few cells."
    ),
    "image": (
        "Describe this picture for a listener who cannot see it: what it shows and what it "
        "means for the answer. If you cannot see the picture, say only what its caption tells."
    ),
    "code": (
        "Explain what this code does in plain words for a listener who cannot see it. Do not "
        "read symbols, brackets or punctuation aloud; name key functions and steps."
    ),
    "math": (
        "Say this formula in words, the way a teacher would read it aloud, then say in a short "
        "phrase what it means."
    ),
    "diagram": (
        "Say in one or two plain sentences what this diagram shows, for a listener who cannot "
        "see it. Do not read the code."
    ),
}
_SYSTEM = (
    "You write a passage that will be read aloud by a voice. {task} Reply with the spoken "
    "passage only: short and to the point, two to four plain sentences, no markdown, no lists, no "
    "brackets, and no mention of 'the table above' or of being an AI. Use the language the "
    "text around it is written in."
)


# Splitting an answer into plain text and parts to describe -----------------------


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and "|" in lines[index]
        and bool(_TABLE_SEPARATOR.match(lines[index + 1]))
    )


def _image_of(source: str, alt: str) -> Part:
    image = None
    match = re.fullmatch(r"data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=]+)", source)
    if match:
        try:
            image = Image(match.group(1), base64.b64decode(match.group(2)))
        except ValueError:
            image = None
    return Part("image", alt.strip() or "a picture", image)


def split_answer(markdown: str) -> list[str | Part]:
    """The answer as running text and the parts that need describing, in order."""

    out: list[str | Part] = []
    start = 0
    for block in parse_markup(markdown).blocks:
        out.extend(_split_prose(markdown[start:block.start]))
        lang = block.language.lower()
        kind: PartKind = "code"
        if block.kind == "math_block" or lang == "math":
            kind = "math"
        elif block.kind == "svg" or lang in {"svg", "mermaid"}:
            kind = "diagram"
        body = block.body.strip() if kind == "math" else block.body.removesuffix("\n")
        out.append(Part(kind, body))
        start = block.end
    out.extend(_split_prose(markdown[start:]))
    return out


def _split_prose(markdown: str) -> list[str | Part]:
    if not markdown:
        return []
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str | Part] = []
    text: list[str] = []

    def flush() -> None:
        if text:
            out.append("\n".join(text))
            text.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_table_start(lines, index):
            end = index
            while end < len(lines) and "|" in lines[end]:
                end += 1
            flush()
            out.append(Part("table", "\n".join(lines[index:end])))
            index = end
        else:
            found = _IMAGE.search(line)
            if found:
                flush()
                before, after = line[: found.start()], line[found.end() :]
                if before.strip():
                    out.append(before)
                out.append(_image_of(found.group(2), found.group(1)))
                if after.strip():
                    text.append(after)
            else:
                text.append(line)
            index += 1
    flush()
    return out


# Cleaning running text --------------------------------------------------------------


def clean_text(markdown: str) -> str:
    """Markdown without its marks; every line ends in a stop so the voice pauses."""

    markdown = strip_citations(markdown, trim_space=True)
    for start, end in reversed(parse_markup(markdown).inline):
        if markdown.startswith("$$", start):
            markdown = markdown[:start] + markdown[start + 2:end - 2] + markdown[end:]
    lines = []
    for line in markdown.split("\n"):
        line = re.sub(r"<[^>\n]+>", "", line)
        line = re.sub(r"^\s*(#{1,6})\s+", "", line)
        line = re.sub(r"^\s*>+\s?", "", line)
        line = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", line)
        if re.fullmatch(r"\s*([-*_]\s*){3,}", line):
            continue
        line = re.sub(r"\[([^\]]+)\]\((?:[^)]*)\)", r"\1", line)
        line = re.sub(r"https?://\S+", "", line)
        line = re.sub(r"(\*\*|__|\*|~~|`)", "", line)
        line = re.sub(r"(?<![\w$])_([^_]+)_(?!\w)", r"\1", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            lines.append("")
            continue
        lines.append(line if re.search(r"[.!?。！？:：;；,，、…)）”\"']$", line) else f"{line}.")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# Describing parts ---------------------------------------------------------------------


def _pick_model(gateway: ModelGateway | None, needs_vision: bool) -> ModelInfo | None:
    if gateway is None:
        return None
    if needs_vision:
        for role in ("photos", "ask", "analysis"):
            chosen = gateway.for_role(role)
            if chosen and chosen[0].vision:
                return chosen[0]
    for role in ("analysis", "ask"):
        chosen = gateway.for_role(role)
        if chosen:
            return chosen[0]
    return None


@usage.job("analysis", "narration")
def _describe(gateway: ModelGateway, model: ModelInfo, part: Part, context: str) -> str:
    images = (part.image,) if part.image else ()
    prompt = f"Text around it (for language and topic):\n{context[:1500]}\n\n"
    if part.kind == "image":
        prompt += f"Picture caption: {part.text}"
    else:
        prompt += f"The {part.kind}:\n{part.text[:6000]}"
    try:
        completion = gateway.complete(
            model,
            system=_SYSTEM.format(task=_INSTRUCTIONS[part.kind]),
            messages=prompt,
            images=images,
            # The description is short, but a thinking model spends tokens before writing it.
            effort="low",
            max_tokens=4000,
        )
    except ModelError as error:
        raise TtsError(f"Describing a {part.kind} for reading aloud failed: {error}") from error
    return clean_text(completion.text)


async def narrate(
    markdown: str,
    gateway: ModelGateway | None,
    *,
    understands_structure: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> Script:
    """The script to read for an answer.

    ``understands_structure``: the supplier's voice model reads tables and pictures
    itself, so nothing needs describing.
    """

    pieces = split_answer(markdown)
    if understands_structure:
        pieces = [piece.text if isinstance(piece, Part) and piece.kind != "diagram" else piece
                  for piece in pieces]
    parts = [piece for piece in pieces if isinstance(piece, Part)]
    context = clean_text("\n".join(piece for piece in pieces if isinstance(piece, str)))
    described: dict[int, str] = {}
    model = None
    if parts:
        model = _pick_model(gateway, needs_vision=any(part.image for part in parts))
        if model is None or gateway is None:
            kinds = ", ".join(sorted({part.kind for part in parts}))
            raise TtsError(
                f"This answer has a {kinds} that needs a model to describe it aloud. "
                "Set up a language model in Settings, then try again."
            )
        wanted = [
            (position, piece)
            for position, piece in enumerate(pieces)
            if isinstance(piece, Part)
        ][:MAX_DESCRIBED_PARTS]
        results = await asyncio.gather(
            *(asyncio.to_thread(_describe, gateway, model, part, context) for _, part in wanted)
        )
        described = {position: text for (position, _), text in zip(wanted, results, strict=True)}
    spoken: list[str] = []
    for position, piece in enumerate(pieces):
        if isinstance(piece, Part):
            spoken.append(
                described.get(position)
                or "Another part of this answer is best seen on screen."
            )
        else:
            cleaned = clean_text(piece)
            if cleaned:
                spoken.append(cleaned)
    script = "\n\n".join(spoken).strip()
    if not script:
        raise TtsError("There is nothing in this answer to read aloud.")
    used = model.method if model and described else None
    return Script(script, described=bool(described), model=used)
