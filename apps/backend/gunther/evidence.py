"""Evidence, tool bindings and read and search results for Ask and Outputs."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


EVIDENCE_CHARS = 900
# What the writer and the checker read of a source that was read further.
CONTEXT_CHARS = 6000


@dataclass(frozen=True)
class Evidence:
    """One numbered thing the answer may lean on: a passage or a web page."""

    kind: str
    title: str
    text: str
    locator: str = ""
    status: str = ""
    confidence: float = 0.0
    url: str | None = None
    # Its number in the conversation's pool, once it has one. Never reused or changed.
    ref: int | None = None
    # What the caller needs to turn this into a citation; opaque here.
    payload: Any = None
    # More of the source than the card shows, for the model to read; the card keeps `text`.
    context: str = ""
    extraction_warnings: tuple[dict[str, Any], ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        citation = (
            self.payload[0] if isinstance(self.payload, tuple) and self.payload else self.payload
        )
        source = getattr(citation, "source_id", "")
        revision = getattr(citation, "source_revision_id", "")
        block = getattr(citation, "block_id", "")
        if source and revision and block:
            return (self.kind, source, f"{revision}:{block}")
        return (
            self.kind,
            self.url or source or self.title,
            hashlib.sha256(self.text.encode()).hexdigest(),
        )

    @property
    def source_key(self) -> str:
        citation = (
            self.payload[0] if isinstance(self.payload, tuple) and self.payload else self.payload
        )
        return str(
            getattr(citation, "work_identifier", "")
            or getattr(citation, "source_id", "")
            or self.url
            or f"unidentified:{self.ref or self.key[2]}"
        )


@dataclass(frozen=True)
class KeyResultMatch:
    id: str
    sentence: str
    groups: tuple[tuple[Evidence, ...], ...]


def key_result_lines(
    matches: Iterable[KeyResultMatch], pool: Sequence[Evidence],
) -> tuple[str, ...]:
    """Point to complete evidence groups that made it into the numbered pool."""
    numbered = {item.key: item.ref for item in pool if item.ref is not None}
    lines = []
    for match in matches:
        refs = dict.fromkeys(
            numbered[item.key]
            for group in match.groups if all(item.key in numbered for item in group)
            for item in group
        )
        if refs:
            pointers = ", ".join(f"[{ref}]" for ref in refs)
            lines.append(f"- {match.sentence} See {pointers}.")
    return tuple(lines)


def key_result_block(lines: Sequence[str]) -> list[str]:
    if not lines:
        return []
    return [
        "Key results already checked in this library (orientation only; cite the passages):",
        *lines,
    ]


@dataclass(frozen=True)
class ReadResult:
    text: str
    spans: list[dict[str, object]] = field(default_factory=list)


class ToolFailure(RuntimeError):
    """A tool could not run. The message says why, in plain words."""


@dataclass(frozen=True)
class Tool:
    """Something the agent may do: turn a query into evidence."""

    name: str
    about: str  # one line for the planner
    run: Callable[[str], list[Evidence]]
    where: str  # "your library", "the web": how steps are worded
    # Whether a model judges the results' relevance before they are used.
    grade: bool = False
    # Checked library findings that can lead Ask to other original passages.
    key_results: Callable[[str], list[KeyResultMatch]] | None = None


@dataclass(frozen=True)
class Toolbox:
    """The tools offered for this question, and what the writer should know about them."""

    tools: tuple[Tool, ...] = ()
    # Plain facts for the planner and writer: what the library is about, why a tool is off.
    notes: tuple[str, ...] = ()
    # Reads more of a source in the pool (the text around a passage, or the whole page).
    # Raises ToolFailure with a plain reason when it cannot.
    reader: Callable[[Evidence], str | ReadResult] | None = None

    def get(self, name: str) -> Tool | None:
        return next((tool for tool in self.tools if tool.name == name), None)


@dataclass(frozen=True)
class Step:
    """One thing the agent did, as the reply shows it."""

    tool: str
    label: str
    query: str = ""
    found: int = 0
    error: str | None = None

    def out(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "label": self.label,
            "query": self.query,
            "found": self.found,
            "error": self.error,
        }


Events = Callable[[dict[str, Any]], None]
