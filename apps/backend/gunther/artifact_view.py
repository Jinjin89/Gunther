"""Project an artifact conversation and its numbered references for decisions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from gunther.artifact_workspace import (
    ConversationBrief,
    Line,
    Work,
    Workspace,
    WorkSpec,
    active_work,
    approach_state,
    locate,
    outline_state,
    status,
)
from gunther.citations import citation_numbers, renumber_citations
from gunther.markup import Markup
from gunther.output_types import DECK_USES, OUTPUT_TYPES, SHAPES
from gunther.outputs import Notes, Pool, layout_of
from gunther.schemas import PreferenceLineOut
from gunther.studio import format as studio_format
from gunther.studio.format import without_design


@dataclass(frozen=True)
class AgentLimits:
    decisions: int = 12  # orchestrator calls in one turn
    calls: int = 200  # all model calls in one turn (a full build is about 100)
    searches: int = 8  # search_library / search_web / read_source outside Studio steps
    questions_in_a_row: int = 2
    history: int = 4  # exchanges shown word for word
    log: int = 12  # change-log lines shown
    pool: int = 160  # sources one conversation may hold


class Numbering:
    """The numbers the user sees. A saved version numbers its sources 1, 2, 3 (``version_map``
    says which pool source each one is); sources it does not cite follow in the pool's order.
    Parts of the work cite the pool's own numbers, which never change."""

    def __init__(self, version_map: Sequence[int], pool_numbers: Sequence[int]) -> None:
        held = set(version_map)
        self.order = [*version_map, *(n for n in pool_numbers if n not in held)]
        self._place = {pool: view for view, pool in reversed(list(enumerate(self.order, 1)))}

    def view(self, pool_number: int) -> int | None:
        return self._place.get(pool_number)

    def pool(self, view_number: int) -> int | None:
        return self.order[view_number - 1] if 1 <= view_number <= len(self.order) else None

    def text(self, text: str | Markup) -> str:
        """Text citing pool numbers, as it reads with the numbers the user sees."""

        return renumber_citations(text, self._place)


def ranges(numbers: Sequence[int]) -> str:
    """Numbers as a short list: 3, 5–7, 9."""

    out: list[str] = []
    ordered = sorted(set(numbers))
    start = 0
    while start < len(ordered):
        end = start
        while end + 1 < len(ordered) and ordered[end + 1] == ordered[end] + 1:
            end += 1
        out.append(
            str(ordered[start])
            if end == start
            else f"{ordered[start]}{'–' if end > start + 1 else ', '}{ordered[end]}"
        )
        start = end + 1
    return ", ".join(out)


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# The view -----------------------------------------------------------------------------------------

MARKS = {"you": "you said", "edited": "edited", "guessed": "guessed"}


@dataclass
class View:
    """Everything the orchestrator is shown (§7.6), before it is put into words."""

    library: str
    web: bool
    workspace: Workspace
    brief: ConversationBrief
    pool: Pool
    versions: dict[str, int]
    history: Sequence[tuple[str, str]]
    notes: Sequence[str]
    not_done: Sequence[str]
    left: tuple[int, int, int]  # decisions, model calls, searches
    kind: str  # of the latest message
    text: str
    focus: dict[str, Any] | None = None
    limits: AgentLimits = field(default_factory=AgentLimits)
    # The question this message answers (the workspace's pending one, as the turn began).
    asked: Any = None
    material: Notes = field(default_factory=Notes)
    revising: bool = False


def _line(label: str, line: Line | PreferenceLineOut | None, empty: str = "not set") -> str:
    return f"{label} {line.text} ({MARKS[line.mark]})" if line else f"{label} {empty}"


def _deck_text(text: str, notes: int | None = 150) -> list[str]:
    """A slide as the orchestrator reads it: without its marker, and its notes cut."""

    lines = layout_of(without_design(text))[1].splitlines()
    if notes is None:
        return lines
    at = next((n for n, line in enumerate(lines) if line.startswith("Note:")), None)
    if at is None:
        return lines
    return [*lines[:at], _one_line(" ".join(lines[at:]), notes)]


def _paragraphs(text: str) -> list[str]:
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", body) if chunk.strip()]


def resolve_passage(passage: str, body: str, deck: bool) -> str:
    """The passage an edit means, as the part's own text. The view shows a report's paragraphs
    as "¶n" openings and a slide's notes cut short, so "¶2" means paragraph 2, and a passage
    cut short with "…" runs on to the end of its paragraph (of the notes, on a slide). Anything
    else is the passage as quoted."""

    quoted = passage.strip()
    number = re.fullmatch(r"¶\s*(\d+)", quoted)
    if number and not deck:
        paragraphs = _paragraphs(body)
        at = int(number.group(1))
        return paragraphs[at - 1] if 1 <= at <= len(paragraphs) else passage
    if quoted.endswith(("…", "...")):
        stem = quoted.rstrip(".…").rstrip()
        stem = re.sub(r"^¶\s*\d+\s+", "", stem)
        found = locate(stem, body) if len(stem) >= 12 else None
        if found is not None:
            start = found[0]
            if deck and body[start:].startswith("Note:"):
                return body[start:].rstrip()
            end = body.find("\n\n", start)
            return body[start : end if end >= 0 else len(body)].rstrip()
    return passage


def part_lines(
    work: Work, brief: ConversationBrief, numbering: Numbering, *, written_parts: bool = True,
) -> list[str]:
    """The map: each part with where it stands and what it says."""

    deck = work.spec.kind == "slides"
    shape = SHAPES[work.spec.kind]
    lines: list[str] = []
    for part in status(work, brief):
        item = next(i for i in work.outline if i.id == part.item_id)
        written = work.parts.get(item.id) if written_parts else None
        state = {
            "ok": "ok",
            "not_written": "not written yet",
            "out_of_date": f"out of date: {part.why}",
            "not_checked": "not re-checked",
        }[part.state]
        head = f"  {part.position} " + (f"[{item.layout or 'text'}] " if deck else "")
        extras = ""
        refs = citation_numbers(shape.markup(written.text)) if written else item.refs
        view = [numbering.view(ref) or ref for ref in refs]
        if view and (not deck or part.state != "ok"):
            extras += f" · sources {ranges(view)}"
        if item.cards and part.state != "ok":
            extras += f" · cards {', '.join(item.cards)}"
        lines.append(f"{head}{item.heading}" + (f" · {state}{extras}" if written_parts else ""))
        if item.notes:
            lines.append(f"      Writer notes: {_one_line(item.notes, 400)}")
        if written is None:
            continue
        if deck:
            lines += [f"      {row}" for row in _deck_text(numbering.text(written.text)) if row]
        else:
            lines += [
                f"      ¶{n} {_one_line(numbering.text(chunk), 200)}"
                for n, chunk in enumerate(_paragraphs(written.text), start=1)
            ]
    return lines


def build_view(v: View) -> str:
    """The orchestrator's view, as text. Its headings and their order are a contract."""

    ws, brief = v.workspace, v.brief
    work = active_work(ws)
    out = [f"Library: {v.library}. Web: {'on' if v.web else 'off'}."]
    out.append(
        "Mode: revise the active verified version." if v.revising else "Mode: plan for approval."
    )
    material = v.material
    has_material = bool(material.sources or material.knowledge or material.discussions)
    if has_material:
        out.append("Material available in the chosen scope (overview, not citable passages):")
        for kind, items in (
            ("Document", material.sources),
            ("Saved answer", material.knowledge),
            ("Discussion", material.discussions),
        ):
            out += [
                f"- {kind}: {_one_line(title, 160)} — {_one_line(text, 400)}"
                for title, text in items
            ]
    for kind in [work.spec.kind] if work else ["slides", "report"]:
        options = (
            f"Layouts: {', '.join(studio_format.LAYOUTS)}"
            if kind == "slides"
            else "Types: " + ", ".join(
                f"{item.id} ({item.label})" for item in OUTPUT_TYPES.values() if item.shape == kind
            )
        )
        if kind == "slides":
            options += f". Uses: {', '.join(DECK_USES)}"
        stages = "understand → propose"
        if v.revising:
            stages = (
                "make → verify" if SHAPES[kind].pipeline == "direct"
                else "understand → plan → write → review"
            )
        out.append(f"{kind} in Studio: {stages}. {options}")
    out.append("Brief (you said = the user's words; edited = the user typed it; guessed = yours):")
    shown = [
        _line("- goal:", brief.goal, "") if brief.goal else None,
        _line("- audience:", brief.audience, "") if brief.audience else None,
        *(_line("- constraint:", line) for line in brief.constraints),
        *(_line("- emphasize:", line) for line in brief.emphasize),
        *(_line("- avoid:", line) for line in brief.avoid),
        *(f"- open: {line.text}" for line in brief.open),
    ]
    out += [row for row in shown if row] or ["- (nothing yet)"]
    if ws.works:
        listed = []
        for number, each in enumerate(ws.works, start=1):
            name = SHAPES[each.spec.kind].name
            saved = f"version {v.versions[each.key]}" if each.key in v.versions else "not saved yet"
            active = " (active)" if each.key == ws.active else ""
            listed.append(f'[{number}] {name} "{each.title or "untitled"}" {saved}{active}')
        out.append("Artifacts: " + " · ".join(listed))
    else:
        out.append("Artifacts: none yet.")
    numbering = Numbering(
        work.version_map if work and v.revising else [], [i.ref for i in v.pool.items if i.ref],
    )
    pointed = ""
    if work:
        shape = SHAPES[work.spec.kind]
        name = shape.name
        spec = work.spec
        out.append(
            f'The active {name} "{work.title or "untitled"}": '
            + " · ".join(
                part
                for part in (
                    _line("use", spec.use, "not set") if name == "deck" else "",
                    _line("length", spec.length),
                    _line("output type", spec.output_type) if name != "deck" else "",
                )
                if part
            )
        )
        if work.plan is not None:
            out.append("The plan's catalog type is fixed. Start a separate plan to change it.")
        approach, outline = approach_state(work, brief), outline_state(work)
        said = {
            "ok": "up to date",
            "not_written": "not written yet",
            "out_of_date": f"out of date ({approach.why})",
        }[approach.state]
        line = f"Approach: {said}."
        if outline.state == "out_of_date":
            line += f" Outline: out of date ({outline.why})."
        elif outline.state == "not_written" and approach.state != "not_written":
            line += " Outline: not planned yet."
        out.append(line)
        if work.approach:
            facts = [
                f"{label}: {work.approach[key]}"
                for label, key in (
                    ("Question", "question"),
                    ("Answer", "answer"),
                    ("Structure", "structure"),
                )
                if work.approach.get(key)
            ]
            extra = f" ({v.pool.top - work.approach_top} sources found since it was written)"
            out.append(
                "  "
                + " / ".join(_one_line(fact, 160) for fact in facts)
                + (extra if v.pool.top > work.approach_top else "")
            )
        out.append("Parts:")
        out += part_lines(work, brief, numbering, written_parts=v.revising) or [
            "  (nothing is planned yet)",
        ]
        focus = v.focus or {}
        if focus and (focus.get("section") or focus.get("quote")):
            how = "selected" if focus.get("selected", True) else "on screen"
            position = focus.get("section")
            line = f"Pointed at ({how}): "
            if position and 1 <= position <= len(work.outline):
                line += f"part {position}, in full"
                written = work.parts.get(work.outline[position - 1].id) if v.revising else None
                if written is not None:
                    shown_text = numbering.text(shape.markup(
                        "\n".join(_deck_text(written.text, None))
                        if name == "deck"
                        else written.text
                    ))
                    pointed = "\n".join(
                        f"      {row}" if row.strip() else "" for row in shown_text.splitlines()
                    )
            elif position:
                line += f"part {position} (there is no such part)"
            if focus.get("quote"):
                line += f"{'; ' if position else ''}selected text: “{focus['quote']}”"
            out.append(line)
            if pointed:
                out.append(pointed)
    pool_items = [i for i in v.pool.items if i.ref is not None]
    newest = list(reversed(pool_items))[:10]
    line = f"Pool: {len(pool_items)} sources"
    if not pool_items and has_material:
        line += "; no evidence passages loaded yet; the material above is available."
    if newest:
        names = [
            f"[{numbering.view(i.ref) or i.ref}] {_one_line(i.title, 60)} ({i.kind})"
            for i in newest
        ]
        line += "; newest: " + " · ".join(names)
        if len(pool_items) > len(newest):
            line += f" · and {len(pool_items) - len(newest)} more"
    out.append(line)
    out.append("Changes so far:")
    out += [f"- {row}" for row in ws.log[-v.limits.log :]] or ["- (none)"]
    out.append("Conversation:")
    for said, replied in list(v.history)[-v.limits.history :]:
        out += [f"You: {_one_line(said, 600)}", f"Gunther: {_one_line(replied, 600)}"]
    if not v.history:
        out.append("(nothing before this message)")
    if v.asked is not None:
        options = " | ".join(v.asked.options)
        status = "this message answers it" if v.kind == "answer" else "pending"
        out.append(f'You asked: "{v.asked.question}" Options: {options} ({status})')
    out.append("This turn so far:")
    out += [f"- {row}" for row in v.notes] or ["- (nothing yet)"]
    if v.not_done:
        out.append("Not done:")
        out += [f"- {row}" for row in v.not_done]
    decisions, calls, searches = v.left
    out.append(f"Budget left: {decisions} decisions · {calls} model calls · {searches} searches")
    label = {
        "message": "message",
        "plan": "planning feedback",
        "answer": "answer to your question",
        "start": "use current settings",
        "continue": '"continue"',
    }.get(v.kind, v.kind)
    out += [f"Latest message ({label}):", v.text]
    return "\n".join(out)


def _brief_json(brief: ConversationBrief, spec: WorkSpec | None) -> dict[str, Any]:
    def line(item: Line | None) -> dict[str, str] | None:
        return {"text": item.text, "mark": item.mark} if item else None

    return {
        "brief": {
            "goal": line(brief.goal),
            "audience": line(brief.audience),
            "constraints": [line(x) for x in brief.constraints],
            "open": [line(x) for x in brief.open],
            "emphasize": [x.model_dump(mode="json", by_alias=True) for x in brief.emphasize],
            "avoid": [x.model_dump(mode="json", by_alias=True) for x in brief.avoid],
        },
        "spec": None
        if spec is None
        else {
            "kind": spec.kind,
            "use": line(spec.use),
            "length": line(spec.length),
            "outputType": line(spec.output_type),
        },
    }
