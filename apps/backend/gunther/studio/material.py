"""Project the evidence and read it whole or in parts for Studio."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from gunther.evidence import Evidence
from gunther.llm import ModelError
from gunther.outputs import Events, Notes, Pool, figure_lines
from gunther.skill_checks import figures_of
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.requests import WORKERS, material_request, request_map
from gunther.studio.state import Run

logger = logging.getLogger("gunther.studio.pipeline")

# How much material is read whole. Beyond it the material is read in parts first (notes).
DIRECT_TOKENS = 60_000
PART_TOKENS = 24_000


def tokens(text: str) -> int:
    """A rough count: a CJK character is about one token, other text about four characters."""

    cjk = sum(1 for char in text if "　" <= char <= "鿿" or "가" <= char <= "힯")
    return cjk + (len(text) - cjk) // 4


# What the steps return --------------------------------------------------------------------


class Ref(BaseModel):
    text: str = ""
    refs: list[int] = Field(default_factory=list)


class Quote(BaseModel):
    text: str = ""
    who: str = ""
    refs: list[int] = Field(default_factory=list)


class PartNotes(BaseModel):
    facts: list[Ref] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


class StudioMaterial:
    @staticmethod
    def _passage(item: Evidence, limit: int | None = None) -> str:
        text = item.context or item.text
        if limit:
            text = text[:limit]
        where = f" · {item.locator}" if item.locator else ""
        citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
        metadata = getattr(citation, "source_metadata", None) or {}
        metadata_line = (
            "\nSource metadata: " + json.dumps(metadata, ensure_ascii=False)
            if metadata
            else ""
        )
        return (
            f"[{item.ref}] ({item.title}{where})\n{text.strip()}"
            + ("\n" + "\n".join(figure_lines(item)) if figures_of(item) else "")
            + (
                "\nFigure provenance: " + json.dumps(figures_of(item), ensure_ascii=False)
                if figures_of(item)
                else ""
            )
            + S.warning_line(item)
            + metadata_line
        )

    def _material(self, items: list[Evidence]) -> str:
        return "\n\n".join(self._passage(item) for item in items)

    # 1. Reading ---------------------------------------------------------------------------

    def read(
        self,
        model,
        run: Run,
        pool: Pool,
        notes: Notes,
        emit: Events,
        kept: dict[str, Any] | None = None,
    ) -> str:
        """The material as the memo step reads it: whole when it fits, else notes per part.
        ``kept``: the notes an earlier run of the same request made, reused when the person's
        own notes are the same."""

        items = sorted(pool.items, key=lambda item: item.ref)
        whole = self._material(items)
        extra = self._notes_text(notes)
        if tokens(whole) <= DIRECT_TOKENS:
            return extra + whole
        if kept and kept.get("notes") == extra and kept.get("digest"):
            self._stage(emit, "read", "Reading the material", "done", "kept from before")
            run.read_digest = kept["digest"]
            return extra + run.read_digest + "\n\n" + whole
        self._stage(emit, "read", "Reading the material", "running")
        parts: list[list[Evidence]] = [[]]
        size = 0
        for item in items:
            cost = tokens(self._passage(item))
            if parts[-1] and size + cost > PART_TOKENS:
                parts.append([])
                size = 0
            parts[-1].append(item)
            size += cost
        what = "a slide deck" if run.spec.kind == "slides" else "a report"
        system = P.NOTES.format(what=what, brief=run.spec.brief or "(none)", kind=what)

        def note(part: list[Evidence]) -> PartNotes | None:
            try:
                return self._json(
                    model, PartNotes, *material_request(self._material(part), system), "low"
                )
            except ModelError as error:
                logger.info("Reading a part failed: %s", error)
                return None

        found = request_map(note, parts, WORKERS)
        lines = []
        for part, result in zip(parts, found, strict=True):
            if result is None:
                # A part that could not be read is given whole, cut to its share.
                lines.append(self._material(part)[:PART_TOKENS])
                continue
            lines += [
                f"- {fact.text} {''.join(f'[{r}]' for r in fact.refs)}" for fact in result.facts
            ]
            lines += [
                f'- Quote: "{quote.text}" — {quote.who} {"".join(f"[{r}]" for r in quote.refs)}'
                for quote in result.quotes
            ]
        self._stage(emit, "read", "Reading the material", "done", f"{len(parts)} parts")
        sources = sorted({item.title for item in items})
        run.read_digest = (
            "Sources: " + "; ".join(sources) + "\n\nNotes from the material:\n" + "\n".join(lines)
        )
        # Keep raw passages too: writers and checks previously fetched them after
        # reading notes. No step may lose evidence to make its opening match.
        return extra + run.read_digest + "\n\n" + whole

    @staticmethod
    def _notes_text(notes: Notes) -> str:
        lines = []
        if notes.knowledge:
            lines.append("The person's saved notes (context, never cited):")
            lines += [f"- {title}: {text[:600]}" for title, text in notes.knowledge]
        if notes.discussions:
            lines.append("The person's earlier discussions (context, never cited):")
            lines += [f"- {title}: {text[:400]}" for title, text in notes.discussions]
        if notes.extraction_gaps:
            lines.append("Selected-source access/extraction gaps (do not guess around them):")
            lines += [f"- {text[:600]}" for text in notes.extraction_gaps]
        return ("\n".join(lines) + "\n\n") if lines else ""
