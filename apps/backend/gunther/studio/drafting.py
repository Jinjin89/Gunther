"""Studio memos, storylines, slide and report drafts, and format repair."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from gunther.llm import ModelError
from gunther.llm import unfence as _unfence
from gunther.markup import markdown_lines
from gunther.output_document import project_outline
from gunther.output_types import SHAPES
from gunther.outputs import BuiltOutput, Events, Planned, Pool, split_document
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.reference import DECK_FORMAT, READ_NOTE, STUDY_DECK_FORMAT, report_format
from gunther.studio.requests import WORKERS, material_request, warm_map
from gunther.studio.state import (
    DeckStory,
    Memo,
    ReportStory,
    Review,
    Run,
    StorySlide,
    _keeps_heading,
    _keeps_layout,
    _matches_plan,
    _validate_count,
    _validate_storyline,
)

logger = logging.getLogger("gunther.studio.pipeline")

CRITIC_ROUNDS = 1  # one review and one revision: a second round rarely changed the story

LENGTH_TARGETS = {
    "report": {
        "short": "800–1,200 words", "standard": "1,500–3,000 words", "long": "3,000–5,000 words",
    },
    "slides": {"short": "5–7 slides", "standard": "8–12 slides", "long": "12–14 slides"},
}


def _outline_text(plan: Sequence[Planned]) -> str:
    return json.dumps(
        [
            {"id": p.id, "heading": p.heading, "goal": p.goal, "layout": p.layout or None,
             "notes": p.notes}
            for p in plan
        ],
        ensure_ascii=False,
    )


def source_images_kept(before: str, after: str) -> bool:
    pattern = r"!\[[^\]]*\]\(gunther-asset:([A-Za-z0-9_-]+(?:#[^)\s]+)?)\)"
    return set(re.findall(pattern, before)) <= set(re.findall(pattern, after))


def replace_layout(slide: StorySlide, layout: str) -> StorySlide:
    return slide.model_copy(update={"layout": layout})


def _report_story_text(story: ReportStory, summary_box: bool) -> str:
    lines = [f"Title: {story.title}"]
    for number, part in enumerate(story.parts(summary_box), 1):
        line = f"{number}. {part.heading} — job: {part.job}"
        if part.exhibits:
            line += f" — exhibits: {'; '.join(part.exhibits)}"
        if part.notes:
            line += f" — writer notes: {part.notes}"
        lines.append(line + " " + "".join(f"[{r}]" for r in part.refs))
        if number == 1 and summary_box:
            lines.extend(f"  - {point}" for point in story.summary_points)
    return "\n".join(lines)


def report_exhibit_counts(run: Run) -> list[int]:
    story = run.story
    if not isinstance(story, ReportStory):
        return []
    return [
        sum(bool(exhibit.strip()) for exhibit in part.exhibits)
        for part in story.parts(run.output_type.summary_box)
    ]


class StudioDrafting:
    # 2. Memo ------------------------------------------------------------------------------

    def memo(self, model, effort, run: Run, material: str, emit: Events) -> Memo:
        self._stage(emit, "memo", "Finding what is worth saying", "running")
        spec = run.spec
        shape = SHAPES[spec.kind]
        system = P.MEMO.format(
            what=shape.what,
            brief=spec.brief or "(none: choose the most valuable question the material answers)",
            audience=spec.audience,
            use_line=f"Use: {run.use}." if run.use else "",
            kind=shape.name,
        )
        memo = self._json(model, Memo, *material_request(material, system, spec), effort)
        self._stage(emit, "memo", "Finding what is worth saying", "done", memo.answer[:200])
        emit({"type": "memo", "memo": memo.model_dump()})
        return memo

    def _memo_text(self, memo: Memo) -> str:
        lines = [f"Answer: {memo.answer}", "Insights:"]
        lines += [
            f"- {item.claim} {''.join(f'[{r}]' for r in item.refs)} — so what: {item.so_what}"
            for item in memo.insights
        ]
        lines.append("Key facts:")
        lines += [
            f"- {item.fact}: {item.value}"
            + (f" (vs {item.compared_to})" if item.compared_to else "")
            + " "
            + "".join(f"[{r}]" for r in item.refs)
            for item in memo.facts
        ]
        if memo.tensions:
            lines.append("Tensions and unknowns:")
            lines += [f"- {t.issue} {''.join(f'[{r}]' for r in t.refs)}" for t in memo.tensions]
        if memo.questions:
            lines.append("What the audience will ask:")
            lines += [f"- {q}" for q in memo.questions]
        return "\n".join(lines)

    # 3. Storyline -------------------------------------------------------------------------

    def _length(self, run: Run) -> str:
        if run.spec.length:
            return f"aim for {LENGTH_TARGETS[run.spec.kind][run.spec.length]}"
        if run.spec.kind == "slides":
            if run.study:
                return (
                    "as many slides as needed to teach the central method and source figures"
                    "; split complex figures into explanatory sequences, never shrink evidence"
                )
            return "as many slides as the argument needs, usually 8–12; never pad"
        return "as long as the argument needs, usually 1,500–3,000 words in 4–7 sections"

    def storyline(
        self, model, effort, run: Run, material: str, emit: Events, *,
        previous_outline: Sequence[Planned] = (), instruction: str = "",
        preview_outline: bool = True, reviewed_inputs: str = "",
    ) -> DeckStory | ReportStory:
        spec = run.spec
        output_type = run.output_type
        shape = SHAPES[output_type.shape]
        self._stage(emit, "story", "Planning the storyline", "running")
        memo = (
            self._study_text(run) if run.study else (self._memo_text(run.memo) if run.memo else "")
        )
        if spec.kind == "slides":
            schema = DeckStory
            system = (P.STUDY_STORY if run.study else P.DECK_STORY).format(
                use=run.use,
                audience=spec.audience,
                brief=spec.brief or "(none)",
                pages=self._length(run),
                use_word=run.use,
                use_rule=P.USE_RULES[run.use],
                layout_guide=P.LAYOUT_GUIDE,
                memo=memo,
            )
        else:
            schema = ReportStory
            system = P.REPORT_STORY.format(
                audience=spec.audience,
                brief=spec.brief or "(none)",
                pages=self._length(run),
                memo=memo,
                recipe=output_type.plan,
            )
            if output_type.summary_box:
                system += (
                    "\nKeep the summary in the summary fields. It is the first counted section; "
                    "return its writer notes in summary_notes, its evidence in summary_refs "
                    "and evidence the summary depends on in summary_required_refs. "
                    "Use sections for the following rows only."
                )
            else:
                system += "\n\n" + P.WITHOUT_SUMMARY
        first = output_type.first_part_role or "first section"
        system += "\n\n" + P.STORY_LENGTH.format(
            parts="slides" if spec.kind == "slides" else "report sections",
            first=first.lower(),
        )
        system += (
            f"\nThe outline can have up to {output_type.part_limit} {shape.unit_label}s. "
            "Return each row's evidence numbers in refs. In required_refs, identify the "
            "subset needed to support the row's main claim or exhibit."
        )
        system += "\n" + P.language_instruction(spec.language, run.model_language)
        if run.plan:
            system += (
                "\nThe approved outline sets the count and order, superseding earlier counts.\n"
                "The approved outline, in order:\n" + _outline_text(run.plan)
            )
        if previous_outline:
            summary_id = (
                "Return the report summary's id as summary_id. "
                if output_type.summary_box else ""
            )
            system += (
                "\nThe existing outline, in order. Preserve headings, goals and order where "
                "they still fit the brief and the requested change. Return the existing id for "
                "each continuing part, even if its title or position changes. Use null for a new "
                f"part and omit removed parts. {summary_id}"
                "Keep only claims supported by the selected material.\n"
                + _outline_text(previous_outline)
            )
        if instruction:
            system += "\nRequested change to the outline: " + instruction
        if run.reviewed:
            system += "\n" + P.OUTLINED_PROPOSAL + reviewed_inputs
        figures = self._figures(material)
        system, prompt = material_request(material, system + figures, spec)
        story = self._json(model, schema, system, prompt, effort)
        length = story.model_dump(include={"requested_count", "requested_minutes"})
        run.reviews = []
        for round_ in range(CRITIC_ROUNDS):
            review = self.critique(model, run, story, material)
            run.reviews.append(review)
            emit(
                {
                    "type": "review",
                    "round": round_ + 1,
                    "verdict": review.verdict,
                    "understood": review.understood,
                    "problems": [p.model_dump() for p in review.problems],
                }
            )
            if review.verdict.strip().lower() in ("ready", "unavailable") or not review.problems:
                break
            again = (
                "Revise this scientific storyline; preserve its scientific handoffs.\n{review}"
                if run.study
                else P.STORY_AGAIN
            ).format(
                review="\n".join(f"- [{p.where}] {p.problem} → {p.fix}" for p in review.problems)
            )
            try:
                story = self._json(
                    model,
                    schema,
                    system,
                    prompt + "\n\nYour storyline:\n" + story.model_dump_json() + "\n\n" + again,
                    "low",
                )
            except ModelError as error:
                logger.info("Revising the storyline failed: %s", error)
                self.skipped.append({
                    "step": "story", "label": "Storyline revision unavailable",
                    "reason": str(error),
                })
                break
        if isinstance(story, DeckStory):
            story = self._tidy_deck_story(story, automatic_layout=run.reviewed)
        story = story.model_copy(update=length)
        if not run.reviewed:
            _validate_storyline(run, story)
        self._stage(emit, "story", "Planning the storyline", "done", story.title)
        emit({"type": "storyline", "storyline": story.model_dump()})
        if not preview_outline:
            return story
        # The planned titles, as the live outline the app already shows while a build runs.
        planned = (
            [{"heading": s.title, "goal": s.job} for s in story.slides]
            if isinstance(story, DeckStory)
            else [
                {"heading": s.heading, "goal": s.job}
                for s in story.parts(output_type.summary_box)
            ]
        )
        if run.plan:
            planned = project_outline(run.plan, include_ids=False, include_layout=False)
        emit({"type": "outline", "title": story.title, "sections": planned})
        return story

    @staticmethod
    def _tidy_deck_story(story: DeckStory, *, automatic_layout: bool = False) -> DeckStory:
        slides = [
            slide if slide.layout in fmt.LAYOUTS or (automatic_layout and slide.layout is None)
            else replace_layout(slide, "points")
            for slide in story.slides
        ]
        if slides[0].layout != "title" and not (automatic_layout and slides[0].layout is None):
            slides.insert(0, StorySlide(title=story.title, layout="title", job=story.subtitle))
        for index, slide in enumerate(slides[1:], 1):
            if slide.layout == "title":
                slides[index] = replace_layout(slide, "statement")
        return story.model_copy(update={"slides": slides})

    def critique(self, model, run: Run, story, material: str) -> Review:
        spec = run.spec
        output_type = run.output_type
        if run.study:
            try:
                return self.scientific_review(model, run, story.model_dump_json(), material)
            except ModelError as error:
                logger.info("The storyline review failed: %s", error)
                self.skipped.append(
                    {"step": "story", "label": "Storyline review unavailable", "reason": str(error)}
                )
                return Review(verdict="unavailable")
        answer = run.memo.answer if run.memo else "(not given)"
        if isinstance(story, DeckStory):
            story_text = "\n".join(
                f"{n}. [{s.layout}] {s.title}  — job: {s.job}"
                for n, s in enumerate(story.slides, 1)
            )
        else:
            story_text = _report_story_text(story, output_type.summary_box)
        system = P.CRITIC.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            answer=answer,
            story=story_text,
            recipe=f"\n\n{output_type.plan}" if output_type.plan else "",
        )
        try:
            return self._json(
                model, Review,
                *material_request(material, system + "\n\nReview it now.", spec), "low",
            )
        except ModelError as error:
            logger.info("The storyline review failed: %s", error)
            self.skipped.append(
                {"step": "story", "label": "Storyline review unavailable", "reason": str(error)}
            )
            return Review(verdict="unavailable")

    @staticmethod
    def _figures(material: str) -> str:
        found = re.findall(r"!\[[^\]]*\]\(gunther-asset:[A-Za-z0-9_-]+\)", material)
        if not found:
            return "\n\nNo figures are available: do not use the figure layout."
        return "\n\nFigures available (copy exactly):\n" + "\n".join(sorted(set(found)))

    # 4. Writing ---------------------------------------------------------------------------

    def write_deck(self, model, effort, run: Run, pool: Pool, material: str, emit: Events) -> str:
        spec = run.spec
        story = run.story
        assert isinstance(story, DeckStory)
        self._stage(emit, "write", "Writing the slides", "running")
        story_text = "\n".join(
            [f"Deck title: {story.title}", f"Subtitle: {story.subtitle}"]
            + [
                f"{n}. [{s.layout or 'automatic'}] {s.title} — job: {s.job} "
                f"{''.join(f'[{r}]' for r in s.refs)}"
                for n, s in enumerate(story.slides, 1)
            ]
        )
        theme = spec.theme if spec.theme in fmt.THEMES else "paper"
        if run.study:
            story_text = story.model_dump_json()
        system = (P.STUDY_WRITE if run.study else P.DECK_WRITE).format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            use=run.use,
            use_rule=P.USE_RULES[run.use],
            language=P.language_instruction(spec.language, run.model_language),
            format=(STUDY_DECK_FORMAT if run.study else DECK_FORMAT)
            + ("\n\n" + READ_NOTE if run.use == "read" else ""),
            memo=self._study_text(run)
            if run.study
            else (self._memo_text(run.memo) if run.memo else ""),
            story=story_text,
            theme=theme,
        )
        if run.plan:
            system += "\nUse the approved slide titles in order:\n" + _outline_text(run.plan)
            if run.reviewed:
                system += "\n" + P.APPROVED_OUTLINE
        if story.requested_minutes is not None:
            system += (
                f"\nWrite speaker notes for a talk lasting {story.requested_minutes:g} minutes."
            )
        if run.study:
            system += "\n" + S.PANEL_ASSIGNMENTS
        system, prompt = material_request(material, system + self._figures(material), spec)
        text = self._text(model, system, prompt, effort, max_tokens=24_000)
        deck = _unfence(text)
        slides = fmt.split_slides(deck)
        if len(slides) < len(run.plan or story.slides):
            # Ran out of room: ask for the rest, from where it stopped.
            more = self._text(
                model,
                system,
                prompt
                + "\n\nYou wrote these slides so far:\n\n"
                + deck
                + f"\n\nContinue with slide {len(slides) + 1}. Output only the remaining slides.",
                effort,
                max_tokens=16_000,
            )
            slides += fmt.split_slides(_unfence(more))
        slides = self._title_marker([fmt.strip_labels(slide) for slide in slides], theme)
        _validate_count(run, story, len(slides), "written deck")
        if not _matches_plan(run, slides, layouts=False):
            raise ModelError("The written deck did not follow the approved outline.")
        self._stage(emit, "write", "Writing the slides", "done", f"{len(slides)} slides")
        return "\n\n---\n\n".join(slides)

    @staticmethod
    def _title_marker(slides: list[str], theme: str) -> list[str]:
        if not slides:
            return slides
        first = fmt.parse_slide(slides[0])
        attrs = {"design": fmt.DESIGN, "theme": theme}
        accent = first.attrs.get("accent")
        if accent:
            attrs["accent"] = accent
        slides[0] = fmt.with_marker(slides[0], "title", **attrs)
        return slides

    def fix_format(self, model, effort, run: Run, deck: str, emit: Events) -> str:
        """Repair slides in parallel for up to two rounds and keep the renderer's fallback."""

        read = run.use == "read"
        notes = run.use == "talk"
        guide = (STUDY_DECK_FORMAT if run.study else DECK_FORMAT) + (
            "\n\n" + READ_NOTE if read else ""
        )
        errors: dict[int, str] = {}
        for _ in range(2):
            slides = fmt.split_slides(deck)
            found = dict(SHAPES[run.spec.kind].format_check(
                deck, read=read, notes_required=notes, scientific=bool(run.study)
            ))
            for index, slide in enumerate(slides):
                if not _keeps_layout(run, index, slide):
                    layout = run.plan[index].layout
                    requirement = (
                        f"the approved {layout} layout" if layout else "a registered layout"
                    )
                    found.setdefault(index + 1, []).append(f"Use {requirement} for this slide.")
            if not found:
                break

            def fix(
                item: tuple[int, list[str]], slides: list[str] = slides
            ) -> tuple[str | None, str | None]:
                n, problems = item
                system = P.FIX_SLIDES.format(format=guide, problems="")
                if run.study:
                    system += "\n" + P.SCIENCE_TRUTH + "\n" + S.PANEL_ASSIGNMENTS
                prompt = (
                    f"Slide {n} problems:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + f"\n\nSlide {n} as written:\n{slides[n - 1]}"
                    + "\n\nRewrite it now."
                )
                try:
                    # A format fix is mechanical: little thinking, so its 4,000 tokens are
                    # spent on the slide (high thinking ran out of room and asked again).
                    again = fmt.split_slides(
                        _unfence(self._text(
                            model, system, P.with_preferences(prompt, run.spec), "low", 4000,
                        ))
                    )
                except ModelError as error:
                    logger.info("Fixing slide %d failed: %s", n, error)
                    return None, str(error) or "The format repair did not finish."
                if not again or not _keeps_heading(run, slides[n - 1], again):
                    return None, None
                text = fmt.strip_labels(again[0])
                if not source_images_kept(slides[n - 1], text):
                    return None, None
                if n == 1:
                    theme = run.spec.theme
                    if theme not in fmt.THEMES:
                        theme = fmt.parse_slide(slides[0]).attrs.get("theme", "paper")
                    text = self._title_marker([text], theme)[0]
                if not _keeps_layout(run, n - 1, text):
                    return None, None
                return text, None

            found = list(found.items())
            fixed = warm_map(fix, found, WORKERS)
            changed = False
            for (n, _), (text, request_error) in zip(found, fixed, strict=True):
                if request_error is None:
                    errors.pop(n, None)
                else:
                    errors[n] = request_error
                if text is not None and text != slides[n - 1]:
                    slides[n - 1], changed = text, True
            deck = "\n\n---\n\n".join(slides)
            if not changed:
                break
        left = SHAPES[run.spec.kind].format_check(
            deck, read=read, notes_required=run.use == "talk", scientific=bool(run.study)
        )
        if not run.study and any("units long" in p for _, problems in left for p in problems):
            slides = fmt.split_slides(deck)
            for n, problems in left:
                if any("units long" in p for p in problems):
                    slides[n - 1] = fmt.trim_to_notes(slides[n - 1], first=n == 1, read=read)
            deck = "\n\n---\n\n".join(slides)
        run.format_repair_errors = errors
        return deck

    def _record_slide_format(self, run: Run, built: BuiltOutput) -> None:
        """Report the format of the deck being saved, after all repairs and fallbacks."""
        remaining = SHAPES[run.spec.kind].format_check(
            built.content,
            read=run.use == "read",
            notes_required=run.use == "talk",
            scientific=bool(run.study),
        )
        if not remaining:
            return
        notes = []
        for n, problems in remaining:
            label, reason = f"Slide {n} still breaks the format", problems[0]
            if error := run.format_repair_errors.get(n):
                label, reason = f"Slide {n} format repair unavailable", f"{error} {reason}"
            notes.append({"step": "format", "label": label, "reason": reason[:600]})
        self.skipped.extend(notes)

    @staticmethod
    def _report_plan_problems(run: Run, preamble: str, parts: Sequence[str]) -> list[str]:
        if not _matches_plan(run, parts):
            return ["Follow the exact approved section headings and order."]
        problems = []
        if run.output_type.summary_box:
            if any(line.lstrip().startswith(":::") for line, fenced in markdown_lines(preamble)
                   if not fenced):
                problems.append("Place the summary and any callouts inside the approved sections.")
            if not parts or not fmt.report_section_has_content(parts[0]):
                problems.append(
                    "Write the executive summary under the first approved heading "
                    "with visible content."
                )
        for number, (part, count) in enumerate(
            zip(parts, report_exhibit_counts(run), strict=False), 1,
        ):
            if problem := fmt.planned_exhibit_problem(fmt.report_exhibits(part), count, number):
                problems.append(problem)
        return problems

    def write_report(self, model, effort, run: Run, pool: Pool, material: str, emit: Events) -> str:
        spec = run.spec
        output_type = run.output_type
        story = run.story
        assert isinstance(story, ReportStory)
        self._stage(emit, "write", "Writing the report", "running")
        guide = report_format(output_type.summary_box)
        system = P.REPORT_WRITE.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            length=f"Default length when the brief gives none: {self._length(run)}.",
            language=P.language_instruction(spec.language, run.model_language),
            format=guide,
            memo=self._memo_text(run.memo) if run.memo else "",
            story=_report_story_text(story, output_type.summary_box),
            recipe=output_type.write,
        )
        if run.plan:
            system += "\nUse exactly these approved section headings in order.\n" + _outline_text(
                run.plan,
            )
            if output_type.summary_box:
                system += (
                    "\nThe first section is the executive summary. Write it under its approved "
                    "heading, with its goal and writer notes. Keep the preamble to the report "
                    "title and its introductory line."
                )
            if run.reviewed:
                system += "\n" + P.APPROVED_OUTLINE
        system, prompt = material_request(material, system + self._figures(material), spec)
        text = _unfence(self._text(model, system, prompt, effort, max_tokens=24_000))

        def format_problems(report: str) -> list[str]:
            return SHAPES[spec.kind].format_check(
                report, summary_box=output_type.summary_box,
            ) + self._report_plan_problems(run, *split_document(report, "report"))

        problems = format_problems(text)
        if problems:
            # One revision of the whole report for what its form gets wrong.
            fix = P.FIX_REPORT.format(problems="\n".join(f"- {p}" for p in problems))
            try:
                fixed = _unfence(
                    self._text(
                        model,
                        system,
                        prompt + "\n\n" + fix + "\n\nYour report:\n\n" + text,
                        effort,
                        max_tokens=24_000,
                    )
                )
                if not self._report_plan_problems(run, *split_document(fixed, "report")):
                    text = fixed
            except ModelError as error:
                logger.info("Fixing the report's form failed: %s", error)
                self.skipped.append({
                    "step": "format", "label": "Report format repair unavailable",
                    "reason": str(error),
                })
            left = format_problems(text)
            if left:
                self.skipped.append(
                    {
                        "step": "format",
                        "label": "The report's form",
                        "reason": "; ".join(left)[:600],
                    }
                )
        if not text.lstrip().startswith("<!-- report: v2 -->"):
            text = "<!-- report: v2 -->\n" + text.lstrip()
        if problems := self._report_plan_problems(run, *split_document(text, "report")):
            raise ModelError(" ".join(problems))
        text = fmt.cite_exhibit_rows(fmt.cite_captions(text))
        self._stage(emit, "write", "Writing the report", "done")
        return text
