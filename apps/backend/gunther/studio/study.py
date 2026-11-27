"""Studio study plans, figure readings and review of scientific explanations."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from gunther.llm import Image, ModelError
from gunther.llm import unfence as _unfence
from gunther.outputs import BuiltOutput, Events, Pool
from gunther.skill_checks import figures_of
from gunther.slide_render import digest
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.drafting import source_images_kept
from gunther.studio.look import vision_model
from gunther.studio.requests import material_request, warm_map
from gunther.studio.state import DeckStory, Run, _matches_plan, _no_events

logger = logging.getLogger("gunther.studio.pipeline")

FIGURE_WORKERS = 4


class StudioStudy:
    # Scientific handoffs ------------------------------------------------------------------

    @staticmethod
    def _study_text(run: Run) -> str:
        readings = "\n".join(r.model_dump_json() for r in run.figure_readings or [])
        references = []
        for reading in run.figure_readings or []:
            references.append(f"![{reading.shows}](gunther-asset:{reading.asset_id})")
            for panel in reading.panels:
                if panel.box:
                    references.append(
                        f"![{reading.asset_id} panel {panel.label}: {panel.shows}] "
                        f"({S.crop_reference(reading.asset_id, panel.box)})"
                    )
        return (
            (run.study.model_dump_json() if run.study else "")
            + "\nActual FigureReadings (absent means not visually read):\n"
            + readings
            + (
                "\nExact figure references the writer may copy:\n" + "\n".join(references)
                if references
                else ""
            )
            + (
                "\nCoverage plan:\n" + run.coverage_plan.model_dump_json()
                if run.coverage_plan
                else ""
            )
            + "\nInternal limitations: "
            + "; ".join(run.limitations or [])
        )

    def _science_limit(self, run: Run, reason: str) -> None:
        if run.limitations is None:
            run.limitations = []
        if reason in run.limitations:
            return
        run.limitations.append(reason)
        self.skipped.append(
            {"step": "science", "label": "Scientific evidence limitation", "reason": reason}
        )

    def study_map(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events
    ) -> S.StudyMap:
        """The study's argument, mapped before any slide is planned. Links to passages or
        figures the material does not hold are sent back once; still wrong, the step fails."""

        self._stage(emit, "study", "Understanding the study", "running")
        system, prompt = material_request(
            material,
            P.STUDY_MAP.format(brief=run.spec.brief or "(none)", audience=run.spec.audience),
            run.spec,
        )
        study = self._json(model, S.StudyMap, system, prompt, effort)
        if problems := S.map_problems(study, pool.items):
            again = (
                prompt
                + "\n\nYour study map:\n"
                + study.model_dump_json()
                + "\n\nFix these links and return the whole map again:\n"
                + "\n".join(f"- {p}" for p in problems)
            )
            study = self._json(model, S.StudyMap, system, again, effort)
            if problems := S.map_problems(study, pool.items):
                self._stage(emit, "study", "Understanding the study", "failed", problems[0])
                raise ModelError("The study map cites material it does not have: " + problems[0])
        self._stage(emit, "study", "Understanding the study", "done", f"{len(study.claims)} claims")
        return study

    def coverage_plan(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events
    ) -> S.CoveragePlan:
        """A source-derived accounting step, independent of the eventual writer storyline."""

        self._stage(emit, "coverage", "Planning source coverage", "running")
        system, prompt = material_request(
            material,
            P.COVERAGE_PLAN.format(
                brief=run.spec.brief or "(none)",
                audience=run.spec.audience,
                figures=", ".join(S.figure_inventory(pool.items)) or "(none)",
                memo=self._study_text(run),
            ),
            run.spec,
        )
        plan = self._json(model, S.CoveragePlan, system, prompt, effort)
        if problems := S.coverage_problems(plan, pool.items):
            plan = self._json(
                model,
                S.CoveragePlan,
                system,
                prompt
                + "\n\nYour plan:\n"
                + plan.model_dump_json()
                + "\n\nFix every accounting problem and return the whole plan:\n"
                + "\n".join(f"- {problem}" for problem in problems),
                effort,
            )
            if problems := S.coverage_problems(plan, pool.items):
                self._stage(emit, "coverage", "Planning source coverage", "failed", problems[0])
                raise ModelError(
                    "The coverage plan does not account for the source figures: " + problems[0]
                )
        self._stage(
            emit,
            "coverage",
            "Planning source coverage",
            "done",
            f"{len(plan.figures)} source figures accounted for",
        )
        return plan

    def read_figures(
        self, model, effort, run: Run, pool: Pool, emit: Events = _no_events, *,
        retained: Sequence[dict] = (),
    ) -> list[S.FigureReading]:
        assert run.study is not None
        linked = S.figure_inventory(pool.items)
        if not linked:
            return []
        selected = vision_model(self.gateway, model, effort)
        if not selected:
            self._science_limit(
                run, "No model that can see images is set up; figures were not read."
            )
            return []
        if self.asset_reader is None:
            self._science_limit(run, "Source image pixels are unavailable; figures were not read.")
            return []
        eye, eye_effort = selected
        self._stage(emit, "figures", "Looking at the figures", "running")
        reusable = {
            row["source_fingerprint"]: row
            for row in [*self.reusable_figures, *retained]
            if isinstance(row, dict) and isinstance(row.get("source_fingerprint"), str)
        }

        def read(asset_id: str) -> S.FigureReading | None:
            passages = [
                e for e in pool.items if any(f["assetId"] == asset_id for f in figures_of(e))
            ]
            source = next(f for f in figures_of(passages[0]) if f["assetId"] == asset_id)
            prompt = (
                f"asset_id: {asset_id}\nCaption, provenance and linked passages:\n"
                + self._material(passages)
            )
            fingerprint = digest([
                P.FIGURE_READ, prompt, S.pool_print(passages, full=True), eye.ref, eye_effort,
            ])
            saved = reusable.get(fingerprint)
            if saved is not None:
                try:
                    reading = S.FigureReading.model_validate(saved)
                    if reading.asset_id != asset_id:
                        raise ValueError("The saved figure reading has a different asset identity.")
                    if not reading.legible or reading.uncertain:
                        self._science_limit(
                            run,
                            f"Figure {asset_id}: "
                            + ("; ".join(reading.uncertain) or "not reliably legible"),
                        )
                    return reading
                except ValueError:
                    pass  # an old saved shape is reread, never guessed
            # The study map step already bounded these IDs to the chosen passages and revision.
            try:
                path, media, _name = self.asset_reader(asset_id)
                self._count_model_call("figures")
                reading, _ = self.gateway.complete_json(
                    eye,
                    S.FigureReading,
                    system=P.FIGURE_READ,
                    prompt=prompt,
                    images=[Image.from_path(path, media)],
                    effort=eye_effort or "low",
                    schema_at="end",
                )
                if reading.asset_id != asset_id:
                    raise ValueError("The figure reading returned a different asset identity.")
                reading.source_revision_id = source.get("revisionId") or ""
                reading.source_fingerprint = fingerprint
                reading.vision_model = eye.ref
                if not reading.legible or reading.uncertain:
                    self._science_limit(
                        run,
                        f"Figure {asset_id}: "
                        + ("; ".join(reading.uncertain) or "not reliably legible"),
                    )
                return reading
            except (ModelError, OSError, LookupError, ValueError) as error:
                # A figure file that is missing or fails its check is a figure not read.
                self._science_limit(run, f"Figure {asset_id} was not read: {error}")
            return None

        readings = [reading for reading in warm_map(read, linked, FIGURE_WORKERS) if reading]
        self._stage(emit, "figures", "Looking at the figures", "done", f"{len(readings)} read")
        return readings

    def scientific_review(self, model, run: Run, content: str, material: str):
        task = P.SCIENTIFIC_REVIEW.format(
            brief=run.spec.brief or "(none)",
            audience=run.spec.audience,
            memo=self._study_text(run),
            coverage=(run.coverage_plan.model_dump_json() if run.coverage_plan else "(none)"),
            story=content,
        )
        return self._json(
            model, S.ScientificReview, *material_request(material, task, run.spec), "low",
        )

    @staticmethod
    def _plain_built(deck: str, run: Run, pool: Pool, model, effort) -> BuiltOutput:
        """A capture-only view of the words as they will visibly be drawn."""
        slides = fmt.split_slides(deck)
        citations = []
        for item in pool.items:
            citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
            if citation is not None:
                citations.append(citation.model_copy(update={"ref": item.ref}))
        return BuiltOutput(
            title=run.story.title if isinstance(run.story, DeckStory) else "Scientific deck",
            content=deck,
            outline=[
                {"heading": fmt.parse_slide(text).title or f"Slide {n}"}
                for n, text in enumerate(slides, 1)
            ],
            citations=citations,
            checks=[],
            model=model,
            effort=effort,
        )

    @staticmethod
    def _visible_slides(deck: str) -> str:
        rows = []
        for number, text in enumerate(fmt.split_slides(deck), 1):
            slide = fmt.parse_slide(text)
            lines = []
            if slide.title:
                lines.append(f"Title: {slide.title}")
            for line in slide.body:
                found = re.match(r"^!\[([^]]*)\]\((gunther-asset:[^)]+)\)$", line)
                if not found:
                    lines.append(line)
                    continue
                caption, reference = found.groups()
                parts = S.image_parts(reference)
                lines.append(
                    f"[Image: {caption or 'source figure'}"
                    + ("; enlarged panel region]" if parts and parts[1] else "]")
                )
            rows.append(f"[Slide {number}]\n" + "\n".join(lines))
        return "\n\n".join(rows)

    def _learner_review(
        self, model, effort, run: Run, deck: str, pool: Pool, emit: Events
    ) -> tuple[S.LearnerReview | None, list[Image]]:
        """A separate call with visible content only; source context never enters it."""
        images: list[Image] = []
        sees = vision_model(self.gateway, model, effort)
        learner_model = model
        renderer = getattr(self, "renderer", None)
        if sees and renderer is not None:
            eye, _ = sees
            learner_model = eye
            try:
                snapshot = renderer.capture(
                    self._plain_built(deck, run, pool, eye, effort), run.use
                )
                images = [
                    Image.from_path(renderer.file(snapshot["id"], slide["image"]), "image/png")
                    for slide in snapshot["slides"]
                ]
            except Exception as error:  # noqa: BLE001 - text remains a valid learner view
                logger.info("Rendering for the independent learner failed: %s", error)
                self._science_limit(
                    run, f"The independent learner saw text only; rendering failed: {error}"
                )
        try:
            self._count_model_call("science")
            learner, _ = self.gateway.complete_json(
                learner_model,
                S.LearnerReview,
                system=P.LEARNER_REVIEW.format(
                    brief=run.spec.brief or "(none)", audience=run.spec.audience
                ),
                prompt=P.with_preferences(
                    "Visible slides (notes are deliberately absent):\n\n"
                    + self._visible_slides(deck),
                    run.spec,
                ),
                images=images,
                effort="low",
                schema_at="end",
            )
            supplied = len(fmt.split_slides(deck))
            answers = (
                learner.question_and_contribution,
                learner.method_or_experiment,
                learner.figures_and_controls,
                learner.evidence_for_conclusions,
                learner.assumptions_limits_and_questions,
            )
            if any(not any(1 <= n <= supplied for n in answer.evidence) for answer in answers):
                raise ValueError("Learner evidence names a slide that was not supplied.")
            run.reviews.append(learner)
            emit({"type": "learner_review", **learner.model_dump(), "images": bool(images)})
            return learner, images
        except (ModelError, ValueError, LookupError, OSError) as error:
            logger.info("The independent learner review failed: %s", error)
            self.skipped.append(
                {
                    "step": "science",
                    "label": "Independent learner unavailable",
                    "reason": str(error),
                }
            )
            return None, images

    def _compare_learner(
        self, model, run: Run, deck: str, learner: S.LearnerReview, material: str
    ) -> S.CoverageReview | None:
        task = P.COVERAGE_COMPARISON + (
            "\nStudy map, image readings and coverage plan:\n"
            + self._study_text(run)
            + "\nIndependent learner reconstruction:\n"
            + learner.model_dump_json()
            + "\nDraft:\n"
            + deck
        )
        try:
            return self._json(
                model, S.CoverageReview, *material_request(material, task, run.spec), "low",
            )
        except ModelError as error:
            logger.info("Comparing the learner with the source failed: %s", error)
            self.skipped.append(
                {"step": "science", "label": "Learner comparison unavailable", "reason": str(error)}
            )
            return None

    def _revise_science(
        self,
        model,
        effort,
        run: Run,
        deck: str,
        pool: Pool,
        material: str,
        problems: list[S.ScientificProblem],
    ) -> str:
        """Rewrite affected slides, allowing one slide to become several; structural omissions
        may still ask for the whole deck."""

        slides = fmt.split_slides(deck)
        def rejected(reason: str) -> str:
            self.skipped.append(
                {
                    "step": "science",
                    "label": "Targeted scientific revision rejected",
                    "reason": reason,
                }
            )
            return deck

        wanted = {number for problem in problems for number in problem.slides}
        if any(number > len(slides) for number in wanted):
            return rejected("the review named a slide outside the draft")
        structural = any(problem.route == "deck" for problem in problems) or not wanted
        try:
            if structural:
                selected, numbers, labels = slides, list(range(1, len(slides) + 1)), "all"
            else:
                numbers = sorted(wanted)
                selected = [slides[number - 1] for number in numbers]
                labels = ", ".join(map(str, numbers))
            task = P.STUDY_REVISE.format(
                problems="\n".join(
                    "- " + p.model_dump_json()
                    for p in problems
                ),
                slides="\n\n".join(
                    f"<!-- replaces slide {number} -->\nSlide {number}:\n{text}"
                    for number, text in zip(numbers, selected, strict=True)
                ),
            )
            task += (
                f"\nThese are deck slide numbers {labels}. When one input slide becomes several "
                "output slides, start every output group with its `<!-- replaces slide N -->` "
                "marker."
            )
            task += "\nStudy map, image readings and coverage plan:\n" + self._study_text(run)
            if run.reviewed:
                task += (
                    "\n" + P.APPROVED_OUTLINE
                    + "\nReturn exactly one replacement slide for each selected position."
                )
            revised = _unfence(
                self._text(model, *material_request(material, task, run.spec), "low", 24_000)
            )
        except ModelError as error:
            logger.info("The targeted scientific revision failed: %s", error)
            return rejected(str(error))

        pieces = fmt.split_slides(revised)
        if structural:
            if len(pieces) < 2 or (run.reviewed and len(pieces) != len(slides)):
                return rejected(f"revision returned {len(pieces)} slides")
            prior_errors = run.format_repair_errors
            candidate = self.fix_format(model, effort, run, revised, _no_events)
            if not source_images_kept(deck, candidate):
                run.format_repair_errors = prior_errors
                return rejected("the revision dropped a source image")
            return candidate

        groups: dict[int, list[str]] = {}
        marker = re.compile(r"\A<!--\s*replaces slide (\d+)\s*-->\n?")
        if any(marker.match(piece) for piece in pieces):
            current = None
            for piece in pieces:
                found = marker.match(piece)
                if found:
                    current = int(found.group(1))
                    piece = piece[found.end():]
                if current not in wanted:
                    return rejected("a replacement group names an unrequested slide")
                groups.setdefault(current, []).append(fmt.strip_labels(piece))
            if set(groups) != wanted:
                return rejected("the revision did not replace every requested slide")
        elif len(pieces) == len(selected):
            groups = {
                number: [fmt.strip_labels(piece)]
                for number, piece in zip(numbers, pieces, strict=True)
            }
        elif len(numbers) == 1 and pieces:
            groups = {numbers[0]: [fmt.strip_labels(piece) for piece in pieces]}
        else:
            return rejected(
                f"revision returned {len(pieces)} slides for {len(selected)} inputs "
                "without complete replacement markers"
            )
        if any(
            not source_images_kept(slides[number - 1], "\n\n---\n\n".join(replacements))
            for number, replacements in groups.items()
        ):
            return rejected("the revision dropped a source image")

        out: list[str] = []
        for number, slide in enumerate(slides, 1):
            out.extend(groups.get(number, [slide]))
        return self.fix_format(model, effort, run, "\n\n---\n\n".join(out), _no_events)

    def scientific_gate(
        self, model, effort, run: Run, deck: str, pool: Pool, material: str, emit: Events
    ) -> str:
        """Source-aware coverage, an independent slide-only learner, comparison and code gates.

        Content is settled here, before design. One full review finds the problems; a targeted
        revision fixes them; a re-check asks only whether those problems are gone. A second
        full review of a revised deck always finds something new, so it never settles: what
        the re-check still finds is delivered as open problems. Image problems are repaired
        by code (``S.heal_images``); nothing here stops the build. An unavailable review is
        never ready."""

        assert run.study is not None
        step, label = "science", "Reviewing the scientific explanation"
        cover = run.plan[0].heading if run.plan else None
        deck = S.apply_source_cover(deck, pool.items, title=cover)
        self._stage(emit, step, label, "running", "checking the slides against the material")
        if run.reviews is None:
            run.reviews = []
        try:
            review: S.ScientificReview | None = self.scientific_review(model, run, deck, material)
        except ModelError as error:
            logger.info("The scientific review failed: %s", error)
            review = None
            self.skipped.append(
                {"step": step, "label": "Scientific review unavailable", "reason": str(error)}
            )
        if review is not None:
            run.reviews.append(review)
            emit({"type": "review", "round": 1, **review.model_dump()})
        self._progress(emit, step, label, "a fresh reader is reading only the slides")
        learner, _images = self._learner_review(model, effort, run, deck, pool, emit)
        if learner is not None:
            self._progress(emit, step, label, "comparing what the reader understood")
        comparison = (
            self._compare_learner(model, run, deck, learner, material)
            if learner is not None
            else None
        )
        if comparison is not None:
            run.reviews.append(comparison)
            emit({"type": "coverage_comparison", "round": 1, **comparison.model_dump()})
        found: list[S.ScientificProblem] = list(review.problems if review is not None else [])
        if comparison is not None:
            found += comparison.coverage_problems + comparison.learner_problems
        code = self._science_findings(run, deck, pool)
        unavailable = review is None or learner is None or comparison is None
        verdicts_ready = bool(
            review and review.verdict == "ready" and comparison and comparison.verdict == "ready"
        )
        still_open = found
        fixed = 0
        if found or code:
            count = len(found) + len(code)
            self._progress(
                emit, step, label, f"fixing {count} problem{'' if count == 1 else 's'}"
            )
            prior_errors = run.format_repair_errors
            revised = self._revise_science(
                model, effort, run, deck, pool, material, [*found, *code]
            )
            revised = S.apply_source_cover(revised, pool.items, title=cover)
            if not _matches_plan(run, fmt.split_slides(revised)):
                revised = deck
                run.format_repair_errors = prior_errors
            if found and revised != deck:
                self._progress(emit, step, label, "checking that the fixes worked")
                still_open = self._recheck_science(model, run, revised, material, found)
                fixed = len(found) - len(still_open)
            deck = revised
            code = self._science_findings(run, deck, pool)
        deck, healed = S.heal_images(deck, run.figure_readings or [], pool.items)
        if healed:
            for note in healed:
                self._science_limit(run, note)
            code = self._science_findings(run, deck, pool)
        for problem in code:
            self._science_limit(run, problem.problem)
        if unavailable:
            run.review_status = "unavailable"
            self._science_limit(
                run, "A scientific review could not run; the deck is not review-ready."
            )
            detail = "source review unavailable"
            if review is not None and learner is None:
                detail = "independent learner unavailable"
            elif review is not None and comparison is None:
                detail = "learner comparison unavailable"
            self._stage(emit, step, label, "done", detail)
        elif still_open or code or (not found and not verdicts_ready):
            run.review_status = "open_problems"
            for problem in still_open:
                self._science_limit(run, f"Review: {problem.where}: {problem.problem}")
            if not found and not verdicts_ready:
                self._science_limit(
                    run, "The review did not mark the deck ready, but named no problem to fix."
                )
            if learner and learner.unexplained:
                self._science_limit(
                    run,
                    "Independent learner could not explain: " + "; ".join(learner.unexplained),
                )
            left = len(still_open) + len(code)
            done = f"{fixed} fixed, " if fixed else ""
            self._stage(emit, step, label, "done", f"{done}{left} open problems recorded")
        else:
            run.review_status = "ready"
            self._stage(emit, step, label, "done", f"ready, {fixed} fixed" if fixed else "ready")
        return deck

    def _science_code(self, run: Run, deck: str, pool: Pool) -> list[str]:
        return list(dict.fromkeys(p.problem for p in self._science_findings(run, deck, pool)))

    def _science_findings(self, run: Run, deck: str, pool: Pool) -> list[S.ScientificProblem]:
        """Every code check of a scientific deck; cover metadata is noted, never revised."""

        assert run.study is not None
        for problem in S.cover_problems(deck, pool.items):
            self._science_limit(run, problem)
        return S.code_findings(
            run.study, run.figure_readings or [], deck, pool.items, plan=run.coverage_plan
        )

    def _recheck_science(
        self, model, run: Run, deck: str, material: str, problems: list[S.ScientificProblem]
    ) -> list[S.ScientificProblem]:
        """Which of the review's problems the revision did not resolve. Unknown counts as open."""

        task = P.SCIENCE_RECHECK.format(
            problems="\n".join(
                f"{n}. {p.where}: {p.problem} — fix: {p.fix}" for n, p in enumerate(problems, 1)
            ),
            deck=deck,
        )
        try:
            check = self._json(
                model, S.ProblemCheck, *material_request(material, task, run.spec), "low",
            )
        except ModelError as error:
            logger.info("The scientific re-check failed: %s", error)
            self.skipped.append(
                {
                    "step": "science",
                    "label": "Scientific re-check unavailable",
                    "reason": str(error),
                }
            )
            return problems
        resolved = {row.n for row in check.problems if row.resolved}
        return [p for n, p in enumerate(problems, 1) if n not in resolved]
