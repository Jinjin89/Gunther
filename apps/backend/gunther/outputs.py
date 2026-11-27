"""Evidence pools, assembly, and checks shared by output creation and saved versions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from gunther.agent import AskAgent, Limits
from gunther.checks import (
    Verdicts,
    coverage_of,
    issue_view,
    map_refs,
)
from gunther.citations import (
    citation_numbers,
    renumber_citations,
    sentence_spans,
    strip_citations,
)
from gunther.evidence import Evidence, Step, Tool, ToolFailure, key_result_block
from gunther.llm import ModelGateway, ModelInfo
from gunther.markup import markdown_lines, parse_markup
from gunther.model_profiles import Effort
from gunther.output_shapes import SHAPES, OutputUnit
from gunther.output_types import OutputLength
from gunther.schemas import ConversationCitationOut
from gunther.skill_checks import (
    Context,
    Page,
    chart_numbers_cited,
    diagram_edge_rows,
    diagram_problem,
    figures_of,
)
from gunther.slide_scene import (
    SCENE_HEADER,
    SceneEnvelope,
    decode_scene,
)
from gunther.studio.format import design_of, without_design

EVIDENCE_CHARS = 900
CARRY_CHARS = 600
UNCHANGED = "UNCHANGED"


IMAGE = re.compile(r"!\[([^\]\n]*)\]\(gunther-asset:([A-Za-z0-9_-]+)\)")


def kept_figures(pool: Pool) -> set[str]:
    """The asset ids of the figures kept with the passages in a pool: the only images an
    output may show, since each belongs to a source it can cite."""

    return {str(figure["assetId"]) for item in pool.items for figure in figures_of(item)}


def drop_unknown_figures(text: str, kept: set[str]) -> tuple[str, list[str]]:
    """The text without the images that are not among ``kept``, and their captions. A text
    with none to leave out is given back exactly as it was."""

    left_out: list[str] = []

    def keep(match: re.Match[str]) -> str:
        if match.group(2) in kept:
            return match.group(0)
        left_out.append(match.group(1).strip() or "Figure")
        return ""

    shown = IMAGE.sub(keep, text)
    if not left_out:
        return text, []
    # The lines the images stood on alone are taken out with them.
    shown = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", re.sub(r"(?m)^[ \t]+$", "", shown))
    return shown.strip("\n"), left_out


VALUE_IN = re.compile(r"(?<![A-Za-z0-9_.+\-−–—])[+\-−–—]?\d[\d,]*(?:\.\d+)?%?")


def _normalized_chart_value(value: str) -> str:
    return value.replace("−", "-").replace("–", "-").replace("—", "-").replace(",", "")


def chart_unverified(
    text: str,
    issues: Sequence[Issue] | None,
    coverage: Mapping[str, Any] | None,
    by_ref: dict[int, Evidence],
    index: int,
) -> tuple[list[str], str | None]:
    """Why a chart slide may not be drawn as a chart, or None when it may.

    Every value must be cited and stated by its cited passage (a check in code), and the
    Checker must have looked at the table's values and found them supported: a claim with a
    value in it must be "supported", and no finding may be about a value. Findings about the
    speaker notes or the line under the chart do not stop the chart; they are shown with the
    slide like any other.

    Returns the problems the code check found (to be shown as findings) and the reason."""

    layout, body = layout_of(text)
    page = Page(index, heading_of(body), layout or "bar_chart", body)
    problems = [
        item.message for item in chart_numbers_cited([page], {}, Context("slides", evidence=by_ref))
    ]
    if problems:
        return problems, problems[0]
    values = {
        _normalized_chart_value(value).rstrip("%")
        for line in page.body
        if line.strip().startswith("|")
        for value in VALUE_IN.findall(
            strip_citations(line, bare_only=True, include_unsourced=False)
        )
    }

    def about_values(claim: str) -> bool:
        normalized = {_normalized_chart_value(v).rstrip("%") for v in VALUE_IN.findall(claim)}
        return bool(normalized & values)

    for issue in issues or ():
        if about_values(issue.claim):
            return [], f"The Checker found a problem with a value: “{issue.claim[:120]}”"
    claims = [claim for claim in (coverage or {}).get("claims", []) if about_values(claim["text"])]
    if issues is None or not claims:
        return [], "Its verification is incomplete: its values were not checked."
    if any(claim.get("verdict") != "supported" for claim in claims):
        return [], "Its verification is incomplete: not every value was found supported."
    return [], None


def figure_lines(item: Evidence) -> list[str]:
    return [
        f"    Retained figure: ![{figure.get('caption', 'Figure')}]"
        f"(gunther-asset:{figure['assetId']}) — cite [{item.ref}]. "
        "Caption is source text; image contents/numbers are not verified."
        for figure in figures_of(item)
    ]


def _with_metadata(kept: Evidence, other: Evidence) -> Evidence:
    """``kept`` with the figures and extraction warnings ``other`` (another reading of the same
    passage) has and it lacks."""

    def citation(item: Evidence):
        return item.payload[0] if isinstance(item.payload, tuple) and item.payload else None

    mine, theirs = citation(kept), citation(other)
    if getattr(mine, "figures", None) is None or getattr(theirs, "figures", None) is None:
        return kept
    figures = list(mine.figures)
    figures += [f for f in theirs.figures if f not in figures]
    warnings = list(mine.extraction_warnings)
    warnings += [w for w in theirs.extraction_warnings if w not in warnings]
    if figures == mine.figures and warnings == mine.extraction_warnings:
        return kept
    merged = mine.model_copy(update={"figures": figures, "extraction_warnings": warnings})
    return replace(kept, payload=(merged, *kept.payload[1:]), extraction_warnings=tuple(warnings))


@dataclass(frozen=True)
class OutputLimits:
    """How far one build may go. Defaults suit most; nothing else depends on them."""

    queries: int = 3  # searches per section
    results: int = 40  # search results one section grades
    per_section: int = 8  # new passages per section
    pool: int = 120  # passages in the whole build
    seed: int = 40  # seed passages from earlier work
    leads: int = 2  # [?] claims looked up per section
    shown: int = 30  # passages a writer sees
    sentences: int = 40  # cited sentences one support check looks at


# Section rules ----------------------------------------------------------------------
#
# The desktop splits a document with the same rules (src/outputs/sections.ts), and both
# are tested with the same cases. A fence is a line starting with ``` or ~~~.
#
# - A report's sections start at lines beginning with "## ". What comes before the first
#   one (the title, the brief) is the preamble.
# - A deck's slides are the parts between lines that are only "---". Empty ones are left out.
# - A section's hash is the sha256 of its text with "\n" line ends, trimmed.
# - A slide made by a skill starts with a marker line that says how it is drawn:
#   "<!-- layout: compare -->", and on the title slide also "<!-- layout: title; accent:
#   blue -->". Code writes it from the plan; the model never does. It is a comment: the
#   heading of a slide is the first line under it, the Checker never sees it, and the hash of
#   the section covers it. A slide without one (older decks) has no layout of its own.

ACCENTS = ("ink", "blue", "teal", "red", "amber", "violet")
THEMES = ("classic", "briefing", "editorial", "keynote")
# At the start of a slide, after any blank lines: its layout and keyed metadata.
LAYOUT_MARK = re.compile(
    r"[ \t\r\n]*<!--[ \t]*layout:[ \t]*(?P<layout>[a-z][a-z0-9_]*)[ \t]*"
    r"(?P<attrs>(?:;[ \t]*[a-z][a-z0-9_]*:[ \t]*[a-z][a-z0-9_-]*[ \t]*)*)"
    r"-->[ \t]*(?:\r?\n|\Z)"
)
MARK_ATTR = re.compile(r";[ \t]*(?P<key>[a-z][a-z0-9_]*):[ \t]*(?P<value>[a-z][a-z0-9_-]*)[ \t]*")
COMPOSITIONS = ("standard", "split", "spotlight")
COMPOSITION_LAYOUTS = {
    "standard": frozenset(
        (
            "title",
            "big_number",
            "compare",
            "two_column",
            "flow",
            "timeline",
            "bar_chart",
            "line_chart",
            "cards",
            "quote",
            "figure",
            "diagram",
            "text",
            "takeaways",
            "composed",
        )
    ),
    "split": frozenset(("big_number", "quote", "figure")),
    "spotlight": frozenset(("big_number", "quote", "text")),
}


def marker_attributes(attrs: str) -> dict[str, str]:
    return {match.group("key"): match.group("value") for match in MARK_ATTR.finditer(attrs)}


def document_spans(content: str, kind: str) -> tuple[int, list[tuple[int, int]]]:
    """The preamble end and the exact ranges of trimmed parts in the saved document."""
    return SHAPES[kind].spans(content)


def split_document(content: str, kind: str) -> tuple[str, list[str]]:
    """A document as its preamble and the stored Markdown of each section."""
    preamble, units = SHAPES[kind].split(content)
    return preamble, [unit.text for unit in units]


def document_units(content: str, kind: str) -> list[OutputUnit]:
    return SHAPES[kind].split(content)[1]


def join_document(preamble: str, sections: Sequence[str], kind: str) -> str:
    return SHAPES[kind].join(preamble, sections)


def section_hash(section: str | OutputUnit) -> str:
    unit = section if isinstance(section, OutputUnit) else OutputUnit(section)
    normal = unit.text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if unit.ancestors:
        normal = json.dumps((*unit.ancestors, normal), ensure_ascii=False)
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()


def check_heading_refs(
    body: str, lines: list[str], by_ref: Mapping[int, object] | None = None
) -> str:
    """Check a heading's conclusion against the passages used by its section."""
    refs = "".join(
        f"[{n}]" for n in citation_numbers(body, bare_only=True)
        if by_ref is None or n in by_ref
    )
    for index, (line, protected) in enumerate(markdown_lines("\n".join(lines))):
        if not protected and re.match(r"^#{1,2}\s", line):
            if refs and not citation_numbers(line, bare_only=True):
                lines[index] = line.rstrip() + " " + refs
            break
    return "\n".join(lines)


class Marked(NamedTuple):
    """A slide split at its layout marker."""

    line: str  # the marker line as it was written; empty when there is none
    layout: str | None
    accent: str | None  # only the title slide has one, and only one of ACCENTS
    theme: str | None  # only the title slide has one, and only one of THEMES
    composition: str
    body: str  # the slide without the marker line, and without the blank lines after it
    scene_line: str
    scene: SceneEnvelope | None
    design_version: int = 0
    design: str = ""  # a designed slide's HTML; it is not part of ``body``


def _marked(text: str) -> Marked:
    design = design_of(text) or ""
    if design:
        text = without_design(text)
    found = LAYOUT_MARK.match(text)
    if found is None:
        return Marked("", None, None, None, "standard", text, "", None, design=design)
    layout = found.group("layout")
    attrs = marker_attributes(found.group("attrs"))
    accent, theme = attrs.get("accent"), attrs.get("theme")
    composition = attrs.get("composition", "standard")
    if composition not in COMPOSITIONS or layout not in COMPOSITION_LAYOUTS[composition]:
        composition = "standard"
    body = re.sub(r"\A(?:[ \t]*\r?\n)+", "", text[found.end() :])
    scene_line = ""
    scene = None
    scene_candidate = body.splitlines()[0] if body.splitlines() else ""
    scene_match = SCENE_HEADER.fullmatch(scene_candidate)
    if scene_match:
        scene_line = scene_candidate.strip()
        scene = decode_scene(scene_line)
        body = re.sub(r"\A(?:[ \t]*\r?\n)+", "", body[len(scene_candidate) :])
    marker_line = found.group(0).strip()
    if scene_line:
        marker_line = f"{marker_line}\n{scene_line}"
    return Marked(
        marker_line,
        layout,
        accent if layout == "title" and accent in ACCENTS else None,
        theme if layout == "title" and theme in THEMES else None,
        composition,
        body,
        scene_line,
        scene,
        1 if layout == "title" and attrs.get("design") == "v1" else 0,
        design,
    )


def layout_of(text: str) -> tuple[str | None, str]:
    """A slide's layout from its marker, and the slide without the marker. A slide with no
    marker has no layout and is given back as it is."""

    marked = _marked(text)
    return marked.layout, marked.body


def with_layout(
    text: str,
    layout: str,
    accent: str | None = None,
    theme: str | None = None,
    composition: str | None = None,
    design_version: int | None = None,
) -> str:
    """The slide with the marker for ``layout`` as its first line, in place of any it had. The
    title slide keeps the accent it had, or takes ``accent``; no other slide has one."""

    marked = _marked(text)
    found = LAYOUT_MARK.match(text)
    attrs = marker_attributes(found.group("attrs")) if found else {}
    kept = (accent or marked.accent) if layout == "title" else None
    kept_theme = (theme or marked.theme) if layout == "title" else None
    kept_composition = composition or marked.composition
    if kept_composition not in COMPOSITIONS or layout not in COMPOSITION_LAYOUTS[kept_composition]:
        kept_composition = "standard"
    values: dict[str, str] = {}
    if kept in ACCENTS and (kept != "ink" or design_version == 1 or marked.design_version == 1):
        values["accent"] = kept
    if kept_theme in THEMES and kept_theme != "classic":
        values["theme"] = kept_theme
    if kept_composition != "standard":
        values["composition"] = kept_composition
    values.update(
        {
            key: value
            for key, value in attrs.items()
            if key not in {"accent", "theme", "composition"}
        }
    )
    if layout == "title" and design_version is not None:
        if design_version == 1:
            values["design"] = "v1"
        else:
            values.pop("design", None)
    suffix = "".join(f"; {key}: {value}" for key, value in values.items())
    line = f"<!-- layout: {layout}{suffix} -->"
    prefix = f"{line}\n{marked.scene_line}" if marked.scene_line else line
    return f"{prefix}\n{marked.body}" if marked.body else prefix


def unmarked(text: str, spec: Spec) -> str:
    """A section as a model is shown it: a deck's slide without its layout marker."""

    return layout_of(text)[1] if spec.kind == "slides" else text


def heading_of(section: str | OutputUnit) -> str:
    if isinstance(section, OutputUnit):
        if section.label is not None:
            return section.label[:160]
        section = section.text
    for line in layout_of(section)[1].splitlines():
        line = line.strip()
        if line:
            return re.sub(r"^#{1,6}\s*", "", line)[:160]
    return ""


def title_of(content: str) -> str | None:
    """The first top-level heading, at most 160 characters."""

    for line, fenced in markdown_lines(content):
        if not fenced and line.startswith("# "):
            return line[2:].strip()[:160] or None
    return None


# The pool -----------------------------------------------------------------------------


class Pool:
    """The passages one output may cite, each under a number it keeps for good."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items: list[Evidence] = []
        self.known: dict[tuple[str, str, str], Evidence] = {}
        self.top = 0

    def __len__(self) -> int:
        return len(self.items)

    def adopt(self, item: Evidence) -> Evidence:
        """The pool's copy of a passage: the same number if it is held, else a new one."""

        held = self.known.get(item.key)
        if held is not None:
            citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
            kept = held
            if getattr(citation, "support_spans", None) and len(item.text) > len(held.text):
                kept = replace(item, ref=held.ref)  # the longer reading of the passage
            # Its figures and extraction warnings stay, whichever reading brought them.
            kept = _with_metadata(kept, item if kept is held else held)
            if kept is not held:
                self.items[self.items.index(held)] = kept
                self.known[item.key] = kept
            return kept
        self.top += 1
        fresh = replace(item, ref=self.top)
        self.items.append(fresh)
        self.known[fresh.key] = fresh
        return fresh

    def restore(self, items: Sequence[Evidence]) -> None:
        """Take passages that already have their numbers (an earlier version's)."""

        for item in items:
            self.items.append(item)
            self.known[item.key] = item
            self.top = max(self.top, item.ref or 0)

    def by_ref(self) -> dict[int, Evidence]:
        return {item.ref: item for item in self.items if item.ref is not None}


# What is asked and what comes back -------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    kind: str  # the output shape
    audience: str
    output_type: str | None = None
    brief: str = ""
    title: str | None = None  # asked for; otherwise the planner's
    theme: str | None = None  # a deck's visual theme
    design_version: int | None = None
    deck_style: str | None = None
    length: OutputLength | None = None
    language: str | None = None
    emphasize: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()


@dataclass(frozen=True)
class Notes:
    """Orientation and the person's earlier work, for the planner and writer. Never cited."""

    knowledge: tuple[tuple[str, str], ...] = ()  # (title, text)
    discussions: tuple[tuple[str, str], ...] = ()  # (title, summary)
    # What the sources are about, for the planner only: (title, overview).
    sources: tuple[tuple[str, str], ...] = ()
    # Selected-source access/extraction gaps; context for the planner, never citations.
    extraction_gaps: tuple[str, ...] = ()
    key_results: tuple[str, ...] = ()  # orientation lines using this pool's numbers


@dataclass(frozen=True)
class Issue:
    claim: str
    verdict: str  # unsupported | unverified | unsourced | contradicted
    note: str = ""

    def out(self) -> dict[str, str]:
        return {"claim": self.claim, "verdict": self.verdict, "note": self.note}


@dataclass(frozen=True)
class Planned:
    heading: str
    goal: str = ""
    layout: str = ""
    refs: tuple[int, ...] = ()
    id: str = ""
    notes: str = ""


@dataclass(frozen=True)
class FirstWrite:
    workspace_id: str
    library_id: str
    conversation_id: str
    work_key: str
    revision: str
    request_id: str
    server_boot_id: str
    attempt_id: str


@dataclass(frozen=True)
class ParentWrite:
    workspace_id: str
    library_id: str
    conversation_id: str | None
    lineage_id: str
    parent_id: str


WriteAuthority = FirstWrite | ParentWrite


@dataclass(frozen=True)
class BuiltOutput:
    title: str
    content: str
    outline: list[dict[str, str]]
    # The passages the text cites, with ``ref`` set to their number in the text.
    citations: list[ConversationCitationOut]
    # One entry per checked section of the final text: {"hash", "verdicts"}.
    checks: list[dict[str, Any]]
    model: ModelInfo
    effort: Effort | None
    notes: tuple[str, ...] = ()
    # Made by a skill: the approach it followed and the run ({name, version, skipped}).
    approach: dict[str, Any] | None = None
    skill: dict[str, Any] | None = None
    # The pool number of each citation, in order: ``citations[n - 1]`` is the pool's passage
    # ``pool_refs[n - 1]``, the one the text calls [n].
    pool_refs: tuple[int, ...] = ()


Events = Callable[[dict[str, Any]], None]


# Prompts ----------------------------------------------------------------------------------

def _origin(item: Evidence) -> str:
    return " · ".join(
        part
        for part in (
            item.kind,
            getattr(item.payload[0], "source_origin", "") if item.payload else "",
            item.title,
            item.locator,
            item.status if item.status and item.kind == "library" else "",
        )
        if part
    )


def _notes_block(notes: Notes) -> list[str]:
    lines = ["", *key_result_block(notes.key_results)] if notes.key_results else []
    # Their own numbers and markers belong to the conversation they came from, not to this
    # pool, so they are left out: a writer must not copy one as if it were a citation.
    if notes.knowledge:
        lines += ["", "Saved knowledge (the user's own conclusions; not citable):"]
        lines += [f"- {title}: {_plain(text)[:600]}" for title, text in notes.knowledge]
    if notes.discussions:
        lines += ["", "Earlier discussions (not citable):"]
        lines += [f"- {title}: {_plain(text)[:300]}" for title, text in notes.discussions]
    return lines


# Shaping what the writer returns -----------------------------------------------------------


def _plain(chunk: str) -> str:
    """A sentence without its numbers and markers."""

    text = " ".join(strip_citations(chunk, bare_only=True).split())
    return re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", text)


def cited_sentences(text: str) -> list[tuple[int, int, str, list[int]]]:
    """The sentences that cite passages: (start, end, sentence, numbers). A slide's marker is
    no sentence; the positions are still those of the whole text."""

    shift = len(text) - len(layout_of(text)[1])
    markup = parse_markup(text[shift:])
    found = []
    for begin, end in sentence_spans(markup):
        chunk = markup.prose_slice(begin, end)
        numbers = citation_numbers(chunk, bare_only=True)
        if numbers:
            found.append((begin + shift, end + shift, chunk.text, numbers))
    return found


@dataclass
class Draft:
    """A section while it is being worked on."""

    text: str
    # None until a check has looked at it.
    verdicts: Verdicts | None = None

    @property
    def issues(self) -> list[Issue] | None:
        if self.verdicts is None:
            return None
        return [Issue(**row) for row in issue_view(self.verdicts)]

    @property
    def coverage(self) -> dict[str, Any] | None:
        if self.verdicts is None:
            return None
        return coverage_of(self.verdicts, section_hash(self.text))


def numbered_check(
    draft: Draft, text: str | OutputUnit, numbers: Mapping[int, int],
) -> dict[str, Any]:
    state = map_refs(draft.verdicts or {}, numbers, lambda t: renumber_citations(t, numbers))
    return {"hash": section_hash(text), "verdicts": state}


def diagram_fallback_reason(draft: Draft) -> str | None:
    """Only draw a diagram when every cited edge received a supported Checker verdict."""
    marked = _marked(draft.text)
    if marked.layout != "diagram":
        return None
    if problem := diagram_problem(draft.text):
        return problem
    if not draft.coverage or draft.coverage.get("status") != "complete":
        return "The Checker did not complete coverage of this diagram's relationships."

    claims = draft.coverage.get("claims", [])

    def clean(value: str) -> str:
        value = strip_citations(value, bare_only=True, include_unsourced=False)
        value = re.sub(r"[*_`|]", " ", value)
        return " ".join(value.split()).casefold()

    for row in diagram_edge_rows(draft.text):
        expected = clean(" ".join(row))
        row_refs = set(citation_numbers(row[1], bare_only=True))
        matched = [
            claim
            for claim in claims
            if expected
            and expected in clean(str(claim.get("text", "")))
            and row_refs.issubset(set(claim.get("refs", [])))
        ]
        if not matched or any(claim.get("verdict") != "supported" for claim in matched):
            return (
                "One or more relationship claims were unsupported, unverified, or "
                "unchecked by the Checker."
            )
    return None


def diagram_as_text(draft: Draft) -> Draft:
    """Keep every relation row, citation and note when a diagram cannot be trusted or laid out."""
    if _marked(draft.text).layout != "diagram":
        return draft
    return Draft(
        with_layout(draft.text, "text"),
        draft.verdicts,
    )


def fallback_unverified_diagram(draft: Draft, index: int, skipped: list[dict[str, Any]]) -> Draft:
    if reason := diagram_fallback_reason(draft):
        skipped.append(
            {
                "step": "layout",
                "label": f"Slide {index + 1} shown as a table/text",
                "reason": reason,
            }
        )
        return diagram_as_text(draft)
    return draft


class OutputAgents:
    """Shared evidence collection and presentation for Studio writers."""

    def __init__(self, gateway: ModelGateway, limits: OutputLimits | None = None) -> None:
        self.gateway = gateway
        self.limits = limits or OutputLimits()
        # Ask's helpers (grading, the audit, the look-up of own-knowledge claims).
        self.ask = AskAgent(gateway, limits=Limits(leads=self.limits.leads))
        # What was changed in a section so that it could be saved (a figure left out, a chart
        # drawn as a table). A skill's version lists these with its skipped steps.
        self.skipped: list[dict[str, str]] = []

    def _research_section(
        self,
        model: ModelInfo,
        question: str,
        queries: Sequence[str],
        tool: Tool,
        pool: Pool,
        allowance: int,
        emit: Events,
        section: int | None,
    ) -> list[int]:
        found: list[Evidence] = []
        for query in queries:
            label = f"Searched {tool.where} for “{query}”"
            emit(
                {
                    "type": "step",
                    "state": "running",
                    "tool": tool.name,
                    "label": label,
                    "section": section,
                }
            )
            try:
                results = tool.run(query)
            except ToolFailure as error:
                step = Step(tool.name, label, query, 0, str(error))
                emit({"type": "step", "state": "done", **step.out(), "section": section})
                continue
            step = Step(tool.name, label, query, len(results))
            emit({"type": "step", "state": "done", **step.out(), "section": section})
            have = {item.key for item in found}
            found += [item for item in results if item.key not in have]
        found = found[: self.limits.results]
        if tool.grade and found:
            found = self.ask.relevant(model, question, "; ".join(queries), found)
        refs: list[int] = []
        for item in found:
            held = pool.known.get(item.key)
            if held is None and allowance > 0 and len(pool) < pool.limit:
                held = pool.adopt(item)
                allowance -= 1
            if held is not None and held.ref is not None and held.ref not in refs:
                refs.append(held.ref)
        return refs

    # Writer -----------------------------------------------------------------------------

    def _pool_lines(self, pool: Pool, first: Sequence[int]) -> list[str]:
        by_ref = pool.by_ref()
        order = [by_ref[ref] for ref in first if ref in by_ref]
        order += [item for item in pool.items if item.ref not in first]
        shown = order[: self.limits.shown]
        if not shown:
            return ["The pool: empty; nothing relevant was found."]
        lines = ["The pool:"]
        for item in shown:
            lines.append(f"[{item.ref}] ({_origin(item)}) {item.text[:EVIDENCE_CHARS]}")
            lines.extend(figure_lines(item))
        return lines
