"""The studio: reports and decks made the way a strong analyst and writer would make them.

read → memo (what is worth saying) → storyline (titles first, with a critic) → draft (the whole
thing at once) → format fix → verify (claims against passages, surgical fixes) → assemble.

About eight model calls for a deck instead of dozens, each with the whole picture in view.
See docs/OUTPUTS.md and docs/STUDIO_FORMAT.md.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from threading import Lock
from typing import Any

from gunther import usage
from gunther.checks import read_verdicts
from gunther.llm import ModelError, ModelInfo
from gunther.llm import unfence as _unfence
from gunther.model_profiles import Effort
from gunther.output_document import assemble_output
from gunther.output_types import DECK_USES, SHAPES, output_type_for
from gunther.outputs import (
    BuiltOutput,
    Draft,
    Events,
    Notes,
    OutputAgents,
    Planned,
    Pool,
    Spec,
    _marked,
    heading_of,
    split_document,
)
from gunther.slide_render import digest
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.drafting import StudioDrafting
from gunther.studio.look import look, vision_model
from gunther.studio.material import StudioMaterial, tokens
from gunther.studio.requests import WORKERS, material_request
from gunther.studio.stages import Checkpoint, checkpoint_stage, stage_event
from gunther.studio.state import (
    DeckStory,
    Memo,
    ReportStory,
    Run,
    _matches_plan,
    _no_events,
    _validate_count,
    _validate_storyline,
)
from gunther.studio.study import StudioStudy
from gunther.studio.verification import StudioVerification

logger = logging.getLogger(__name__)

# Every slide of a deck is also designed as HTML (studio/design.py); False: the template draws.
DESIGN_SLIDES = True


class StudioAgents(StudioMaterial, StudioStudy, StudioDrafting, StudioVerification, OutputAgents):
    """Makes a report or a deck with the Studio method."""

    def __init__(
        self,
        gateway,
        limits=None,
        *,
        use: str | None = None,
        renderer=None,
        design: bool | None = None,
        asset_reader=None,
        reusable_figures: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(gateway, limits)
        self.design = DESIGN_SLIDES if design is None else design
        # Draws slides with the real renderer for the "look" step; None: that step is skipped.
        self.renderer = renderer
        self.asset_reader = asset_reader or getattr(renderer, "asset_reader", None)
        self.scientific_context: dict[str, Any] | None = None
        self.use = use if use in DECK_USES else None
        self.trail: list[dict[str, Any]] = []
        self._started: dict[str, float] = {}
        self._active_stages: list[str] = []
        self._stage_calls: dict[str, int] = {}
        self._counter_lock = Lock()
        self.reusable_figures = list(reusable_figures or [])

    # Plumbing -----------------------------------------------------------------------------

    def _stage(self, emit: Events, step: str, label: str, state: str, detail: str = "") -> None:
        event = stage_event(step, label, state, detail or None)
        if state == "running":
            self._started[step] = time.monotonic()
            self._active_stages.append(step)
            self._stage_calls.setdefault(step, 0)
        if state in ("done", "failed"):
            took = round(time.monotonic() - self._started.get(step, time.monotonic()), 3)
            event["seconds"] = took
            calls = self._stage_calls.get(step, 0)
            event["modelCalls"] = calls
            self.trail.append(
                {
                    "step": step,
                    "state": state,
                    "detail": detail,
                    "seconds": took,
                    "modelCalls": calls,
                }
            )
            if self._active_stages and self._active_stages[-1] == step:
                self._active_stages.pop()
            logger.info("Studio %s %s in %ss: %s", step, state, took, detail[:120])
        emit(event)

    def _progress(self, emit: Events, step: str, label: str, detail: str) -> None:
        """What a running stage is doing now, shown beside its label; its clock keeps going."""

        emit(stage_event(step, label, "running", detail))

    def _count_model_call(self, stage: str | None = None) -> None:
        stage = stage or (self._active_stages[-1] if self._active_stages else None)
        if stage is None:
            return
        with self._counter_lock:
            self._stage_calls[stage] = self._stage_calls.get(stage, 0) + 1

    def _json(self, model, schema, system, prompt, effort):
        if self._active_stages:
            self._count_model_call(self._active_stages[-1])
        # A reply in the wrong shape (twice, inside the gateway) is asked for once more from
        # scratch before the step fails: one bad reply should not cost the whole build.
        try:
            result, _ = self.gateway.complete_json(
                model, schema, system=system, prompt=prompt, effort=effort, schema_at="end"
            )
        except ModelError as error:
            if "could not read" not in str(error):
                raise
            logger.info("Asking again from scratch after an unreadable reply")
            self._count_model_call()
            result, _ = self.gateway.complete_json(
                model, schema, system=system, prompt=prompt, effort=effort, schema_at="end"
            )
        return result

    def _text(self, model, system, prompt, effort, max_tokens=16_000, *, on_text=None) -> str:
        if self._active_stages:
            self._count_model_call(self._active_stages[-1])
        # A thinking model spends its budget thinking first: never ask for less than this.
        room = max(max_tokens, 16_000)
        try:
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort=effort, max_tokens=room,
                on_text=on_text,
            )
        except ModelError as error:
            if "ran out of room" not in str(error) or effort in (None, "off", "low"):
                raise
            logger.info("Out of room while thinking; asking again with less thinking")
            self._count_model_call()
            completion = self.gateway.complete(
                model, system=system, messages=prompt, effort="low", max_tokens=room,
                on_text=on_text,
            )
        return completion.text

    @staticmethod
    def _checked_progress(run: Run, drafts: list[Draft], *, refit_pending: bool) -> dict[str, Any]:
        return {
            "slides": [{"text": draft.text, "verdicts": draft.verdicts} for draft in drafts],
            "limitations": list(run.limitations or []),
            "review_status": run.review_status,
            **({"refit_pending": True} if refit_pending else {}),
        }

    # 4. Designing -------------------------------------------------------------------------

    def design_deck(
        self, model, run: Run, drafts: list[Draft], emit: Events, *, only: list[int]
    ) -> list[Draft]:
        """The deck's style, then every slide designed on its own. The words are final by now:
        a design may not change one. It never fails a build: whatever goes wrong, the slides
        stay as they are and the template draws them."""

        label = "Designing the slides"
        self._stage(emit, "design", label, "running")
        try:
            slides = [d.text for d in drafts]
            first = fmt.parse_slide(slides[0])
            style = run.spec.deck_style
            if style not in fmt.STYLES:
                style = D.pick_style(
                    self, model, title=first.title, subtitle=first.body[0] if first.body else "",
                    brief=run.spec.brief, audience=run.spec.audience,
                )
            designed, count, notes = D.design_slides(
                self,
                model,
                D.apply_style(slides, style),
                style,
                workers=WORKERS,
                only=only,
                progress=lambda done, total: self._progress(
                    emit, "design", label, f"{done} of {total} slides designed"
                ),
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
    def _prepare(
        self,
        model: ModelInfo,
        effort: Effort | None,
        spec: Spec,
        pool: Pool,
        notes: Notes,
        emit: Events,
        *,
        purpose: str | None = None,
        fixed_outline: Sequence[Planned] | None = None,
        scope: Mapping[str, Any] | None = None,
        web: bool = False,
        resume: dict[str, Any] | None = None,
        checkpoint: Checkpoint | None = None,
    ) -> tuple[Run, str, dict[str, Any], dict[str, Any]]:
        """Read and understand the material for a build or a standalone Understand."""

        if not (pool.items or notes.knowledge or notes.discussions):
            raise ModelError("There is no material to work from: choose some sources first.")
        use = (self.use or "talk") if spec.kind == "slides" else ""
        plan = tuple(Planned(p.heading.strip(), p.goal, id=p.id) for p in fixed_outline or ())
        ids = [p.id for p in plan if p.id]
        if len(set(ids)) != len(ids) or any(len(p.heading.splitlines()) != 1 for p in plan):
            raise ModelError("Approved outline headings must be single lines with unique part IDs.")
        run = Run(spec, use, plan=plan)
        printed = S.pool_print(pool.items)
        resumed = resume or {}
        eye = vision_model(self.gateway, model, effort) if spec.kind == "slides" else None
        note_inputs = asdict(notes)
        if not notes.key_results:
            note_inputs.pop("key_results")  # preserve checkpoints made before key results
        material_key = digest([
            printed, scope, note_inputs, model.ref, effort, web, spec.kind,
            [eye[0].ref, eye[1]] if eye else None,
        ])
        request_key = digest([
            asdict(spec), run.use, purpose, [(p.id, p.heading, p.goal) for p in plan],
        ])
        # A changed request can keep its reading of the same material, but not the draft.
        same_material = resumed.get("material_key") == material_key
        studied = resumed if same_material else {}
        saved = studied if resumed.get("request_key") == request_key else {}
        # A figure's reading is kept for that figure, whatever else changed.
        self.reusable_figures = [*self.reusable_figures, *(resumed.get("figure_readings") or [])]
        state: dict[str, Any] = {
            "passages": len(pool),
            "pool": printed,
            "kind": spec.kind,
            "brief": spec.brief,
            "material_key": material_key,
            "request_key": request_key,
        }
        # The purpose is the model's decision, from the brief and a short look at the material.
        if spec.kind == "slides":
            with checkpoint_stage(emit, state, checkpoint, "purpose") as publish:
                if purpose in ("explain_study", "general"):
                    run.purpose = S.Purpose(purpose=purpose, reason="Kept the saved purpose")
                elif saved.get("purpose"):
                    run.purpose = S.Purpose.model_validate(saved["purpose"])
                else:
                    self._stage(publish, "purpose", "Understanding the request", "running")
                    run.purpose = self._json(
                        model,
                        S.Purpose,
                        P.PURPOSE,
                        f"Brief: {spec.brief or '(none)'}\nThis deck's purpose so far: "
                        + json.dumps(resumed.get("previous_purpose"))
                        + "\nMaterial overview:\n"
                        + S.overview(pool.items),
                        "low",
                    )
                    self._stage(
                        publish, "purpose", "Understanding the request", "done", run.purpose.reason,
                    )
                state["purpose"] = run.purpose.model_dump()
            emit({"type": "purpose", **state["purpose"]})
        with checkpoint_stage(emit, state, checkpoint, "read") as publish:
            material = self.read(model, run, pool, notes, publish, kept=saved.get("read"))
            if run.read_digest:
                state["read"] = {"notes": self._notes_text(notes), "digest": run.read_digest}
        if run.purpose and run.purpose.purpose == "explain_study":
            run.limitations = []
            with checkpoint_stage(emit, state, checkpoint, "study") as publish:
                kept = (
                    S.StudyMap.model_validate(studied["study_map"])
                    if studied.get("study_map")
                    else None
                )
                if kept and not S.map_problems(kept, pool.items):
                    run.study = kept
                else:
                    run.study = self.study_map(model, effort, run, pool, material, publish)
                state["study_map"] = run.study.model_dump()
            with checkpoint_stage(emit, state, checkpoint, "figures") as publish:
                run.figure_readings = self.read_figures(model, effort, run, pool, publish)
                warned = [item.ref for item in pool.items if S.warnings_of(item)]
                if warned:
                    self._science_limit(
                        run,
                        f"{len(warned)} passages have extraction warnings; a value from them is "
                        "stated only when clean text or a figure reading confirms it.",
                    )
                state["figure_readings"] = [r.model_dump() for r in run.figure_readings]
                state["limitations"] = run.limitations
            coverage_key = state["coverage_key"] = digest(self._study_text(run))
            with checkpoint_stage(emit, state, checkpoint, "coverage") as publish:
                kept_plan = (
                    S.CoveragePlan.model_validate(studied["coverage_plan"])
                    if studied.get("coverage_plan") and studied.get("coverage_key") == coverage_key
                    else None
                )
                if kept_plan and not S.coverage_problems(kept_plan, pool.items):
                    run.coverage_plan = kept_plan
                    self._stage(
                        publish, "coverage", "Planning source coverage", "done", "kept from before"
                    )
                else:
                    run.coverage_plan = self.coverage_plan(
                        model, effort, run, pool, material, publish
                    )
                state["coverage_plan"] = run.coverage_plan.model_dump()
            self.scientific_context = {**state, "purpose": "explain_study"}
        else:
            with checkpoint_stage(emit, state, checkpoint, "memo") as publish:
                if saved.get("memo"):
                    run.memo = Memo.model_validate(saved["memo"])
                    self._stage(
                        publish, "memo", "Finding what is worth saying", "done", "kept from before"
                    )
                else:
                    run.memo = self.memo(model, effort, run, material, publish)
                state["memo"] = run.memo.model_dump()
        return run, material, state, saved

    def _make_direct(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events,
        state: dict, saved: dict, checkpoint: Checkpoint | None, fallback_title: str,
    ) -> tuple[BuiltOutput, int]:
        spec, item = run.spec, run.output_type
        shape = SHAPES[item.shape]
        units = f"{shape.unit_label}s"
        # Short and standard are smaller practice sets within the shape's hard limit.
        divisor = {"short": 4, "standard": 2, "long": 1}[spec.length or "standard"]
        target = max(1, item.part_limit // divisor)
        header = shape.preamble(item.id)
        task = P.DIRECT_WRITE.format(
            output_type=item.label, brief=spec.brief or "(none)", audience=spec.audience,
            length=f"Aim for {target} {units} if the brief gives no count. "
            f"Use at most {item.part_limit} {units}.",
            language=P.language_instruction(spec.language, run.model_language),
            recipe=item.write, memo=self._memo_text(run.memo) if run.memo else "",
            truth=P.TRUTH, header=f"Start with {header} on its own line." if header else "",
        )
        if run.plan:
            task += "\nUse these approved headings in order:\n" + "\n".join(
                f"- {part.heading}" for part in run.plan
            )
        system, prompt = material_request(material, task)

        def problems(text: str) -> list[str]:
            found = item.direct_problems(text)
            if not _matches_plan(run, shape.split(text)[1]):
                found.append("Keep the approved headings in order.")
            return found

        label = f"Writing {item.label.lower()}"
        text = saved.get("direct_text")
        if text is None:
            self._stage(emit, "write", label, "running")
            text = _unfence(self._text(model, system, prompt, effort, max_tokens=24_000))
            self._stage(emit, "write", label, "done")
        else:
            self._stage(emit, "write", label, "done", "kept from before")
        with checkpoint_stage(emit, state, checkpoint, "format") as publish:
            label = f"Checking the {shape.name} format"
            self._stage(publish, "format", label, "running")
            found = problems(text)
            if found:
                repair = P.DIRECT_FIX.format(
                    problems="\n".join(f"- {p}" for p in found), draft=text,
                )
                try:
                    text = _unfence(self._text(
                        model, system, f"{prompt}\n\n{repair}", effort, max_tokens=24_000,
                    ))
                except ModelError as error:
                    self._stage(publish, "format", label, "failed", str(error))
                    raise ModelError(f"{found[0]} Format repair failed: {error}") from error
                found = problems(text)
            if found:
                self._stage(publish, "format", label, "failed", found[0])
                raise ModelError("\n".join(found))
            state["direct_text"] = text
            self._stage(publish, "format", label, "done")

        preamble, parts = shape.split(text)
        with checkpoint_stage(emit, state, None, "verify") as publish:
            drafts = self.verify(model, run, parts, pool, publish, material)
        label = f"Assembling the {shape.name}"
        self._stage(emit, "assemble", label, "running")
        outline = list(run.plan) or [Planned(heading_of(draft.text)) for draft in drafts]
        title = spec.title or (run.memo.title if run.memo else "") or fallback_title or item.label
        built = assemble_output(spec, title, preamble, drafts, pool, outline, model, effort)
        self._stage(emit, "assemble", label, "done", f"{len(drafts)} {units}")
        return built, len(drafts)

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
        purpose: str | None = None,
        fixed_outline: Sequence[Planned] | None = None,
        outline_approved: bool = True,
        scope: Mapping[str, Any] | None = None,
        web: bool = False,
        resume: dict[str, Any] | None = None,
        checkpoint: Checkpoint | None = None,
        **_ignored: Any,
    ) -> BuiltOutput:
        """Reuse completed stages only when their material and request still match."""

        if outline_approved and fixed_outline:
            output_type = output_type_for(spec.kind, spec.output_type)
            if error := output_type.part_limit_error(len(fixed_outline)):
                raise ModelError(error)
        emit = events or _no_events
        run, material, state, saved = self._prepare(
            model, effort, spec, pool, notes, emit, purpose=purpose, fixed_outline=fixed_outline,
            scope=scope, web=web, resume=resume, checkpoint=checkpoint,
        )
        if SHAPES[spec.kind].pipeline == "direct":
            built, count = self._make_direct(
                model, effort, run, pool, material, emit, state, saved, checkpoint, fallback_title,
            )
            return self._finish_build(run, pool, built, count)
        plan = run.plan
        same_science = (
            saved.get("coverage_key") == state.get("coverage_key")
            and saved.get("coverage_plan") == state.get("coverage_plan")
        )
        reuse_story = bool(
            saved.get("storyline")
            and (same_science if run.study else saved.get("memo") == state.get("memo"))
        )
        with checkpoint_stage(emit, state, checkpoint, "story") as publish:
            if reuse_story:
                schema = DeckStory if spec.kind == "slides" else ReportStory
                run.story = schema.model_validate(saved["storyline"])
                _validate_storyline(run, run.story)
                self._stage(publish, "story", "Planning the storyline", "done", "kept from before")
            else:
                run.story = self.storyline(model, effort, run, material, publish)
            state["storyline"] = run.story.model_dump()
        looked: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        if spec.kind == "slides":
            reused_draft = bool(run.study and reuse_story and saved.get("draft"))
            with checkpoint_stage(emit, state, checkpoint, "write", "science") as publish:
                if reused_draft:
                    text = saved["draft"]
                    run.limitations = state["limitations"] = list(saved.get("limitations") or [])
                    run.review_status = saved.get("review_status") or "unavailable"
                    run.reviews = []
                    kinds = {
                        "scientific": S.ScientificReview,
                        "learner": S.LearnerReview,
                        "coverage": S.CoverageReview,
                    }
                    for saved_review in saved.get("reviews") or []:
                        kind = kinds.get(saved_review.get("kind"))
                        if kind is not None and saved_review.get("review") is not None:
                            run.reviews.append(kind.model_validate(saved_review["review"]))
                    self._stage(publish, "write", "Writing the slides", "done", "kept from before")
                    self._stage(
                        publish,
                        "science",
                        "Reviewing the scientific explanation",
                        "done",
                        "kept from before",
                    )
                else:
                    text = self.write_deck(model, effort, run, pool, material, emit)
                    text = self.fix_format(model, effort, run, text, emit)
                    if run.study:
                        # A resumable draft includes the scientific review of its source content.
                        text = self.scientific_gate(
                            model, effort, run, text, pool, material, publish,
                        )
                if run.study:
                    state["draft"] = text
                    state["reviews"] = []
                    for review in run.reviews or []:
                        kind = (
                            "scientific"
                            if isinstance(review, S.ScientificReview)
                            else "learner"
                            if isinstance(review, S.LearnerReview)
                            else "coverage"
                        )
                        state["reviews"].append({"kind": kind, "review": review.model_dump()})
                    state["review_status"] = run.review_status
                    self.scientific_context.update(
                        {
                            "coverage_plan": run.coverage_plan.model_dump()
                            if run.coverage_plan
                            else None,
                            "reviews": state["reviews"],
                            "review_status": run.review_status,
                        }
                    )
            checked = saved.get("checked") if reused_draft else None
            needs_refit = not checked or checked.get("refit_pending", False)
            verify_checkpoint = checkpoint if run.study else None
            with checkpoint_stage(emit, state, verify_checkpoint, "verify") as publish:
                if checked:
                    # The claims of this same draft were checked before the run stopped.
                    drafts = [
                        Draft(
                            row["text"],
                            read_verdicts(row, _marked(row["text"]).body)
                            if row.get("verdicts") is not None or row.get("issues") is not None
                            else None,
                        )
                        for row in checked["slides"]
                    ]
                    run.limitations = list(checked.get("limitations") or [])
                    state["limitations"] = run.limitations
                    run.review_status = checked.get("review_status") or run.review_status
                    self._stage(
                        publish,
                        "verify",
                        "Checking every claim against its source",
                        "done",
                        "kept from before",
                    )
                else:
                    drafts = self.verify(
                        model, run, fmt.split_slides(text), pool, publish, material,
                    )
                if run.study:
                    state["checked"] = self._checked_progress(
                        run, drafts, refit_pending=needs_refit,
                    )
            if needs_refit:
                with checkpoint_stage(emit, state, verify_checkpoint):
                    drafts = self._fit_checked(model, effort, run, drafts, pool, material)
                    if run.study:
                        state["checked"] = self._checked_progress(run, drafts, refit_pending=False)
            checked = [self._chart_fallback(d, i) for i, d in enumerate(drafts)]
            if self.design and checked:
                only = [i for i, draft in enumerate(drafts) if draft is checked[i]]
                checked = self.design_deck(model, run, checked, emit, only=only)
            drafts = checked
            title = (
                (fmt.parse_slide(drafts[0].text).title or "Slide 1")
                if drafts else (run.story.title or fallback_title)
            )

            def assemble_deck(parts: list[Draft]) -> BuiltOutput:
                _validate_count(run, run.story, len(parts), "assembled deck")
                if not _matches_plan(run, [d.text for d in parts]):
                    raise ModelError("The deck no longer follows the approved outline.")
                planned = [
                    Planned(slide.title or f"Slide {n}", layout=slide.layout)
                    for n, slide in enumerate((fmt.parse_slide(d.text) for d in parts), 1)
                ]
                if plan:
                    planned = [
                        replace(p, layout=s.layout) for p, s in zip(plan, planned, strict=True)
                    ]
                return assemble_output(spec, title, "", parts, pool, planned, model, effort)

            with checkpoint_stage(emit, state, None):
                built = assemble_deck(drafts)
                try:
                    built, looked, snapshot = look(
                        self, built, drafts, model=model, effort=effort, use=run.use, pool=pool,
                        assemble=assemble_deck, emit=emit, unfence=_unfence, material=material,
                    )  # fmt: skip
                except Exception:  # noqa: BLE001 - looking never fails a build
                    logger.exception("The look at the slides failed")
                    reason = "The slide review did not finish."
                    self.skipped.append(
                        {"step": "look", "label": "Slide review unavailable", "reason": reason}
                    )
                    self._stage(emit, "look", "Looking at the slides", "failed", reason)
            self._record_slide_format(run, built)
        else:
            text = self.write_report(model, effort, run, pool, material, emit)
            preamble, sections = split_document(text, "report")
            _validate_count(run, run.story, len(sections), "written report")
            with checkpoint_stage(emit, state, None, "verify") as publish:
                drafts = self.verify(model, run, sections, pool, publish, material)
            with checkpoint_stage(emit, state, None):
                drafts = [self._chart_fallback(d, i) for i, d in enumerate(drafts)]
                outline = list(plan) or [
                    Planned(re.sub(r"^##\s*", "", d.text.splitlines()[0]).strip()[:160])
                    for d in drafts
                ]
                title = run.story.title or fallback_title
                built = assemble_output(spec, title, preamble, drafts, pool, outline, model, effort)
                count = len(split_document(built.content, "report")[1])
                _validate_count(run, run.story, count, "assembled report")
                if count != len(outline):
                    raise ModelError("The assembled report does not match its outline.")
        return self._finish_build(run, pool, built, len(drafts), looked, snapshot)

    def _finish_build(self, run, pool, built, count, looked=None, snapshot=None) -> BuiltOutput:
        return replace(
            built,
            approach=self._saved_approach(run, pool, count) | {
                "question": run.spec.brief,
                "ref_map": {str(ref): n for n, ref in enumerate(built.pool_refs, 1)},
            },
            skill={
                "name": "studio",
                "version": 1,
                "skipped": self.skipped,
                "trail": self.trail,
                **({"look": looked} if looked else {}),
                **({"renderCheck": snapshot} if snapshot else {}),
            },
        )

    @staticmethod
    def _saved_approach(run: Run, pool: Pool, pages: int | None = None) -> dict[str, Any]:
        return {
            "method": "studio",
            # The fields an approach has everywhere (Approach.saved_as reads them).
            "question": run.spec.brief,
            "purpose": run.purpose.purpose if run.purpose else "general",
            "purpose_reason": run.purpose.reason if run.purpose else None,
            # A scientific deck's argument and checks; absent on every other artifact.
            "study_map": run.study.model_dump() if run.study else None,
            "coverage_plan": run.coverage_plan.model_dump() if run.coverage_plan else None,
            "figure_readings": [r.model_dump() for r in run.figure_readings or []]
            if run.study
            else None,
            "limitations": (run.limitations or []) if run.study else None,
            "review_status": run.review_status,
            "allowed_assets": S.allowed_assets(pool.items) if run.study else None,
            "pages": pages if run.spec.kind == "slides" else None,
            "supplements": [],
            "gaps": run.study.limitations
            if run.study
            else ([t.issue for t in run.memo.tensions] if run.memo else []),
            "use": run.use or None,
            "answer": run.study.contribution
            if run.study
            else (run.memo.answer if run.memo else ""),
            "memo": run.memo.model_dump() if run.memo else None,
            "storyline": run.story.model_dump() if run.story else None,
            "reviews": [r.model_dump() for r in run.reviews or []],
        }


__all__ = ["StudioAgents", "tokens"]
