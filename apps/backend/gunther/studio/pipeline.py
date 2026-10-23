"""The studio: reports and decks made the way a strong analyst and writer would make them.

read → memo (what is worth saying) → storyline (titles first, with a critic) → draft (the whole
thing at once) → format fix → verify (claims against passages, surgical fixes) → assemble.

About eight model calls for a deck instead of dozens, each with the whole picture in view.
See docs/ARTIFACT_QUALITY_GOAL.md §4 and docs/STUDIO_FORMAT.md.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, Field

from gunther import usage
from gunther.agent import Evidence
from gunther.llm import ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.outputs import (
    BuiltOutput,
    Draft,
    Events,
    Notes,
    OutputAgents,
    Planned,
    Pool,
    Spec,
    check_citations,
    split_document,
)
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio.look import look
from gunther.studio.reference import DECK_FORMAT, READ_NOTE, REPORT_FORMAT

logger = logging.getLogger(__name__)

# How much material is read whole. Beyond it the material is read in parts first (notes).
DIRECT_TOKENS = 60_000
PART_TOKENS = 24_000
WRITER_TOKENS = 45_000
CRITIC_ROUNDS = 1  # one review and one revision: a second round rarely changed the story
WORKERS = 12
# Every slide of a deck is also designed as HTML (studio/design.py); False: the template draws.
DESIGN_SLIDES = True
STUDIO_CHECK = (
    "Check this slide or report section. Its title states the conclusion the cited evidence "
    "supports: judge the title against the sources cited in it. Only factual statements can be "
    "'not' or 'partly': numbers, names, dates, events, and what a source says or found — "
    "use 'not' when the cited source does not state it or states otherwise. Recommendations, "
    "decisions, priorities, interpretations and transitions are the author's judgement: mark "
    "them 'inference' or 'nonfactual', never 'not', even when the sources do not say them."
)
# What the claim fix works on: a cited fact its passage does not state, and a number with no
# source (the fixer cites the passage that states it, or drops the number).
FIXABLE = ("unsupported", "contradicted", "unsourced")
READ_HINT = r"\b(pre-?read|handout|to read|send|email)\b|阅读|发给|会后"


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


class Insight(BaseModel):
    claim: str
    so_what: str = ""
    refs: list[int] = Field(default_factory=list)


class Fact(BaseModel):
    fact: str
    value: str = ""
    compared_to: str = ""
    refs: list[int] = Field(default_factory=list)


class Tension(BaseModel):
    issue: str
    refs: list[int] = Field(default_factory=list)


class Memo(BaseModel):
    language: str = ""
    answer: str
    insights: list[Insight] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    tensions: list[Tension] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    title: str = ""


class StorySlide(BaseModel):
    title: str
    layout: str = "points"
    job: str = ""
    refs: list[int] = Field(default_factory=list)


class DeckStory(BaseModel):
    title: str
    subtitle: str = ""
    slides: list[StorySlide] = Field(min_length=2)


class StorySection(BaseModel):
    heading: str
    job: str = ""
    exhibits: list[str] = Field(default_factory=list)
    refs: list[int] = Field(default_factory=list)


class ReportStory(BaseModel):
    title: str
    summary_heading: str = ""
    summary_points: list[str] = Field(default_factory=list)
    sections: list[StorySection] = Field(min_length=1)


class Problem(BaseModel):
    where: str = ""
    problem: str = ""
    fix: str = ""


class Review(BaseModel):
    understood: str = ""
    verdict: str = "revise"
    problems: list[Problem] = Field(default_factory=list)


@dataclass
class Run:
    """What one build knows as it goes."""

    spec: Spec
    use: str  # "talk" | "read" (decks); "" for reports
    language: str
    memo: Memo | None = None
    story: DeckStory | ReportStory | None = None
    reviews: list[Review] | None = None


def _no_events(_event: dict[str, Any]) -> None:
    return None


def _in_context(function):
    """Run ``function`` in a copy of this thread's context (traces, budgets) on a worker."""

    context = contextvars.copy_context()
    return lambda *args: context.copy().run(function, *args)


class StudioAgents(OutputAgents):
    """Makes a report or a deck with the studio method. Checking reuses OutputAgents."""

    def __init__(
        self,
        gateway,
        limits=None,
        *,
        use: str | None = None,
        tool=None,
        renderer=None,
        design: bool | None = None,
    ) -> None:
        super().__init__(gateway, limits)
        self.design = DESIGN_SLIDES if design is None else design
        # Draws slides with the real renderer for the "look" step; None: that step is skipped.
        self.renderer = renderer
        self.use = use if use in ("talk", "read") else None
        self.tool = tool
        self.trail: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}

    # Plumbing -----------------------------------------------------------------------------

    def _stage(self, emit: Events, step: str, label: str, state: str, detail: str = "") -> None:
        event: dict[str, Any] = {"type": "stage", "id": step, "label": label, "state": state}
        if detail:
            event["detail"] = detail
        if state == "running":
            self._started[step] = time.monotonic()
        if state in ("done", "failed"):
            took = round(time.monotonic() - self._started.get(step, time.monotonic()))
            event["seconds"] = took
            self.trail.append({"step": step, "state": state, "detail": detail, "seconds": took})
            logger.info("Studio %s %s in %ss: %s", step, state, took, detail[:120])
        emit(event)

    def _json(self, model, schema, system, prompt, effort):
        # A reply in the wrong shape (twice, inside the gateway) is asked for once more from
        # scratch before the step fails: one bad reply should not cost the whole build.
        try:
            result, _ = self.gateway.complete_json(
                model, schema, system=system, prompt=prompt, effort=effort
            )
        except ModelError as error:
            if "could not read" not in str(error):
                raise
            logger.info("Asking again from scratch after an unreadable reply")
            result, _ = self.gateway.complete_json(
                model, schema, system=system, prompt=prompt, effort=effort
            )
        return result

    def _text(self, model, system, prompt, effort, max_tokens=16_000) -> str:
        # A thinking model spends its budget thinking first: never ask for less than this.
        room = max(max_tokens, 16_000)
        try:
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort=effort, max_tokens=room
            )
        except ModelError as error:
            if "ran out of room" not in str(error) or effort in (None, "off", "low"):
                raise
            logger.info("Out of room while thinking; asking again with less thinking")
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort="low", max_tokens=room
            )
        return completion.text

    @staticmethod
    def _passage(item: Evidence, limit: int | None = None) -> str:
        text = item.context or item.text
        if limit:
            text = text[:limit]
        where = f" · {item.locator}" if item.locator else ""
        return f"[{item.ref}] ({item.title}{where})\n{text.strip()}"

    def _material(self, items: list[Evidence]) -> str:
        return "\n\n".join(self._passage(item) for item in items)

    def _language(self, spec: Spec, pool: Pool) -> str:
        sample = spec.brief or " ".join(item.text[:200] for item in pool.items[:10])
        cjk = sum(1 for char in sample if "一" <= char <= "鿿")
        return "Chinese (简体中文)" if cjk > len(sample) * 0.15 else "English"

    # 1. Reading ---------------------------------------------------------------------------

    def read(self, model, run: Run, pool: Pool, notes: Notes, emit: Events) -> str:
        """The material as the memo step reads it: whole when it fits, else notes per part."""

        items = list(pool.items)
        whole = self._material(items)
        extra = self._notes_text(notes)
        if tokens(whole) <= DIRECT_TOKENS:
            return extra + whole
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
                return self._json(model, PartNotes, system, self._material(part), "low")
            except ModelError as error:
                logger.info("Reading a part failed: %s", error)
                return None

        with ThreadPoolExecutor(WORKERS) as workers:
            found = list(workers.map(_in_context(note), parts))
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
        return (
            extra
            + "Sources: "
            + "; ".join(sources)
            + "\n\nNotes from the material:\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _notes_text(notes: Notes) -> str:
        lines = []
        if notes.knowledge:
            lines.append("The person's saved notes (context, never cited):")
            lines += [f"- {title}: {text[:600]}" for title, text in notes.knowledge]
        if notes.discussions:
            lines.append("The person's earlier discussions (context, never cited):")
            lines += [f"- {title}: {text[:400]}" for title, text in notes.discussions]
        return ("\n".join(lines) + "\n\n") if lines else ""

    # 2. Memo ------------------------------------------------------------------------------

    def memo(self, model, effort, run: Run, material: str, emit: Events) -> Memo:
        self._stage(emit, "memo", "Finding what is worth saying", "running")
        spec = run.spec
        what = "a slide deck" if spec.kind == "slides" else "a report"
        system = P.MEMO.format(
            what=what,
            brief=spec.brief or "(none: choose the most valuable question the material answers)",
            audience=spec.audience,
            use_line=f"Use: {run.use}." if run.use else "",
            kind="deck" if spec.kind == "slides" else "report",
            truth=P.TRUTH,
        )
        memo = self._json(model, Memo, system, "The material:\n\n" + material, effort)
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
        spec = run.spec
        brief = spec.brief
        minutes = re.search(r"(\d+)\s*(?:-|\s)?\s*(?:min|minute|分钟)", brief, re.I)
        pages = re.search(r"(\d+)\s*(?:slides|pages|页|张)", brief, re.I)
        if spec.kind == "slides":
            if pages:
                return f"exactly {pages.group(1)} slides, as the brief asks"
            if minutes:
                count = max(5, min(20, round(int(minutes.group(1)) * 0.9)))
                return f"about {count} slides for {minutes.group(1)} minutes"
            return "as many slides as the argument needs, usually 8–12; never pad"
        words = re.search(r"(\d+)\s*(?:words|字)", brief, re.I)
        if words:
            return f"about {words.group(1)} words"
        return "as long as the argument needs, usually 1,500–3,000 words in 4–7 sections"

    def storyline(self, model, effort, run: Run, material: str, emit: Events):
        spec = run.spec
        self._stage(emit, "story", "Planning the storyline", "running")
        memo = self._memo_text(run.memo) if run.memo else ""
        if spec.kind == "slides":
            schema = DeckStory
            system = P.DECK_STORY.format(
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
            )
        figures = self._figures(material)
        prompt = "The material (for reference):\n\n" + self._trim(material, WRITER_TOKENS) + figures
        story = self._json(model, schema, system, prompt, effort)
        run.reviews = []
        for round_ in range(CRITIC_ROUNDS):
            review = self.critique(model, run, story)
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
            if review.verdict.strip().lower() == "ready" or not review.problems:
                break
            again = P.STORY_AGAIN.format(
                review="\n".join(f"- [{p.where}] {p.problem} → {p.fix}" for p in review.problems)
            )
            try:
                story = self._json(
                    model,
                    schema,
                    system,
                    prompt + "\n\nYour storyline:\n" + story.model_dump_json() + "\n\n" + again,
                    "medium",
                )
            except ModelError as error:
                logger.info("Revising the storyline failed: %s", error)
                break
        if isinstance(story, DeckStory):
            story = self._tidy_deck_story(story)
        self._stage(emit, "story", "Planning the storyline", "done", story.title)
        emit({"type": "storyline", "storyline": story.model_dump()})
        # The planned titles, as the live outline the app already shows while a build runs.
        planned = (
            [{"heading": s.title, "goal": s.job} for s in story.slides]
            if isinstance(story, DeckStory)
            else [{"heading": story.summary_heading or "Summary", "goal": ""}]
            + [{"heading": s.heading, "goal": s.job} for s in story.sections]
        )
        emit({"type": "outline", "title": story.title, "sections": planned})
        return story

    @staticmethod
    def _tidy_deck_story(story: DeckStory) -> DeckStory:
        slides = [
            replace_layout(slide, slide.layout if slide.layout in fmt.LAYOUTS else "points")
            for slide in story.slides
        ]
        if slides[0].layout != "title":
            slides.insert(0, StorySlide(title=story.title, layout="title", job=story.subtitle))
        for index, slide in enumerate(slides[1:], 1):
            if slide.layout == "title":
                slides[index] = replace_layout(slide, "statement")
        return DeckStory(title=story.title, subtitle=story.subtitle, slides=slides)

    def critique(self, model, run: Run, story) -> Review:
        spec = run.spec
        if isinstance(story, DeckStory):
            lines = [
                f"{n}. [{s.layout}] {s.title}  — job: {s.job}"
                for n, s in enumerate(story.slides, 1)
            ]
        else:
            lines = [f"Title: {story.title}", f"Summary: {story.summary_heading}"]
            lines += [f"  - {p}" for p in story.summary_points]
            lines += [
                f"{n}. {s.heading}  — job: {s.job}"
                + (f" — exhibits: {'; '.join(s.exhibits)}" if s.exhibits else "")
                for n, s in enumerate(story.sections, 1)
            ]
        system = P.CRITIC.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            story="\n".join(lines),
            answer=run.memo.answer if run.memo else "(not given)",
        )
        try:
            return self._json(model, Review, system, "Review it now.", "medium")
        except ModelError as error:
            logger.info("The storyline review failed: %s", error)
            return Review(verdict="ready")

    @staticmethod
    def _figures(material: str) -> str:
        found = re.findall(r"!\[[^\]]*\]\(gunther-asset:[A-Za-z0-9_-]+\)", material)
        if not found:
            return "\n\nNo figures are available: do not use the figure layout."
        return "\n\nFigures available (copy exactly):\n" + "\n".join(sorted(set(found)))

    @staticmethod
    def _trim(text: str, budget: int) -> str:
        if tokens(text) <= budget:
            return text
        ratio = budget / max(1, tokens(text))
        return text[: int(len(text) * ratio)] + "\n…"

    # 4. Writing ---------------------------------------------------------------------------

    def _writer_material(self, run: Run, pool: Pool, material: str) -> str:
        """The passages the writer quotes from: all of them when they fit, else the ones the
        memo and the storyline lean on, then the rest by order, up to the budget."""

        if tokens(material) <= WRITER_TOKENS:
            return material
        wanted: list[int] = []
        if run.memo:
            for item in [*run.memo.insights, *run.memo.facts, *run.memo.tensions]:
                wanted += item.refs
        story = run.story
        if isinstance(story, DeckStory):
            for slide in story.slides:
                wanted += slide.refs
        elif isinstance(story, ReportStory):
            for section in story.sections:
                wanted += section.refs
        by_ref = pool.by_ref()
        order = list(dict.fromkeys([r for r in wanted if r in by_ref]))
        order += [item.ref for item in pool.items if item.ref not in order]
        chosen, size = [], 0
        for ref in order:
            text = self._passage(by_ref[ref])
            if size + tokens(text) > WRITER_TOKENS:
                continue
            chosen.append(ref)
            size += tokens(text)
        chosen.sort()
        return self._material([by_ref[ref] for ref in chosen])

    def write_deck(self, model, effort, run: Run, pool: Pool, material: str, emit: Events) -> str:
        spec = run.spec
        story = run.story
        assert isinstance(story, DeckStory)
        self._stage(emit, "write", "Writing the slides", "running")
        story_text = "\n".join(
            [f"Deck title: {story.title}", f"Subtitle: {story.subtitle}"]
            + [
                f"{n}. [{s.layout}] {s.title} — job: {s.job} {''.join(f'[{r}]' for r in s.refs)}"
                for n, s in enumerate(story.slides, 1)
            ]
        )
        theme = spec.theme if spec.theme in fmt.THEMES else "paper"
        system = P.DECK_WRITE.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            use=run.use,
            use_rule=P.USE_RULES[run.use],
            voice=P.VOICE.format(language=run.language),
            truth=P.TRUTH,
            format=DECK_FORMAT + ("\n\n" + READ_NOTE if run.use == "read" else ""),
            memo=self._memo_text(run.memo) if run.memo else "",
            story=story_text,
            theme=theme,
        )
        prompt = (
            "The material (passages with their numbers):\n\n"
            + self._writer_material(run, pool, material)
            + self._figures(material)
        )
        text = self._text(model, system, prompt, effort, max_tokens=24_000)
        deck = _unfence(text)
        slides = fmt.split_slides(deck)
        if len(slides) < len(story.slides):
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
        """Each slide that breaks the format is written again on its own, all such slides at
        once, for at most three rounds; a slide that still breaks it keeps its words and is
        drawn by the renderer's fallback."""

        read = run.use == "read"
        notes = run.use == "talk"
        guide = DECK_FORMAT + ("\n\n" + READ_NOTE if read else "")
        for _ in range(3):
            found = fmt.check_deck(deck, read=read, notes_required=notes)
            if not found:
                break
            slides = fmt.split_slides(deck)

            def fix(item: tuple[int, list[str]], slides: list[str] = slides) -> str | None:
                n, problems = item
                system = P.FIX_SLIDES.format(
                    format=guide,
                    problems=f"Slide {n} problems:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + f"\n\nSlide {n} as written:\n{slides[n - 1]}",
                )
                try:
                    again = fmt.split_slides(
                        _unfence(self._text(model, system, "Rewrite it now.", effort, 4000))
                    )
                except ModelError as error:
                    logger.info("Fixing slide %d failed: %s", n, error)
                    return None
                if not again:
                    return None
                text = fmt.strip_labels(again[0])
                if n == 1:
                    text = self._title_marker(
                        [text], fmt.parse_slide(slides[0]).attrs.get("theme", "paper")
                    )[0]
                return text

            with ThreadPoolExecutor(WORKERS) as workers:
                fixed = list(workers.map(_in_context(fix), found))
            changed = False
            for (n, _), text in zip(found, fixed, strict=True):
                if text is not None and text != slides[n - 1]:
                    slides[n - 1], changed = text, True
            deck = "\n\n---\n\n".join(slides)
            if not changed:
                break
        left = fmt.check_deck(deck, read=read, notes_required=run.use == "talk")
        if any("units long" in p for _, problems in left for p in problems):
            slides = fmt.split_slides(deck)
            for n, problems in left:
                if any("units long" in p for p in problems):
                    slides[n - 1] = fmt.trim_to_notes(slides[n - 1], first=n == 1, read=read)
            deck = "\n\n---\n\n".join(slides)
            left = fmt.check_deck(deck, read=read, notes_required=run.use == "talk")
        if left:
            self.skipped.append(
                {
                    "step": "format",
                    "label": f"{len(left)} slide(s) still break the format",
                    "reason": "; ".join(f"slide {n}: {p[0]}" for n, p in left)[:600],
                }
            )
        return deck

    def write_report(self, model, effort, run: Run, pool: Pool, material: str, emit: Events) -> str:
        spec = run.spec
        story = run.story
        assert isinstance(story, ReportStory)
        self._stage(emit, "write", "Writing the report", "running")
        story_text = "\n".join(
            [f"Title: {story.title}", f"Executive summary heading: {story.summary_heading}"]
            + [f"  - {p}" for p in story.summary_points]
            + [
                f"{n}. {s.heading} — job: {s.job}"
                + (f" — exhibits: {'; '.join(s.exhibits)}" if s.exhibits else "")
                + " "
                + "".join(f"[{r}]" for r in s.refs)
                for n, s in enumerate(story.sections, 1)
            ]
        )
        system = P.REPORT_WRITE.format(
            brief=spec.brief or "(none)",
            audience=spec.audience,
            length=f"Length: {self._length(run)}.",
            voice=P.VOICE.format(language=run.language),
            truth=P.TRUTH,
            format=REPORT_FORMAT,
            memo=self._memo_text(run.memo) if run.memo else "",
            story=story_text,
        )
        prompt = (
            "The material (passages with their numbers):\n\n"
            + self._writer_material(run, pool, material)
            + self._figures(material)
        )
        text = _unfence(self._text(model, system, prompt, effort, max_tokens=24_000))
        if not text.lstrip().startswith("<!-- report: v2 -->"):
            text = "<!-- report: v2 -->\n" + text.lstrip()
        problems = fmt.check_report(text)
        if problems:
            # One revision of the whole report for what its form gets wrong.
            fix = P.FIX_REPORT.format(
                format=REPORT_FORMAT, problems="\n".join(f"- {p}" for p in problems)
            )
            try:
                fixed = _unfence(
                    self._text(
                        model,
                        system + "\n\n" + fix,
                        prompt + "\n\nYour report:\n\n" + text,
                        effort,
                        max_tokens=24_000,
                    )
                )
                if fixed.count("\n## ") >= text.count("\n## ") - 1:
                    text = fixed
            except ModelError as error:
                logger.info("Fixing the report's form failed: %s", error)
            left = fmt.check_report(text)
            if left:
                self.skipped.append(
                    {
                        "step": "format",
                        "label": "The report's form",
                        "reason": "; ".join(left)[:600],
                    }
                )
        text = fmt.cite_exhibit_rows(fmt.cite_captions(fmt.ensure_summary_mark(text)))
        self._stage(emit, "write", "Writing the report", "done")
        return text

    # 5. Verifying -------------------------------------------------------------------------

    def verify(self, model, run: Run, parts: list[str], pool: Pool, emit: Events) -> list[Draft]:
        """Each part on its own, all parts at once: its claims checked against the passages it
        cites, the unsupported ones fixed, and the fixed part checked once more. No part waits
        for another."""

        self._stage(emit, "verify", "Checking every claim against its source", "running")
        by_ref = pool.by_ref()

        rest = "\n".join(parts[1:])

        def check(text: str) -> Draft:
            text, _ = check_citations(text, set(by_ref))
            issues, coverage = self._claims(model, text, by_ref, rest)
            issues += self._chart_issues(text, by_ref)
            return Draft(text, issues, coverage)

        def check_and_fix(text: str) -> Draft:
            draft = check(text)
            if not any(issue.verdict in FIXABLE for issue in draft.issues or []):
                return draft
            title = fmt.MARK.match(text) and fmt.parse_slide(text).layout == "title"
            fixed = self._fix_claims(model, [draft], by_ref, rest if title else "")
            return check(fixed[0]) if fixed else draft

        with ThreadPoolExecutor(WORKERS) as workers:
            drafts = list(workers.map(_in_context(check_and_fix), parts))
        for index, draft in enumerate(drafts):
            if any(issue.note.startswith("Chart value") for issue in draft.issues or []):
                # A chart whose values its sources do not state is drawn as a table.
                drafts[index] = Draft(
                    fmt.with_marker(draft.text, "table"), draft.issues, draft.coverage
                )
                self.skipped.append(
                    {
                        "step": "verify",
                        "label": f"Part {index + 1} drawn as a table",
                        "reason": "Some chart values were not found in their cited sources.",
                    }
                )
        left = sum(
            1
            for d in drafts
            for i in d.issues or []
            if i.verdict in ("unsupported", "contradicted")
        )
        self._stage(
            emit,
            "verify",
            "Checking every claim against its source",
            "done",
            f"{left} claim(s) still flagged" if left else "all claims supported",
        )
        return drafts

    def _claims(self, model, text: str, by_ref: dict[int, Evidence], rest: str = ""):
        """One claim check per part. A title is checked against the passages its own slide or
        section cites (it states their conclusion). The title slide states the whole deck's
        conclusion, so a figure on it is checked against the passages the other slides (``rest``)
        cite for that same figure, not only its own. Interpretation and recommendation that
        follow from cited facts are the author's judgement, not findings; what is flagged is a
        cited claim its passages do not state, and a specific fact (a number) with no source."""

        from gunther.outputs import Issue, _marked, section_hash

        marked = _marked(text)
        body = marked.body
        lines = body.split("\n")
        if marked.layout == "title":
            # The title slide's meta line (audience · date) states no claim.
            content = [n for n, line in enumerate(lines) if line.strip()]
            if len(content) >= 3:
                lines[content[2]] = ""
            lines = [_lend_refs(line, rest) for line in lines]
        refs = "".join(f"[{n}]" for n in dict.fromkeys(re.findall(r"\[(\d+)\]", body)))
        for at, line in enumerate(lines):
            if re.match(r"^#{1,2}\s", line):
                if refs and not re.search(r"\[\d+\]", line):
                    lines[at] = line.rstrip() + " " + refs
                break
        checked = "\n".join(lines)
        _, check, _, _ = self.ask.check_sentences(
            model, STUDIO_CHECK, checked, by_ref, limit=self.limits.sentences
        )
        coverage = (
            check._coverage
            if check is not None
            else {"status": "failed", "considered": 0, "checked": 0, "unchecked": 0, "claims": []}
        )
        coverage["answerHash"] = section_hash(text)
        issues = []
        for claim in coverage.get("claims", []):
            verdict, sentence = claim["verdict"], claim["text"]
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", sentence).strip()
            if verdict in ("not", "partly"):
                issues.append(Issue(plain[:300], "unsupported", claim.get("reason") or ""))
            elif verdict == "unsourced" and re.search(r"\d", plain):
                issues.append(Issue(plain[:300], "unsourced", "A specific fact with no source."))
        # A number with no citation on its line (a recommendation repeating a figure, a table
        # row): the claim fix cites the passage it comes from, or drops the number.
        held = {issue.claim[:80] for issue in issues}
        for line in fmt.uncited_lines([ln for ln in body.split("\n") if not ln.startswith(":::")]):
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", line).strip()
            if plain[:80] not in held and not re.match(r"^(Source|Sources|来源)\s*[:：]", plain):
                issues.append(Issue(plain[:300], "unsourced", "A number with no citation."))
        return issues, coverage

    def _cover(self, model, drafts: list[Draft], pool: Pool) -> list[Draft]:
        """The subtitle is the deck's core message: after the slides are final, it is made to
        state what their titles say (claim fixes can move a title away from it), then the
        title slide is checked against its sources again."""

        if not drafts or fmt.parse_slide(drafts[0].text).layout != "title":
            return drafts
        cover = fmt.parse_slide(drafts[0].text)
        body = [line for line in cover.body if line.strip()]
        if not cover.title or not body:
            return drafts
        titles = [fmt.parse_slide(d.text).title for d in drafts[1:]]
        titles = [t for t in titles if t]
        rest = "\n".join(d.text for d in drafts[1:])
        cited = [
            line.strip()
            for line in rest.split("\n")
            if re.search(r"\[\d+\]", line)
            and fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", line)) & fmt.numbers_in(" ".join(titles))
        ][:30]
        system = P.COVER.format(
            title=cover.title,
            subtitle=body[0],
            titles="\n".join(f"{n}. {t}" for n, t in enumerate(titles, 2)),
            cited="\n".join(cited) or "(none)",
        )
        try:
            line = _unfence(self._text(model, system, "Reply now.", "low", 2000)).strip()
        except ModelError as error:
            logger.info("Aligning the subtitle failed: %s", error)
            return drafts
        line = line.splitlines()[0].strip() if line else ""
        line = re.sub(r"^(Subtitle|副标题)\s*[:：]\s*", "", line)
        if not line or line == body[0] or line.startswith("#"):
            return drafts
        text = drafts[0].text.replace(body[0], line, 1)
        by_ref = pool.by_ref()
        text, _ = check_citations(text, set(by_ref))
        issues, coverage = self._claims(model, text, by_ref, rest)
        if any(i.verdict in ("unsupported", "contradicted") for i in issues):
            logger.info("The aligned subtitle is not supported; keeping the old one")
            return drafts
        return [Draft(text, issues + self._chart_issues(text, by_ref), coverage), *drafts[1:]]

    def _refit(self, model, effort, run: Run, drafts: list[Draft], pool: Pool) -> list[Draft]:
        """Claim fixes can lengthen a slide past its budget: the format is checked again, and a
        slide that changed is checked against its sources once more."""

        joined = "\n\n---\n\n".join(d.text for d in drafts)
        if not fmt.check_deck(joined, read=run.use == "read", notes_required=False):
            return drafts
        fitted = fmt.split_slides(self.fix_format(model, effort, run, joined, _no_events))
        if len(fitted) != len(drafts):
            return drafts
        changed = [i for i, (d, t) in enumerate(zip(drafts, fitted, strict=True)) if d.text != t]
        by_ref = pool.by_ref()

        rest = "\n".join(fitted[1:])

        def check(text: str) -> Draft:
            issues, coverage = self._claims(model, text, by_ref, rest)
            return Draft(text, issues + self._chart_issues(text, by_ref), coverage)

        with ThreadPoolExecutor(WORKERS) as workers:
            again = list(workers.map(_in_context(check), [fitted[i] for i in changed]))
        for index, draft in zip(changed, again, strict=True):
            drafts[index] = draft
        return drafts

    @staticmethod
    def _chart_issues(text: str, by_ref: dict[int, Evidence]):
        from gunther.outputs import Issue

        issues = []
        for value, refs in fmt.chart_numbers(text):
            sources = " ".join((by_ref[r].context or by_ref[r].text) for r in refs if r in by_ref)
            if not refs or fmt.normal_number(value) not in fmt.numbers_in(sources):
                issues.append(
                    Issue(value, "unsupported", "Chart value not found in its cited source.")
                )
        return issues

    def _fix_claims(self, model, drafts: list[Draft], by_ref, rest: str = "") -> list[str] | None:
        parts = []
        refs: set[int] = set()
        for number, draft in enumerate(drafts, 1):
            flagged = [i for i in draft.issues or [] if i.verdict in FIXABLE]
            parts.append(
                f"Part {number}:\n{draft.text}\n\nUnsupported in part {number}:\n"
                + "\n".join(f'- "{i.claim}" ({i.note})' for i in flagged)
            )
            lent = "\n".join(_lend_refs(line, rest) for line in draft.text.split("\n"))
            refs |= {int(n) for n in re.findall(r"\[(\d+)\]", lent if rest else draft.text)}
            refs |= _stating(flagged, by_ref)
        passages = "\n\n".join(self._passage(by_ref[r], 3000) for r in sorted(refs) if r in by_ref)
        system = P.FIX_CLAIMS.format(parts="\n\n=====\n\n".join(parts), passages=passages)
        try:
            text = _unfence(self._text(model, system, "Rewrite them now.", "medium", 16_000))
        except ModelError as error:
            logger.info("Fixing unsupported claims failed: %s", error)
            return None
        pieces = [p.strip() for p in re.split(r"\n-{3,}\n", "\n" + text + "\n") if p.strip()]
        if len(pieces) != len(drafts):
            logger.info("Claim fix returned %d parts for %d", len(pieces), len(drafts))
            if len(drafts) == 1:
                return None

            # Asked again one part at a time; a part whose fix fails keeps its text.
            def single(draft: Draft) -> str:
                one = self._fix_claims(model, [draft], by_ref)
                return one[0] if one else draft.text

            with ThreadPoolExecutor(WORKERS) as workers:
                return list(workers.map(_in_context(single), drafts))
        out = []
        for draft, piece in zip(drafts, pieces, strict=True):
            piece = re.sub(r"\A\s*Part \d+:\s*\n", "", piece)
            # A slide keeps its marker even when the fixer dropped it.
            marker = fmt.MARK.match(draft.text)
            if marker and not fmt.MARK.match(piece):
                piece = marker.group(0).strip() + "\n" + piece
            out.append(piece)
        return out

    # 4. Designing -------------------------------------------------------------------------

    def design_deck(self, model, run: Run, drafts: list[Draft], emit: Events) -> list[Draft]:
        """The deck's style, then every slide designed on its own. The words are final by now:
        a design may not change one. It never fails a build: whatever goes wrong, the slides
        stay as they are and the template draws them."""

        label = "Designing the slides"
        self._stage(emit, "design", label, "running")
        try:
            slides = [d.text for d in drafts]
            first = fmt.parse_slide(slides[0])
            style = D.pick_style(
                self,
                model,
                title=first.title,
                subtitle=first.body[0] if first.body else "",
                brief=run.spec.brief,
                audience=run.spec.audience,
            )
            designed, count, notes = D.design_slides(
                self, model, D.apply_style(slides, style), style, workers=WORKERS
            )
        except Exception as error:  # noqa: BLE001 - designing never fails a build
            logger.warning("Designing the slides failed: %s", error)
            self.skipped.append(
                {"step": "design", "label": "Slides were not designed", "reason": str(error)[:300]}
            )
            self._stage(emit, "design", label, "done", "not designed")
            return drafts
        self.skipped += notes
        plain = len(drafts) - count
        self._stage(
            emit,
            "design",
            label,
            "done",
            f"{count} slides designed" + (f", {plain} drawn plainly" if plain else ""),
        )
        return [replace(d, text=text) for d, text in zip(drafts, designed, strict=True)]

    # The whole run ------------------------------------------------------------------------

    @usage.job("outputs")
    def make(
        self,
        model: ModelInfo,
        effort: Effort | None,
        spec: Spec,
        pool: Pool,
        notes: Notes,
        events: Events | None = None,
        *,
        fallback_title: str = "",
        resume: dict[str, Any] | None = None,
        checkpoint: Any = None,
        **_ignored: Any,
    ) -> BuiltOutput:
        """``resume``: what an earlier, failed run of the same request finished (its memo and
        storyline), used when the pool still has the same passages; ``checkpoint(state)`` hears
        that state after each of those steps, so a failure later does not lose them."""

        emit = events or _no_events
        if not pool.items:
            raise ModelError("There is no material to work from: choose some sources first.")
        use = self.use or ("read" if re.search(READ_HINT, spec.brief, re.I) else "talk")
        run = Run(spec, use if spec.kind == "slides" else "", self._language(spec, pool))
        material = self.read(model, run, pool, notes, emit)
        saved = resume if resume and resume.get("passages") == len(pool) else {}
        state: dict[str, Any] = {"passages": len(pool), "kind": spec.kind, "brief": spec.brief}
        if saved.get("memo") and saved.get("brief") == spec.brief:
            run.memo = Memo.model_validate(saved["memo"])
            self._stage(emit, "memo", "Finding what is worth saying", "done", "kept from before")
        else:
            run.memo = self.memo(model, effort, run, material, emit)
        state["memo"] = run.memo.model_dump()
        if checkpoint:
            checkpoint(dict(state))
        if run.memo.language:
            run.language = run.memo.language
        if saved.get("storyline") and saved.get("memo") == state["memo"]:
            schema = DeckStory if spec.kind == "slides" else ReportStory
            run.story = schema.model_validate(saved["storyline"])
            self._stage(emit, "story", "Planning the storyline", "done", "kept from before")
        else:
            run.story = self.storyline(model, effort, run, material, emit)
        state["storyline"] = run.story.model_dump()
        if checkpoint:
            checkpoint(dict(state))
        looked: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        if spec.kind == "slides":
            text = self.write_deck(model, effort, run, pool, material, emit)
            text = self.fix_format(model, effort, run, text, emit)
            parts = fmt.split_slides(text)
            drafts = self.verify(model, run, parts, pool, emit)
            drafts = self._cover(model, drafts, pool)
            drafts = self._refit(model, effort, run, drafts, pool)
            if self.design and drafts:
                drafts = self.design_deck(model, run, drafts, emit)
            outline = [
                Planned(
                    fmt.parse_slide(d.text).title or f"Slide {n}",
                    layout=fmt.parse_slide(d.text).layout,
                )
                for n, d in enumerate(drafts, 1)
            ]
            title = outline[0].heading if outline else (run.story.title or fallback_title)

            def assemble_deck(parts: list[Draft]) -> BuiltOutput:
                planned = [
                    Planned(
                        fmt.parse_slide(d.text).title or f"Slide {n}",
                        layout=fmt.parse_slide(d.text).layout,
                    )
                    for n, d in enumerate(parts, 1)
                ]
                return self.assemble(spec, title, "", parts, pool, planned, model, effort)

            built = assemble_deck(drafts)
            try:
                built, looked, snapshot = look(
                    self, built, drafts, model=model, effort=effort, use=run.use, pool=pool,
                    assemble=assemble_deck, emit=emit, unfence=_unfence,
                )  # fmt: skip
            except Exception as error:  # noqa: BLE001 - looking never fails a build
                logger.warning("The look at the slides failed: %s", error)
        else:
            text = self.write_report(model, effort, run, pool, material, emit)
            preamble, sections = split_document(text, "report")
            if not sections:
                raise ModelError("The report came back without sections.")
            drafts = self.verify(model, run, sections, pool, emit)
            # A claim fix may rewrite an exhibit: its caption and rows cite their sources again.
            drafts = [
                replace(
                    d,
                    text=fmt.cite_exhibit_rows(fmt.cite_captions(fmt.table_separators(d.text))),
                )
                for d in drafts
            ]
            # A sentence repeating a figure cited elsewhere in the report cites it too.
            # (Sections hold blank lines themselves: they are joined on a line of their own.)
            cut = "\n\n<!-- section break -->\n\n"
            whole = cut.join(d.text for d in drafts)
            fixed = fmt.cite_repeated_numbers(whole).split(cut)
            if len(fixed) == len(drafts):
                drafts = [replace(d, text=t) for d, t in zip(drafts, fixed, strict=True)]
            # What is still uncited cites the passages that state its figures, preferring
            # those its own section cites.
            stated = {r: (e.context or e.text) for r, e in pool.by_ref().items() if r is not None}
            drafts = [replace(d, text=fmt.cite_stated_numbers(d.text, stated)) for d in drafts]
            outline = [
                Planned(re.sub(r"^##\s*", "", d.text.splitlines()[0]).strip()[:160]) for d in drafts
            ]
            title = run.story.title or fallback_title
            built = self.assemble(spec, title, preamble, drafts, pool, outline, model, effort)
        approach = {
            "method": "studio",
            # The fields an approach has everywhere (Approach.saved_as reads them).
            "question": spec.brief,
            "purpose": "",
            "structure": "",
            "structure_reason": "",
            "pages": len(drafts) if spec.kind == "slides" else None,
            "supplements": [],
            "gaps": [t.issue for t in run.memo.tensions] if run.memo else [],
            "use": run.use or None,
            "answer": run.memo.answer if run.memo else "",
            "memo": run.memo.model_dump() if run.memo else None,
            "storyline": run.story.model_dump() if run.story else None,
            "reviews": [r.model_dump() for r in run.reviews or []],
            "ref_map": {str(ref): n for n, ref in enumerate(built.pool_refs, 1)},
        }
        return replace(
            built,
            approach=approach,
            skill={
                "name": "studio",
                "version": 1,
                "skipped": self.skipped,
                "trail": self.trail,
                **({"look": looked} if looked else {}),
                **({"renderCheck": snapshot} if snapshot else {}),
            },
        )


def replace_layout(slide: StorySlide, layout: str) -> StorySlide:
    return slide.model_copy(update={"layout": layout})


def _stating(issues, by_ref: dict[int, Evidence], most: int = 6) -> set[int]:
    """Passages that state the figures of claims flagged as having no source, so the fix can
    cite them. Small whole numbers match too much to tell; a sign is a direction a passage may
    state in words ("down 9.5%")."""

    wanted = {
        n.lstrip("-")
        for issue in issues
        if issue.verdict == "unsourced"
        for n in fmt.numbers_in(issue.claim)
        if "." in n or len(re.sub(r"\D", "", n)) >= 3
    }
    found: list[int] = []
    for ref, item in by_ref.items():
        stated = {n.lstrip("-") for n in fmt.numbers_in(item.context or item.text)}
        if ref is not None and wanted & stated:
            found.append(ref)
    return set(found[:most])


def _lend_refs(line: str, rest: str, most: int = 6) -> str:
    """The line with the citations other slides give for the same figures added (for checking
    only). A line with no figure, or whose figures no other slide cites, is left as it is."""

    plain = re.sub(r"\s*\[\d+\]", "", line)
    wanted = fmt.numbers_in(plain)
    if not wanted:
        return line
    lent: list[str] = []
    for other in rest.split("\n"):
        cited = re.findall(r"\[(\d+)\]", other)
        if cited and wanted & fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", other)):
            lent += cited
    own = re.findall(r"\[(\d+)\]", line)
    extra = [n for n in dict.fromkeys(lent) if n not in own][:most]
    return line.rstrip() + "".join(f" [{n}]" for n in extra) if extra else line


def _unfence(text: str) -> str:
    text = text.strip()
    fenced = re.match(r"\A```[a-z]*\n(?P<body>.*)\n```\s*\Z", text, re.S)
    return fenced.group("body") if fenced else text


__all__ = ["StudioAgents", "tokens"]
