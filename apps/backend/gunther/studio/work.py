"""Studio operations on a saved artifact conversation."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from gunther.artifact_workspace import (
    CardRec,
    PartRec,
    Work,
    _section_item,
    _stamp,
    _take_check,
    _translate,
    approach_in_pool,
    approach_refs,
    rebind_approach_refs,
)
from gunther.checks import (
    Verdicts,
    audit_verdict,
    claim_text,
    has_stage,
    merge_verdicts,
    verdicts_of,
)
from gunther.citations import citation_numbers
from gunther.evidence import Evidence
from gunther.llm import ModelError
from gunther.llm import unfence as _unfence
from gunther.output_checks import check_saved_part, output_claim_inventory, support_inventory
from gunther.output_shapes import OutputUnit
from gunther.output_types import SHAPES, output_type_for
from gunther.outputs import (
    COMPOSITION_LAYOUTS,
    UNCHANGED,
    BuiltOutput,
    Draft,
    Notes,
    Planned,
    Pool,
    Spec,
    _marked,
    _notes_block,
    document_units,
    fallback_unverified_diagram,
    section_hash,
    split_document,
)
from gunther.schemas import OutlineItem
from gunther.skill_checks import CHARTS
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.drafting import source_images_kept
from gunther.studio.pipeline import StudioAgents
from gunther.studio.reference import DECK_FORMAT, READ_NOTE, STUDY_DECK_FORMAT, report_format
from gunther.studio.stages import decode_stage
from gunther.studio.state import DeckStory, Insight, Memo, Run

logger = logging.getLogger(__name__)


def _single_part(text: str, kind: str, index: int) -> str:
    """Read a complete part with the same boundaries used to save the document."""
    preamble, parts = split_document(text, kind)
    shape = SHAPES[kind]
    unit = shape.unit_label
    if preamble or len(parts) != 1:
        raise ModelError(f"Studio must return one complete {unit} at position {index + 1}.")
    part, = parts
    if shape.node:
        if shape.node(part) is None:
            raise ModelError(f"Studio did not return a valid {unit} at position {index + 1}.")
        return part
    if shape.unit_problem:
        if problem := shape.unit_problem(part):
            raise ModelError(f"{unit.capitalize()} {index + 1}: {problem}")
        return part
    mark, body = "##", part
    if kind == "slides":
        layout = fmt.parse_slide(part).layout or ("title" if index == 0 else "")
        mark = "#" if layout in ("title", "section") else "##"
        marked = _marked(part)
        body = marked.body if marked.scene_line else D.visible_content(part)
        if (layout == "title") != (index == 0):
            raise ModelError("The title layout belongs on the first slide.")
    if not re.match(rf"^{mark}[ \t]+\S", body.lstrip()):
        raise ModelError(f"Studio did not return a valid heading for {unit} {index + 1}.")
    return part


def slide_in_deck_format(text: str, cover: str, index: int) -> str:
    """Give a revised slide a marker that the saved deck's renderer understands."""
    if not cover:
        return text
    attrs = fmt.parse_slide(cover).attrs
    if index == 0:
        return fmt.with_marker(text, "title", **attrs)
    slide = fmt.parse_slide(text)
    if attrs.get("design") == fmt.DESIGN:
        if slide.layout in CHARTS:
            return fmt.with_marker(text, "chart", chart=slide.layout.removesuffix("_chart"))
        return text
    if slide.layout in ("chart", *CHARTS):
        rows, _ = fmt.read_table(slide.body)
        # The legacy renderer draws just one value column per label.
        if any(len(row) > 2 for row in rows):
            return fmt.with_marker(text, "compare")
    if not slide.layout or slide.layout in COMPOSITION_LAYOUTS["standard"]:
        return text
    if slide.layout == "chart":
        layout = "line_chart" if slide.attrs.get("chart") == "line" else "bar_chart"
        return fmt.with_marker(text, layout)
    if slide.layout == "table":
        return fmt.with_marker(text, "compare")
    if slide.layout == "section":
        text = "#" + _marked(text).body  # The legacy body uses a second-level heading.
    return fmt.with_marker(text, "text")


PART_WRITE = """\
You are the best writer at a top consulting firm, working on one {unit} of an existing \
{what}. The whole document is shown so the {unit} fits it: same voice, no repetition, the \
right link to its neighbours.

Brief: {brief}
Audience: {audience}.{use_line}

{task}

{voice}

{truth}

{format}

Reply with only the {unit}, complete, in the format above."""


SCIENCE_PART_WRITE = """\
You are working on one slide of an existing scientific explanation deck: it teaches a study \
to someone who wants to understand it. The whole deck is shown so the slide fits it: same \
voice, no repetition, the right link to its neighbours.

Brief: {brief}
Audience: {audience}. Use: {use}.

{task}

{voice}

{truth}

{format}

The study map and the figure readings (a figure without a reading was not looked at):
{study}

Reply with only the slide, complete, in the format above."""


class ReviewAsk(BaseModel):
    part: int
    instruction: str


class ReviewPlan(BaseModel):
    understood: str = ""
    changes: list[ReviewAsk] = Field(default_factory=list)


REVIEW = """\
You are a demanding senior partner reading a finished {what} before it goes to the client. \
Brief: {brief}. Audience: {audience}.

Name at most {most} parts that most need improving for this audience — weak or vague titles, \
a part that does not prove its title, repetition, a missing link in the argument, too much \
text, a layout that does not fit the evidence — and for each say exactly what to change. Do \
not ask for facts the document does not have. If it is ready, ask for nothing.

Reply with a JSON object: {{"understood": "the core message in one sentence", "changes": \
[{{"part": number, "instruction": "..."}}]}}"""


class StudioWorkAgents(StudioAgents):
    def __init__(
        self, gateway, kind: str, limits=None, *, tools=(),
        asset_reader=None, reusable_figures=None,
    ) -> None:
        super().__init__(
            gateway, limits, asset_reader=asset_reader, reusable_figures=reusable_figures,
        )
        self.kind = kind
        self.tools = {tool.name: tool for tool in tools}

    def _science_run(self, spec: Spec, saved: dict | None = None) -> Run | None:
        data = (self.scientific_context if saved is None else saved) or {}
        if spec.kind != "slides" or data.get("purpose") != "explain_study":
            return None
        study = decode_stage(S.StudyMap, data.get("study_map"))
        if study is None:
            return None
        figures = data.get("figure_readings")
        return Run(
            spec,
            self.use or "talk",
            purpose=S.Purpose(
                purpose="explain_study", reason=data.get("purpose_reason") or "Kept from this deck"
            ),
            study=study,
            coverage_plan=decode_stage(S.CoveragePlan, data.get("coverage_plan")),
            figure_readings=[
                reading for row in (figures if isinstance(figures, list) else [])
                if (reading := decode_stage(S.FigureReading, row)) is not None
            ],
            limitations=list(data.get("limitations") or []),
        )

    def understand_work(
        self, model, effort, spec, pool, notes, emit, purpose, *,
        manifest=None, resume=None, checkpoint=None,
    ):
        prepared = self.prepare(
            model, effort, spec, pool, notes, manifest=manifest or {}, resume=resume,
            emit=emit, checkpoint=checkpoint, purpose=purpose,
        )
        return self._saved_approach(prepared.run, pool)

    def replan_work(
        self, model, effort, spec, pool, notes, emit, saved: dict, cards: Sequence[CardRec],
        outline: Sequence[Planned], instruction: str,
    ) -> tuple[str, list[Planned], dict, bool]:
        refs = set(approach_refs(saved))
        refs.update(ref for card in cards for ref in card.refs)
        refs.update(ref for part in outline for ref in part.refs)
        run = self._science_run(spec, saved)
        # Passage numbers keep their identity across turns, including source revisions.
        refreshed = bool(refs - pool.by_ref().keys()) or bool(
            run and (
                (run.study and S.map_problems(run.study, pool.items))
                or (run.coverage_plan and S.coverage_problems(run.coverage_plan, pool.items))
            )
        )
        if refreshed:
            prepared = self.prepare(
                model, effort, spec, pool, notes, manifest={}, resume=None,
                emit=emit, checkpoint=None, purpose=saved.get("purpose"),
            )
            run, material = prepared.run, prepared.material
        else:
            material = self._notes_text(notes) + self._material(pool.items)
        if run is None:
            memo = decode_stage(Memo, saved.get("memo")) or Memo(
                answer=saved.get("answer", ""),
                insights=[
                    Insight(claim=card.claim, so_what=card.note, refs=card.refs)
                    for card in cards
                ],
            )
            run = Run(
                spec, (self.use or "talk") if spec.kind == "slides" else "",
                memo=memo,
            )
        run.story = self.storyline(
            model, effort, run, material, emit, previous_outline=outline, instruction=instruction,
            preview_outline=False,
        )
        if isinstance(run.story, DeckStory):
            planned = [
                Planned(s.title.strip(), s.job, id=s.id or "", layout=s.layout or "",
                        refs=tuple(s.refs), notes=s.notes)
                for s in run.story.slides
            ]
        else:
            planned = [
                Planned(s.heading.strip(), s.job, id=s.id or "", refs=tuple(s.refs), notes=s.notes)
                for s in run.story.parts(run.output_type.summary_box)
            ]
        if any(len(item.heading.splitlines()) != 1 for item in planned):
            raise ModelError("Outline headings must be single lines.")
        for item in planned:
            OutlineItem(heading=item.heading, goal=item.goal, id=item.id)
        return run.story.title, planned, self._saved_approach(run, pool, len(planned)), refreshed

    def final_notes(self, spec, planned, drafts, pool) -> list[dict[str, Any]]:
        """Form problems left in the finished whole, as notes for the version."""

        if spec.kind == "slides":
            deck = "\n\n---\n\n".join(d.text for d in drafts)
            scientific = self._science_run(spec)
            found = SHAPES[spec.kind].format_check(
                deck,
                read=self.use == "read",
                notes_required=False,
                scientific=bool(scientific),
            )
            notes = [
                {"step": "format", "label": f"Slide {n}", "reason": problems[0]}
                for n, problems in found
            ]
            if scientific and scientific.study:
                # The finished deck after edits: a central figure an edit removed, a damaged
                # value or an image from outside the build's material is said with the version.
                notes += [
                    {"step": "science", "label": "Scientific check", "reason": problem}
                    for problem in S.code_problems(
                        scientific.study,
                        scientific.figure_readings or [],
                        deck,
                        pool.items,
                        self._allowed(),
                        plan=scientific.coverage_plan,
                    )
                ] + [
                    {"step": "science", "label": "Cover metadata", "reason": problem}
                    for problem in S.cover_problems(
                        deck, pool.items
                    )
                ]
            return notes
        return []

    def _allowed(self) -> list[str] | None:
        """The build's own source images, when the deck kept them (older decks did not)."""

        return (self.scientific_context or {}).get("allowed_assets")

    def _science_problems(self, text: str, pool: Pool, scientific: Run) -> list[str]:
        """What code checks on one edited slide: damaged values left out, scope kept."""

        return S.value_problems(
            text, pool.items, scientific.figure_readings or []
        ) + S.crop_problems(text, scientific.figure_readings or []) + S.scope_problems(
            text, pool.items, self._allowed()
        )

    def record(self) -> dict[str, Any]:
        return {"name": "studio", "version": 1, "skipped": self.skipped, "trail": self.trail}

    @staticmethod
    def _what(spec: Spec) -> str:
        return SHAPES[spec.kind].what

    # What WorkSteps calls for edits and checks --------------------------------------------

    def _checked_layout(self, draft: Draft, section: int) -> Draft:
        draft = self._chart_fallback(draft, section)
        return fallback_unverified_diagram(draft, section, self.skipped)

    def check_built(self, model, text: str | OutputUnit, pool: Pool, section) -> Draft:
        checked = self._claims(model, text, pool.by_ref(), kind=self.kind)
        raw = text.text if isinstance(text, OutputUnit) else text
        return self._checked_layout(Draft(raw, checked), section)

    def check_unbuilt(self, model, text, by_ref, prior=None) -> Verdicts:
        if has_stage(prior or {}, "audit", "support"):
            return check_saved_part(
                self.ask, model, text, by_ref, prior, limit=self.limits.sentences, kind=self.kind,
            )
        findings = {k: row for k, row in (prior or {}).items() if row["stage"] == "finding"}
        return findings | self._claims(model, text, by_ref, prior=prior, kind=self.kind)

    def check_edit(
        self, model, text: str, pool: Pool, part: PartRec, section: int, lines: range | None,
    ) -> Draft:
        prior = part.verdicts or {}
        by_ref, body = pool.by_ref(), _marked(text).body
        if SHAPES[self.kind].pipeline == "direct":
            # Projected claims can combine several lines of the original unit.
            lines = None
        _, active = output_claim_inventory(
            self.kind, text, by_ref, rest="", scientific=bool(self.scientific_context),
        )
        if has_stage(prior, "audit"):
            active |= verdicts_of(audit_verdict(body, "unchecked"))
        if has_stage(prior, "support", "audit"):
            active |= support_inventory(body, by_ref)
        changed = {key: row for key, row in active.items()
                   if row["stage"] == "checker" and (lines is None or row["line"] in lines)}
        retained = {claim_text(line) for n, line in enumerate(body.splitlines())
                    if lines is not None and n not in lines}
        retained.update(claim_text(row["text"]) for key, row in active.items()
                        if row["stage"] == "checker" and key not in changed)
        active.update({key: row for key, row in prior.items()
                       if row["stage"] == "finding" and claim_text(row["text"]) in retained})
        checked = self._claims(model, text, by_ref, prior=prior, inventory=changed, kind=self.kind)
        active.update(checked)
        return self._checked_layout(Draft(text, merge_verdicts(active, prior, checked)), section)

    def validate_evidence(self, spec: Spec, text: str, pool: Pool) -> None:
        scientific = self._science_run(spec)
        problems = S.scope_problems(text, pool.items)
        if scientific:
            problems += self._science_problems(text, pool, scientific)
        if problems:
            raise ModelError(problems[0])

    def validated_part(self, spec: Spec, text: str, index: int, pool: Pool) -> str:
        text = _single_part(text, spec.kind, index)
        self.validate_evidence(spec, text, pool)
        return text

    def fit(self, model, effort, spec, index, pool, text):
        """Try fitting once and keep the valid writer text if fitting fails."""

        if spec.kind != "slides":
            return text
        use = self.use or "talk"
        slide = fmt.parse_slide(text)
        problems = fmt.check_slide(slide, first=index == 0, read=use == "read")
        if not problems:
            return text
        scientific = self._science_run(spec)
        system = P.FIX_SLIDES.format(
            format=(STUDY_DECK_FORMAT if scientific else DECK_FORMAT)
            + ("\n\n" + READ_NOTE if use == "read" else ""),
            problems=f"Slide {index + 1} problems:\n"
            + "\n".join(f"- {p}" for p in problems)
            + f"\n\nSlide {index + 1} as written:\n{text}",
        )
        if scientific:
            system += "\n" + P.SCIENCE_TRUTH
        try:
            fixed = _unfence(self._text(
                model, system, P.with_preferences("Rewrite it now.", spec), effort, 4000,
            ))
            candidate = _single_part(fmt.strip_labels(fixed), spec.kind, index)
        except ModelError as error:
            _single_part(text, spec.kind, index)
            logger.info("Fitting a slide failed: %s", error)
            return text
        if not source_images_kept(text, candidate) or S.scope_problems(candidate, pool.items):
            return text
        if scientific and self._science_problems(candidate, pool, scientific):
            # Fitting may not bring in what the edit was checked to leave out.
            return text
        return candidate

    # Writing one part, reviewing the whole ------------------------------------------------

    def write_part(
        self,
        model,
        effort,
        spec: Spec,
        parts: list[str],
        headings: list[str],
        index: int,
        pool: Pool,
        *,
        goal: str = "",
        layout: str = "",
        writer_notes: str = "",
        instruction: str | None = None,
        notes: Notes | None = None,
        refs: Sequence[int] = (),
        approach: dict | None = None,
        on_text=None,
    ) -> str:
        deck = spec.kind == "slides"
        shape = SHAPES[spec.kind]
        unit = shape.unit_label
        use = self.use or "talk"
        # The words are written from the words: a slide's design is not shown to the writer.
        parts = [fmt.without_design(text) for text in parts]
        current = parts[index] if index < len(parts) else ""
        if shape.node and current:
            end = shape.subtree_end(parts, index)
            current = shape.join("", parts[index:end])
        whole = "\n\n---\n\n".join(
            f"[{unit} {n}]\n{text}"
            if text
            else f"[{unit} {n}] (not written yet: {headings[n - 1]})"
            for n, text in enumerate(parts, 1)
        )
        if instruction and current:
            task = (
                f"Rewrite {unit} {index + 1} as instructed, changing only what the instruction "
                f"needs: {instruction}\nIf this {unit} needs no change, reply {UNCHANGED}."
                f"\n\nThe {unit} now:\n{current}"
            )
        else:
            task = (
                f"Write {unit} {index + 1}. Its title: {headings[index]}."
                + (f" Its job: {goal}." if goal else "")
                + (f" Layout: {layout}." if deck and layout else "")
            )
        if writer_notes:
            task += "\nWriter notes: " + writer_notes
        cited = [*refs, *citation_numbers(shape.markup(whole), bare_only=True)]
        material = self._part_material(pool, cited, f"{headings[index]} {goal} {instruction or ''}")
        scientific = self._science_run(spec)
        model_language = (
            scientific.model_language if scientific
            else ((approach or {}).get("memo") or {}).get("language")
        )
        voice = f"{P.VOICE} {P.language_instruction(spec.language, model_language)}"
        output_type = output_type_for(spec.kind, spec.output_type)
        if deck:
            guide = DECK_FORMAT + ("\n\n" + READ_NOTE if use == "read" else "")
        elif shape.pipeline == "direct":
            guide = output_type.write + (
                f"\nReturn the selected {unit} and its complete replacement subtree only. "
                "Start that subtree's root at the left margin and omit the document header."
                if shape.node else f"\nReturn one complete {unit} without a document header."
            )
        else:
            guide = report_format(output_type.summary_box) + "\n\n" + output_type.write
        system = PART_WRITE.format(
            unit=unit,
            what=self._what(spec),
            brief=spec.brief or "(none)",
            audience=spec.audience,
            use_line=f" Use: {use}." if deck else "",
            task=task,
            voice=voice,
            truth=P.TRUTH,
            format=guide,
        )
        if scientific:
            system = SCIENCE_PART_WRITE.format(
                brief=spec.brief or "(none)",
                audience=spec.audience,
                use=use,
                task=task,
                voice=voice,
                truth=P.TRUTH + "\n\n" + P.SCIENCE_TRUTH,
                format=STUDY_DECK_FORMAT + ("\n\n" + READ_NOTE if use == "read" else ""),
                study=self._study_text(scientific),
            )
        context = "\n".join(_notes_block(notes)) if notes else ""
        prompt = (
            f"The whole {self._what(spec)[2:]}:\n\n{whole}\n{context}"
            f"\n\nThe material:\n\n{material}"
        )
        prompt = P.with_preferences(prompt, spec)

        def written(prompt: str) -> str:
            text = _unfence(self._text(model, system, prompt, effort, 6000, on_text=on_text))
            if instruction and current and text.upper() == UNCHANGED:
                return current
            if shape.node:
                if not current:
                    raise ModelError("Build the whole output before writing a subtree.")
                candidate = shape.join(shape.preamble(output_type.id), [text])
                problems = output_type.direct_problems(candidate)
                if problems:
                    raise ModelError("\n".join(problems))
                _, replacement = split_document(text, spec.kind)
                return shape.join("", shape.at_depth(parts[index], replacement))
            text = _single_part(fmt.strip_labels(text) if deck else text, spec.kind, index)
            if deck and index == 0 and current:
                text = fmt.with_marker(text, "title", **fmt.parse_slide(current).attrs)
            return text

        text = written(prompt)
        # What code checks on a whole deck, on this slide: damaged values left out and images
        # from the build's material. Asked again once, then the edit fails.
        if scientific and text != current and (
            found := self._science_problems(text, pool, scientific)
        ):
            text = written(
                prompt
                + f"\n\nYour {unit}:\n{text}\n\nFix this and reply with the whole {unit}:\n"
                + "\n".join(f"- {p}" for p in found)
            )
            if found := self._science_problems(text, pool, scientific):
                raise ModelError("The edited slide breaks a scientific rule: " + found[0])
        return text

    def rewrite_passage(self, model, effort, spec: Spec, schema, *, system: str, prompt: str):
        """Return the checked replacement for a selected passage."""

        replacement, _ = self.gateway.complete_json(
            model, schema, system=system, prompt=P.with_preferences(prompt, spec), effort=effort,
        )
        return replacement

    def _part_material(self, pool: Pool, cited: list[int], about: str) -> str:
        """The passages a part may need: those the document cites, then the ones sharing the
        most words with what the part is about, within the writer's budget."""

        by_ref = pool.by_ref()
        words = {w for w in re.findall(r"\w{4,}", about.casefold())}

        def score(item: Evidence) -> int:
            return len(words & set(re.findall(r"\w{4,}", item.text.casefold())))

        order = list(dict.fromkeys(r for r in cited if r in by_ref))
        order += [
            item.ref
            for item in sorted(pool.items, key=score, reverse=True)
            if item.ref not in order
        ]
        chosen, size = [], 0
        for ref in order:
            text = self._passage(by_ref[ref], 3000)
            if size + len(text) > 60_000:
                break
            chosen.append(ref)
            size += len(text)
        return "\n\n".join(self._passage(by_ref[ref], 3000) for ref in sorted(chosen))

    def review_parts(
        self, model, effort, spec: Spec, parts: list[str], most: int = 4
    ) -> list[ReviewAsk]:
        unit = SHAPES[spec.kind].unit_label
        parts = [fmt.without_design(text) for text in parts]
        whole = "\n\n---\n\n".join(f"[{unit} {n}]\n{text}" for n, text in enumerate(parts, 1))
        system = REVIEW.format(
            what=self._what(spec), brief=spec.brief or "(none)", audience=spec.audience, most=most
        )
        scientific = self._science_run(spec)
        if scientific:
            system = (
                "Review this scientific explanation deck. Name at most "
                + str(most)
                + " slides needing changes. Check the question/contribution, mechanism, central "
                "experiments, figure axes/controls, evidence for conclusions and limits. Reject "
                "overclaims, incompatible comparisons and omitted central experiments. "
                "No action-title or recommendation requirements. Return ReviewPlan: understood, "
                "changes [{part, instruction}].\n"
                + P.SCIENCE_TRUTH
                + "\nStudy map and image readings:\n"
                + self._study_text(scientific)
            )
        try:
            plan, _ = self.gateway.complete_json(
                model, ReviewPlan, system=system, prompt=P.with_preferences(whole, spec),
                effort="medium",
            )
        except ModelError as error:
            self.skipped.append({
                "step": "review", "label": "Review unavailable", "reason": str(error),
            })
            raise
        return [c for c in plan.changes if 1 <= c.part <= len(parts)][:most]


def load_built(work: Work, built: BuiltOutput, made: Pool, pool: Pool, brief) -> None:
    """A studio build into ``work``: approach, outline and parts, in the conversation pool's
    numbers. Only the passages the build cites join the conversation pool (``made`` is the
    larger pool the studio read; ``built.pool_refs`` are its numbers)."""

    sections = document_units(built.content, work.spec.kind)
    plans = [OutlineItem.model_validate(row) for row in built.outline]
    ids = [plan.id for plan in plans if plan.id]
    if len(sections) != len(plans) or len(ids) != len(set(ids)):
        raise ModelError("The assembled artifact does not match its outline.")

    updated_pool = Pool(pool.limit)
    updated_pool.restore(pool.items)
    updated_pool.top = pool.top
    by_ref = made.by_ref()
    version_map = [updated_pool.adopt(by_ref[ref]).ref for ref in built.pool_refs]
    scientific_map = {}
    if (built.approach or {}).get("purpose") == "explain_study":
        # The argument may need uncited passages during a later edit. Keep its scoped evidence.
        scientific_map = {ref: updated_pool.adopt(item).ref for ref, item in by_ref.items()}
    checks = {item["hash"]: item for item in built.checks}
    approach = dict(built.approach or {})
    if scientific_map:
        approach.pop("ref_map", None)
        approach = rebind_approach_refs(approach, scientific_map)
    else:
        approach = approach_in_pool(approach, version_map)
    outline, parts = [], {}
    next_item = work.next_item
    reserved = set(ids)
    shape = SHAPES[work.spec.kind]
    for position, (section, plan) in enumerate(zip(sections, plans, strict=True)):
        text, _ = _translate(shape.markup(section.text), version_map)
        while f"p{next_item}" in reserved:
            next_item += 1
        item = _section_item(
            work.spec.kind, position, section, text, plan.id or f"p{next_item}", plan,
        )
        next_item += 1
        part = PartRec(text=text, inputs="")
        _take_check(part, section, checks.get(section_hash(section)), version_map)
        outline.append(item)
        parts[item.id] = part
    work.title, work.approach = built.title, approach
    work.outline, work.parts = outline, parts
    work.version_map, work.next_item = list(version_map), next_item
    pool.items, pool.known, pool.top = updated_pool.items, updated_pool.known, updated_pool.top
    work.approach_top = pool.top
    work.skipped = [*work.skipped, *[dict(s) for s in (built.skill or {}).get("skipped", [])]]
    _stamp(work, brief)
