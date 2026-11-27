"""The studio: reports and decks made the way a strong analyst and writer would make them.

read → memo (what is worth saying) → storyline (titles first, with a critic) → draft (the whole
thing at once) → format fix → verify (claims against passages, surgical fixes) → assemble.

About eight model calls for a deck instead of dozens, each with the whole picture in view.
See docs/OUTPUTS.md and docs/STUDIO_FORMAT.md.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from threading import Lock
from typing import Any
from uuid import uuid4

from gunther import usage
from gunther.checks import read_verdicts
from gunther.citations import check_citations
from gunther.llm import ModelError, ModelInfo
from gunther.llm import unfence as _unfence
from gunther.model_profiles import Effort
from gunther.output_document import assemble_output, number_output_part
from gunther.output_types import DECK_USES, SHAPES
from gunther.outputs import (
    BuiltOutput,
    Draft,
    Events,
    FirstWrite,
    Notes,
    OutputAgents,
    ParentWrite,
    Planned,
    Pool,
    Spec,
    WriteAuthority,
    _marked,
    heading_of,
    numbered_check,
    split_document,
)
from gunther.schemas import (
    PlanCountOut,
    PlanDraftOut,
    PlanLineInput,
    PlanOptionsOut,
    PlanPartOut,
    PlanProposalOut,
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
from gunther.studio.stages import Checkpoint, checkpoint_stage, decode_stage, stage_event
from gunther.studio.state import (
    DeckStory,
    DirectProposal,
    Memo,
    ProposedTopic,
    ReportStory,
    Run,
    StorySection,
    StorySlide,
    _matches_plan,
    _no_events,
    _validate_count,
)
from gunther.studio.study import StudioStudy
from gunther.studio.verification import StudioVerification

logger = logging.getLogger(__name__)

# Every slide of a deck is also designed as HTML (studio/design.py); False: the template draws.
DESIGN_SLIDES = True

PROGRESS_FORMAT = 2
PREPARE_VERSION = 1
STORY_VERSION = 2
WRITE_VERSION = 1
_STORY_FIELDS = ("story_key", "story_inputs", "storyline", "plan_draft")
_WRITE_FIELDS = (
    "write_key", "draft", "direct_text", "checked", "reviews", "review_status", "limitations",
)


@dataclass
class Prepared:
    run: Run
    material: str
    pool: Pool
    progress: dict[str, Any]


def _drop_stages(state: dict[str, Any], fields: Sequence[str]) -> None:
    for field in fields:
        state.pop(field, None)


def _saved_strings(value: Any) -> list[str]:
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return value.copy()
    return []


def _planned(row: PlanPartOut, refs: Sequence[int] | None = None) -> Planned:
    return Planned(
        row.heading, row.goal, row.layout or "", tuple(row.refs if refs is None else refs),
        row.id, row.notes,
    )


def _checked_row_refs(
    refs: Sequence[int], required: Sequence[int], valid: set[int],
) -> dict[str, list[int]]:
    checked_required = list(dict.fromkeys(ref for ref in required if ref in valid))
    return {
        "refs": list(dict.fromkeys(ref for ref in (*refs, *checked_required) if ref in valid)),
        "required_refs": checked_required,
    }


def _row_id() -> str:
    return f"p{uuid4().hex}"


def _topics(
    topics: Sequence[ProposedTopic], previous: Sequence[PlanLineInput],
) -> list[PlanLineInput]:
    available = {topic.id for topic in previous}
    result = []
    for topic in topics:
        identity = topic.id if topic.id in available else _row_id()
        available.discard(identity)
        result.append(PlanLineInput(id=identity, text=topic.text))
    return result


def _prepare_inputs(spec: Spec) -> dict[str, Any]:
    return {
        name: value for name, value in asdict(spec).items()
        if name not in {"title", "theme", "design_version", "deck_style"}
    }


def _story_options(options: PlanOptionsOut) -> dict[str, Any]:
    return options.model_dump(
        mode="json", exclude={"voices", "scope", "web", "theme", "design_version"},
    )


def _story_digest(
    prepared: Prepared, inputs: Any, plan: PlanDraftOut | None = None,
) -> str:
    item = prepared.run.output_type
    return digest([
        STORY_VERSION, prepared.progress["prepare_key"], item.id, item.plan, item.write,
        inputs, plan.model_dump(mode="json") if plan is not None else None,
    ])


def _write_authority(value: object, revision: str) -> WriteAuthority:
    if not isinstance(value, (FirstWrite, ParentWrite)):
        raise RuntimeError("Studio generation requires write authority.")
    if not revision or (isinstance(value, FirstWrite) and value.revision != revision):
        raise ValueError("Studio write authority must match the approved revision.")
    return value


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
                    brief=run.spec.brief, audience=run.spec.audience, spec=run.spec,
                )
            designed, count, notes = D.design_slides(
                self,
                model,
                D.apply_style(slides, style),
                style,
                workers=WORKERS,
                only=only,
                spec=run.spec,
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
    def prepare(
        self,
        model: ModelInfo,
        effort: Effort | None,
        spec: Spec,
        pool: Pool,
        notes: Notes,
        *,
        manifest: dict[str, Any],
        resume: dict[str, Any] | None,
        emit: Events,
        checkpoint: Checkpoint | None,
        purpose: str | None = None,
        legacy_outline: Sequence[Planned] = (),
    ) -> Prepared:
        """Understand the pinned material for this brief and retain completed stages."""

        if not (pool.items or notes.knowledge or notes.discussions):
            raise ModelError("There is no material to work from: choose some sources first.")
        self.scientific_context = None
        use = (self.use or "talk") if spec.kind == "slides" else ""
        run = Run(spec, use)
        eye = vision_model(self.gateway, model, effort) if use else None
        vision = [eye[0].ref, eye[1]] if eye else None
        note_inputs = asdict(notes)
        printed = S.pool_print(pool.items, full=True)
        manifest = json.loads(json.dumps(manifest))
        # Historical selection order and input records are used only by the old hash recipe.
        legacy_scope = manifest.pop("legacy_scope", None)
        legacy_web = manifest.pop("legacy_web", None)
        material_key = digest([manifest, printed, note_inputs])
        prepare_key = digest([
            PREPARE_VERSION, P.PURPOSE, P.NOTES, P.MEMO, P.STUDY_MAP, P.COVERAGE_PLAN,
            P.FIGURE_READ,
            material_key, _prepare_inputs(spec), use, purpose, model.ref, effort, vision,
            self.asset_reader is not None,
        ])
        resumed = resume if isinstance(resume, dict) else {}
        prep_fields = (
            "purpose", "read", "memo", "study_map", "figure_readings", "figure_limitations",
            "coverage_plan",
        )
        state: dict[str, Any] = {
            "format": PROGRESS_FORMAT,
            "material_key": material_key,
            "prepare_key": prepare_key,
            "manifest": manifest,
            "pool_refs": [item.ref for item in pool.items],
            "notes": json.loads(json.dumps(note_inputs)),
            "pool": printed,
        }
        legacy_matches = resumed.get("format") is None and self._legacy_prepare_matches(
            resumed, spec, notes, run.use, purpose, legacy_outline, manifest, pool, model.ref,
            effort, vision, legacy_scope, legacy_web,
        )
        same_material = resumed.get("material_key") == material_key
        if (
            resumed.get("format") == PROGRESS_FORMAT
            and same_material and resumed.get("prepare_key") == prepare_key
        ):
            fields = (*prep_fields, *_STORY_FIELDS, *_WRITE_FIELDS)
            state.update({key: resumed[key] for key in fields if key in resumed})
        elif legacy_matches:
            state.update({key: resumed[key] for key in prep_fields if key in resumed})

        def discard(*fields: str) -> None:
            _drop_stages(state, (*fields, *_STORY_FIELDS, *_WRITE_FIELDS))

        applying_preferences = same_material and resumed.get("prepare_key") != prepare_key
        if applying_preferences:
            self._stage(emit, "preferences", "Applying your preferences", "running")

        if use:
            with checkpoint_stage(emit, state, checkpoint, "purpose") as publish:
                run.purpose = decode_stage(S.Purpose, state.get("purpose"))
                if run.purpose is None:
                    discard(*prep_fields)
                    if purpose in ("explain_study", "general"):
                        run.purpose = S.Purpose(purpose=purpose, reason="Kept the saved purpose")
                    else:
                        label = "Understanding the request"
                        self._stage(publish, "purpose", label, "running")
                        run.purpose = self._json(
                            model, S.Purpose, P.PURPOSE,
                            P.with_preferences(
                                f"Brief: {spec.brief or '(none)'}\n"
                                "This deck's purpose so far: "
                                + json.dumps(resumed.get("previous_purpose")),
                                spec,
                            ) + "\n\nMaterial overview:\n" + S.overview(pool.items),
                            "low",
                        )
                        self._stage(publish, "purpose", label, "done", run.purpose.reason)
                    state["purpose"] = run.purpose.model_dump()
            emit({"type": "purpose", **state["purpose"]})

        with checkpoint_stage(emit, state, checkpoint, "read") as publish:
            read = state.get("read")
            extra = self._notes_text(notes)
            if not isinstance(read, dict) or read.get("notes") != extra or not isinstance(
                read.get("digest"), str,
            ):
                if read is not None or resumed.get("format") == PROGRESS_FORMAT:
                    discard("memo", "study_map", "coverage_plan")
                read = None
            material = self.read(model, run, pool, notes, publish, kept=read)
            state["read"] = {"notes": extra, "digest": run.read_digest}

        if run.purpose and run.purpose.purpose == "explain_study":
            with checkpoint_stage(emit, state, checkpoint, "study") as publish:
                run.study = decode_stage(S.StudyMap, state.get("study_map"))
                if run.study is None or S.map_problems(run.study, pool.items):
                    discard("coverage_plan")
                    run.study = self.study_map(model, effort, run, pool, material, publish)
                    state["study_map"] = run.study.model_dump()
            with checkpoint_stage(emit, state, checkpoint, "figures") as publish:
                figures = state.get("figure_readings")
                kept = (
                    [decode_stage(S.FigureReading, row) for row in figures]
                    if isinstance(figures, list) else None
                )
                figure_notes = state.get("figure_limitations")
                wanted = set(S.figure_inventory(pool.items))
                valid_figures = kept is not None and all(
                    reading is not None and reading.asset_id in wanted for reading in kept
                )
                if valid_figures and isinstance(figure_notes, list):
                    run.figure_readings = kept
                    run.limitations = _saved_strings(figure_notes)
                else:
                    # Literal figure readings can survive changes to the brief or planning model.
                    prior = resumed.get("figure_readings")
                    run.limitations = []
                    run.figure_readings = self.read_figures(
                        model, effort, run, pool, publish,
                        retained=[row for row in prior if isinstance(row, dict)]
                        if isinstance(prior, list) else (),
                    )
                    if warned := [item.ref for item in pool.items if S.warnings_of(item)]:
                        self._science_limit(
                            run,
                            f"{len(warned)} passages have extraction warnings; a value from "
                            "them is stated only when clean text or a figure reading confirms it.",
                        )
                    current = [reading.model_dump() for reading in run.figure_readings]
                    if not legacy_matches and (
                        current != figures or run.limitations != figure_notes
                    ):
                        discard("coverage_plan")
                    state["figure_readings"] = current
                    state["figure_limitations"] = list(run.limitations)
            with checkpoint_stage(emit, state, checkpoint, "coverage") as publish:
                run.coverage_plan = decode_stage(S.CoveragePlan, state.get("coverage_plan"))
                if legacy_matches and resumed.get("coverage_key") != digest(self._study_text(run)):
                    run.coverage_plan = None
                if run.coverage_plan is None or S.coverage_problems(run.coverage_plan, pool.items):
                    discard()
                    run.coverage_plan = self.coverage_plan(
                        model, effort, run, pool, material, publish,
                    )
                    state["coverage_plan"] = run.coverage_plan.model_dump()
                if "limitations" not in state:
                    state["limitations"] = list(run.limitations or [])
            self.scientific_context = self._saved_approach(run, pool)
        else:
            with checkpoint_stage(emit, state, checkpoint, "memo") as publish:
                run.memo = decode_stage(Memo, state.get("memo"))
                if run.memo is None:
                    discard()
                    run.memo = self.memo(model, effort, run, material, publish)
                    state["memo"] = run.memo.model_dump()
        if applying_preferences:
            self._stage(emit, "preferences", "Applying your preferences", "done")
        return Prepared(run, material, pool, state)

    @staticmethod
    def _legacy_prepare_matches(
        resume, spec, notes, use, purpose, outline, manifest, pool, model_ref, effort, vision,
        scope, web,
    ) -> bool:
        """Read the historical keys when their original inputs are still available."""

        if not resume.get("request_key") or spec.emphasize or spec.avoid:
            return False
        if scope is None and "scope" in manifest and "inputs" in manifest:
            scope = {"selection": manifest["scope"], "inputs": manifest["inputs"]}
        if not isinstance(scope, dict) or not {"selection", "inputs"} <= scope.keys():
            return False
        if not isinstance(web, bool):
            web_pass = manifest.get("web_pass")
            if web_pass not in ("off", "complete", "unavailable"):
                return False
            web = web_pass != "off"
        old_notes = asdict(notes)
        if not notes.key_results:
            old_notes.pop("key_results")
        old_spec = asdict(spec)
        old_spec.pop("emphasize")
        old_spec.pop("avoid")
        old_material = digest([
            S.pool_print(pool.items), scope, old_notes, model_ref, effort, web, spec.kind, vision,
        ])
        old_request = digest([
            old_spec, use, purpose, [(p.id, p.heading.strip(), p.goal) for p in outline],
        ])
        return resume.get("material_key") == old_material and resume["request_key"] == old_request

    @usage.job("outputs")
    def propose(
        self,
        prepared: Prepared,
        *,
        model: ModelInfo,
        effort: Effort | None,
        options: PlanOptionsOut,
        previous: PlanDraftOut | None,
        feedback: str,
        fixed_rows: tuple[PlanPartOut, ...],
        order_fixed: bool,
        coverage_fixed: bool = False,
        emit: Events,
        checkpoint: Checkpoint,
    ) -> PlanDraftOut:
        """Plan with the prepared evidence, before calling any content writer."""

        run = replace(prepared.run, reviewed=True)
        item = run.output_type
        if options.type != item.id:
            raise ValueError("The plan type must match the prepared output type.")
        state = prepared.progress
        request = {
            "options": _story_options(options),
            "model": model.ref,
            "effort": effort,
            "feedback": feedback,
            "previous": previous.model_dump(mode="json") if previous else None,
            "fixed_rows": [row.model_dump(mode="json") for row in fixed_rows],
            "order_fixed": order_fixed,
            "coverage_fixed": coverage_fixed,
        }
        kept, story = self._retained_plan(prepared)
        if kept and state.get("story_inputs") == request and (
            item.capabilities.pipeline == "direct" or story is not None
        ):
            return kept

        with checkpoint_stage(emit, state, checkpoint, "story") as publish:
            _drop_stages(state, _WRITE_FIELDS)
            if item.capabilities.pipeline == "direct":
                plan = self._propose_direct(
                    prepared, model, effort, options, previous, coverage_fixed, request, publish,
                )
                state.pop("storyline", None)
            else:
                previous_rows = previous.outline or [] if previous else []
                story = self.storyline(
                    model, effort, run, prepared.material, publish,
                    previous_outline=[_planned(row) for row in previous_rows],
                    instruction=feedback, preview_outline=False,
                    reviewed_inputs=json.dumps(
                        {"options": request["options"], "fixed_rows": request["fixed_rows"],
                         "order_fixed": order_fixed},
                        ensure_ascii=False,
                    ),
                )
                plan, story = self._project_story(
                    prepared, options, story, previous, fixed_rows, order_fixed,
                )
                state["storyline"] = story.model_dump()
                publish({
                    "type": "outline", "title": plan.proposal.title,
                    "sections": [row.model_dump(mode="json") for row in plan.outline or []],
                })
            state.update(story_key=_story_digest(prepared, request, plan), story_inputs=request,
                         plan_draft=plan.model_dump(mode="json"))
        return plan

    def _propose_direct(
        self, prepared, model, effort, options, previous, coverage_fixed, request, emit,
    ) -> PlanDraftOut:
        run = prepared.run
        item = run.output_type
        old = previous.proposal if previous else None
        count = old.count if old and old.count.mode != "auto" else PlanCountOut(
            mode="auto", value=item.automatic_target(options.length),
        )
        if problem := item.part_count_error(count.value):
            raise ModelError(problem)
        task = P.DIRECT_PROPOSAL.format(
            output_type=item.label, brief=run.spec.brief or "(none)", audience=run.spec.audience,
            language=P.language_instruction(run.spec.language, run.model_language),
            size=f"{count.mode}: {count.value} {item.capabilities.unit_label}s",
            recipe=item.write, memo=self._memo_text(run.memo) if run.memo else "",
        )
        task += "\n\nReview inputs:\n" + json.dumps(
            {key: request[key] for key in ("options", "previous", "feedback", "coverage_fixed")},
            ensure_ascii=False,
        )
        label = "Planning the coverage"
        self._stage(emit, "story", label, "running")
        proposal = self._json(
            model, DirectProposal, *material_request(prepared.material, task, run.spec), effort,
        )
        plan = PlanDraftOut(
            proposal=PlanProposalOut(
                title=options.title or proposal.title,
                summary=proposal.summary, angle=proposal.angle,
                coverage=old.coverage if coverage_fixed and old else _topics(
                    proposal.coverage, old.coverage if old else (),
                ),
                count=count, target_minutes=proposal.target_minutes,
            ),
            outline=None,
        )
        self._stage(emit, "story", label, "done", plan.proposal.title)
        return plan

    def _project_story(
        self, prepared: Prepared, options: PlanOptionsOut, story: DeckStory | ReportStory,
        previous: PlanDraftOut | None, fixed_rows: tuple[PlanPartOut, ...], order_fixed: bool,
    ) -> tuple[PlanDraftOut, DeckStory | ReportStory]:
        run = prepared.run
        item = run.output_type
        previous_rows = previous.outline or [] if previous else []
        prior = {row.id: row for row in previous_rows}
        fixed = {row.id: row for row in fixed_rows}
        known = prior | fixed
        ordered_rows = previous_rows or (fixed_rows if order_fixed else ())
        source = story.slides if isinstance(story, DeckStory) else story.parts(item.summary_box)
        valid_refs = set(prepared.pool.by_ref())
        suggested: dict[str, PlanPartOut] = {}
        source_units = []
        summary_id = None
        for index, unit in enumerate(source):
            if item.summary_box and index == 0:
                identity = summary_id = ordered_rows[0].id if ordered_rows else _row_id()
            else:
                identity = unit.id if unit.id in known and unit.id not in suggested else _row_id()
            old = known.get(identity)
            layout = unit.layout if isinstance(unit, StorySlide) else None
            notes = old.notes if old and "notes" not in unit.model_fields_set else unit.notes
            values = {
                "id": identity,
                "heading": unit.title if isinstance(unit, StorySlide) else unit.heading,
                "goal": unit.job[:400],
                "layout": old.layout if old and "layout" not in unit.model_fields_set else layout,
                "notes": notes[:400],
                **_checked_row_refs(unit.refs, unit.required_refs, valid_refs),
            }
            row = PlanPartOut.model_validate((old.model_dump() if old else {}) | values)
            suggested[identity] = row
            source_units.append(unit.model_copy(update={"id": identity}))

        if isinstance(story, DeckStory):
            story = story.model_copy(update={"slides": source_units})
        else:
            story = story.with_parts(source_units, item.summary_box)
        rows = []
        order = fixed if order_fixed else suggested | fixed
        if summary_id is not None:
            order = {summary_id: suggested[summary_id], **order}
        for identity in order:
            manual = fixed.get(identity)
            row = suggested.get(identity) or manual
            assert row is not None
            if manual is not None:
                edited = {field: getattr(manual, field) for field in manual.edited_fields}
                if any(edited.get(field, getattr(row, field)) != getattr(row, field)
                       for field in ("heading", "goal")):
                    edited.update(refs=[], required_refs=[])
                row = row.model_copy(update=edited | {"edited_fields": manual.edited_fields})
            row = row.model_copy(update=_checked_row_refs(row.refs, row.required_refs, valid_refs))
            rows.append(row)
        if not order_fixed and isinstance(story, DeckStory):
            locked_title = next(
                (row for row in rows if row.layout == "title" and "layout" in row.edited_fields),
                None,
            )
            if locked_title is not None:
                rows.remove(locked_title)
                rows.insert(0, locked_title)
                rows = [
                    row.model_copy(update={"layout": None})
                    if row is not locked_title and row.layout == "title"
                    and "layout" not in row.edited_fields else row
                    for row in rows
                ]
        retained_ids = {row.id for row in rows}
        rows = [
            row.model_copy(update={
                "requires": [key for key in row.requires if key in retained_ids],
            })
            for row in rows
        ]
        self._validate_rows(run, rows)
        answer = run.study.contribution if run.study else run.memo.answer if run.memo else ""
        title = options.title or story.title.strip()[:160] or item.label
        summary = story.summary.strip()[:600] or answer[:600] or title
        angle = story.angle.strip()[:600] or answer[:600] or title
        old_topics = previous.proposal.coverage if previous else ()
        proposal = PlanProposalOut(
            title=title, summary=summary, angle=angle,
            coverage=_topics(story.coverage or [ProposedTopic(text=angle[:300])], old_topics),
            count=PlanCountOut(mode="exact", value=len(rows)),
            target_minutes=story.requested_minutes,
        )
        return PlanDraftOut(proposal=proposal, outline=rows), self._bind_story(
            prepared, proposal, rows, story, suggested,
        )

    @staticmethod
    def _validate_rows(run: Run, rows: Sequence[PlanPartOut]) -> None:
        item = run.output_type
        if problem := item.part_count_error(len(rows)):
            raise ModelError(problem)
        identities = {row.id for row in rows}
        for index, row in enumerate(rows):
            if set(row.requires) - identities:
                raise ModelError(f"Part {row.id} depends on a row outside the outline.")
            if problem := item.capabilities.outline_layout_error(row.layout, index):
                raise ModelError(f"Part {row.id}: {problem}")

    def _retained_plan(
        self, prepared: Prepared,
    ) -> tuple[PlanDraftOut | None, DeckStory | ReportStory | None]:
        state = prepared.progress
        plan = decode_stage(PlanDraftOut, state.get("plan_draft"))
        inputs = state.get("story_inputs")
        if plan is None or not isinstance(inputs, dict):
            return None, None
        try:
            matches = state.get("story_key") == _story_digest(prepared, inputs, plan)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            return None, None
        schema = DeckStory if prepared.run.spec.kind == "slides" else ReportStory
        story = decode_stage(schema, state.get("storyline"))
        if story is not None and plan.outline is not None:
            units = (story.slides if isinstance(story, DeckStory)
                     else story.parts(prepared.run.output_type.summary_box))
            if len(units) != len(plan.outline) or any(
                (unit.id, unit.job, unit.notes) != (row.id, row.goal, row.notes)
                or (
                    (unit.title, unit.layout) if isinstance(unit, StorySlide)
                    else (unit.heading, None)
                ) != (row.heading, row.layout)
                for unit, row in zip(units, plan.outline, strict=True)
            ):
                story = None
        return plan, story

    @staticmethod
    def _bind_story(
        prepared: Prepared, proposal: PlanProposalOut, rows: Sequence[PlanPartOut],
        story: DeckStory | ReportStory | None, previous: Mapping[str, PlanPartOut],
    ) -> DeckStory | ReportStory:
        """Bind the approved rows to surviving annotations using their stable IDs."""

        deck = prepared.run.spec.kind == "slides"
        summary_box = prepared.run.output_type.summary_box
        if deck:
            story = story if isinstance(story, DeckStory) else DeckStory(
                title=proposal.title, slides=[StorySlide(title=rows[0].heading)],
            )
            units = story.slides
        else:
            story = story if isinstance(story, ReportStory) else ReportStory(
                title=proposal.title, sections=[],
            )
            units = story.parts(summary_box)
        by_id = {unit.id: unit for unit in units}
        refs = set(prepared.pool.by_ref())
        assets = set(S.figure_inventory(prepared.pool.items))
        bound = []
        for index, row in enumerate(rows):
            current = by_id.get(row.id)
            old = previous.get(row.id)
            if not old or any(
                getattr(row, field) != getattr(old, field)
                for field in ("heading", "goal")
            ):
                current = None
            if summary_box and index == 0 and current is None:
                story = story.model_copy(update={"summary_points": []})
            fields = {
                "id": row.id, "job": row.goal, "notes": row.notes,
                **_checked_row_refs(row.refs, row.required_refs, refs),
            }
            if deck:
                slide = (
                    current if isinstance(current, StorySlide) else StorySlide(title=row.heading)
                )
                bound.append(slide.model_copy(update=fields | {
                    "title": row.heading, "layout": row.layout,
                    "figures": [figure for figure in slide.figures if figure.asset_id in assets],
                }))
            else:
                section = current if isinstance(current, StorySection) else StorySection(
                    heading=row.heading,
                )
                bound.append(section.model_copy(update=fields | {"heading": row.heading}))
        story = (story.model_copy(update={"slides": bound}) if deck
                 else story.with_parts(bound, summary_box))
        return story.model_copy(update={
            "title": proposal.title, "requested_count": len(rows),
            "requested_minutes": proposal.target_minutes,
        })

    @usage.job("outputs")
    def generate(
        self,
        prepared: Prepared,
        *,
        model: ModelInfo,
        effort: Effort | None,
        options: PlanOptionsOut,
        plan: PlanDraftOut,
        revision: str,
        authority: WriteAuthority,
        emit: Events,
        checkpoint: Checkpoint,
    ) -> BuiltOutput:
        """Write the approved plan, reusing preparation and binding edits without replanning."""

        authority = _write_authority(authority, revision)
        spec = prepared.run.spec
        run = replace(
            prepared.run, reviewed=True,
            spec=replace(spec, title=plan.proposal.title, theme=spec.theme or options.theme),
            limitations=list(prepared.run.limitations or []),
        )
        item = run.output_type
        if options.type != item.id:
            raise ValueError("The plan type must match the prepared output type.")
        if problem := item.part_count_error(plan.proposal.count.value):
            raise ModelError(problem)
        state = prepared.progress
        kept, story = self._retained_plan(prepared)
        inputs = state["story_inputs"] if kept is not None else {}
        inputs = inputs | {"options": _story_options(options), "model": model.ref, "effort": effort}
        if item.capabilities.pipeline == "direct":
            if plan.outline is not None or not plan.proposal.coverage:
                raise ModelError("Approve the coverage topics and quantity before writing.")
        else:
            if plan.outline is None or plan.proposal.count.mode != "exact" or (
                len(plan.outline) != plan.proposal.count.value
            ):
                raise ModelError("The approved part count must match the outline.")
            self._validate_rows(run, plan.outline)
            by_id = {row.id: row for row in kept.outline or []} if kept else {}
            run.story = self._bind_story(prepared, plan.proposal, plan.outline, story, by_id)
            parts = (run.story.slides if isinstance(run.story, DeckStory)
                     else run.story.parts(item.summary_box))
            run.plan = tuple(
                _planned(row, part.refs) for row, part in zip(plan.outline, parts, strict=True)
            )
            if kept is None or story is None:
                _drop_stages(state, _WRITE_FIELDS)
        story_key = _story_digest(prepared, inputs, plan)
        contract = plan.model_dump(mode="json")
        parent = asdict(authority) if isinstance(authority, ParentWrite) else None
        with checkpoint_stage(emit, state, checkpoint):
            state.update(story_key=story_key, story_inputs=inputs, plan_draft=contract)
            if run.story is not None:
                state["storyline"] = run.story.model_dump()
            self._write_key(
                prepared, run,
                [revision, contract, options.model_dump(mode="json"), model.ref, effort, parent],
            )
        self.scientific_context = self._saved_approach(run, prepared.pool) if run.study else None
        if item.capabilities.pipeline == "direct":
            built, count = self._make_direct(
                model, effort, run, prepared.pool, prepared.material, emit, state,
                checkpoint, plan.proposal,
            )
            return self._finish_build(run, prepared.pool, built, count)
        return self._write_outlined(
            model, effort, run, prepared.pool, prepared.material, emit, state, checkpoint,
            plan.outline,
        )

    def _write_key(self, prepared: Prepared, run: Run, inputs: Any) -> None:
        state = prepared.progress
        key = digest([
            WRITE_VERSION, state["material_key"], state["story_key"], asdict(run.spec), run.use,
            fmt.DESIGN, self.design, inputs,
        ])
        if state.get("write_key") != key:
            _drop_stages(state, _WRITE_FIELDS)
            state["write_key"] = key

    def _make_direct(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events,
        state: dict, checkpoint: Checkpoint, proposal: PlanProposalOut,
    ) -> tuple[BuiltOutput, int]:
        spec, item = run.spec, run.output_type
        shape = SHAPES[item.shape]
        units = f"{shape.unit_label}s"
        count = proposal.count
        target = count.value
        exact = count.mode == "exact"
        if exact:
            length = f"Write exactly {target} {units}."
        else:
            length = f"Aim for {target} {units}. Use at most {item.part_limit} {units}."
        header = shape.preamble(item.id)
        task = P.DIRECT_WRITE.format(
            output_type=item.label, brief=spec.brief or "(none)", audience=spec.audience,
            length=length,
            language=P.language_instruction(spec.language, run.model_language),
            recipe=item.write, memo=self._memo_text(run.memo) if run.memo else "",
            truth=P.TRUTH, header=f"Start with {header} on its own line." if header else "",
        )
        task += "\n\nApproved direction and quantity:\n" + json.dumps(
            proposal.model_dump(mode="json", include={"title", "angle", "coverage", "count",
                                                      "target_minutes"}),
            ensure_ascii=False,
        )
        system, prompt = material_request(material, task, spec)

        def problems(text: str) -> list[str]:
            found = item.direct_problems(text)
            _, sections = shape.split(text)
            if exact and len(sections) != target:
                found.append(f"Expected exactly {target} {units}. The draft has {len(sections)}.")
            return found

        label = f"Writing {item.label.lower()}"
        with checkpoint_stage(emit, state, checkpoint, "write", "format") as publish:
            text = state.get("direct_text")
            if not isinstance(text, str) or problems(text):
                self._stage(publish, "write", label, "running")
                text = _unfence(self._text(model, system, prompt, effort, max_tokens=24_000))
                self._stage(publish, "write", label, "done")
            else:
                self._stage(publish, "write", label, "done", "kept from before")
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

        preamble, parts = split_document(text, spec.kind)
        with checkpoint_stage(emit, state, None, "verify") as publish:
            drafts = self.verify(model, run, parts, pool, publish, material)
        label = f"Assembling the {shape.name}"
        self._stage(emit, "assemble", label, "running")
        outline = [
            Planned(heading_of(unit)) for unit in shape.units([draft.text for draft in drafts])
        ]
        built = assemble_output(
            spec, proposal.title, preamble, drafts, pool, outline, model, effort,
        )
        built = replace(built, title=proposal.title)
        if exact and len(shape.split(built.content)[1]) != target:
            raise ModelError(f"The assembled output must contain exactly {target} {units}.")
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
        manifest: dict[str, Any] | None = None,
        resume: dict[str, Any] | None = None,
        checkpoint: Checkpoint | None = None,
        authority: WriteAuthority | None = None,
        options: PlanOptionsOut | None = None,
        plan: PlanDraftOut | None = None,
        revision: str = "",
    ) -> BuiltOutput:
        """Prepare and write an already reviewed plan under explicit authority."""
        permit = _write_authority(authority, revision)
        if options is None or plan is None:
            raise RuntimeError("Studio generation requires a reviewed plan and options.")
        emit = events or _no_events
        prepared = self.prepare(
            model, effort, spec, pool, notes, manifest=manifest or {}, resume=resume, emit=emit,
            checkpoint=checkpoint,
        )
        return self.generate(
            prepared, model=model, effort=effort, options=options, plan=plan, revision=revision,
            authority=permit, emit=emit, checkpoint=checkpoint or _no_events,
        )

    def _write_outlined(
        self, model, effort, run: Run, pool: Pool, material: str, emit: Events,
        state: dict[str, Any], checkpoint: Checkpoint, rows: list[PlanPartOut],
    ) -> BuiltOutput:
        spec, plan = run.spec, run.plan
        assert run.story is not None
        looked: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        if spec.kind == "slides":
            stored = state.get("draft")
            reused_draft = isinstance(stored, str) and self._matches_story(
                run, fmt.split_slides(stored), "written deck",
            )
            outcomes = ("ready", "open_problems", "unavailable")
            if run.study and state.get("review_status") not in outcomes:
                reused_draft = False
            with checkpoint_stage(emit, state, checkpoint, "write", "science") as publish:
                if reused_draft:
                    text = stored
                    if run.study:
                        run.limitations = _saved_strings(state.get("limitations"))
                        run.review_status = state["review_status"]
                        run.reviews = self._restore_reviews(state.get("reviews"))
                    self._stage(publish, "write", "Writing the slides", "done", "kept from before")
                    if run.study:
                        self._stage(
                            publish, "science", "Reviewing the scientific explanation",
                            "done", "kept from before",
                        )
                else:
                    state.pop("checked", None)
                    text = self.write_deck(model, effort, run, pool, material, publish)
                    text = self.fix_format(model, effort, run, text, publish)
                    if run.study:
                        # A resumable draft includes the scientific review of its source content.
                        text = self.scientific_gate(
                            model, effort, run, text, pool, material, publish,
                        )
                    slides = fmt.split_slides(text)
                    cover = fmt.parse_slide(slides[0])
                    if spec.theme in fmt.THEMES and cover.attrs.get("theme") != spec.theme:
                        slides[0] = fmt.with_marker(
                            slides[0], cover.layout, **(cover.attrs | {"theme": spec.theme}),
                        )
                        text = "\n\n---\n\n".join(slides)
                if not _matches_plan(run, fmt.split_slides(text)):
                    raise ModelError("The slides must use the approved headings and layouts.")
                state["draft"] = text
                if run.study:
                    state["reviews"] = [
                        {"kind": kind, "review": review.model_dump()}
                        for review in run.reviews or []
                        for kind, schema in self._scientific_reviews().items()
                        if isinstance(review, schema)
                    ]
                    state.update(
                        review_status=run.review_status, limitations=list(run.limitations or []),
                    )
                    self.scientific_context.update(
                        {
                            "coverage_plan": run.coverage_plan.model_dump()
                            if run.coverage_plan
                            else None,
                            "reviews": state["reviews"],
                            "review_status": run.review_status,
                        }
                    )
            checked = state.get("checked")
            restored = self._restore_checked(run, checked)
            needs_refit = not restored or checked.get("refit_pending", False)
            with checkpoint_stage(emit, state, checkpoint, "verify") as publish:
                if restored:
                    # The claims of this same draft were checked before the run stopped.
                    drafts = restored
                    run.limitations = _saved_strings(checked.get("limitations"))
                    if checked.get("review_status") in outcomes:
                        run.review_status = checked["review_status"]
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
                state["checked"] = self._checked_progress(run, drafts, refit_pending=needs_refit)
            if needs_refit:
                with checkpoint_stage(emit, state, checkpoint):
                    drafts = self._fit_checked(model, effort, run, drafts, pool, material)
                    state["checked"] = self._checked_progress(run, drafts, refit_pending=False)
                    if run.study:
                        state["limitations"] = list(run.limitations or [])
            checked = [self._chart_fallback(d, i) for i, d in enumerate(drafts)]
            if self.design and checked:
                only = [i for i, draft in enumerate(drafts) if draft is checked[i]]
                checked = self.design_deck(model, run, checked, emit, only=only)
            drafts = checked
            title = run.story.title

            def assemble_deck(parts: list[Draft]) -> BuiltOutput:
                _validate_count(run, run.story, len(parts), "assembled deck")
                if not _matches_plan(run, [d.text for d in parts]):
                    raise ModelError(
                        "The deck could not keep the approved headings and layouts. "
                        "Choose Automatic for a layout the evidence cannot support."
                    )
                planned = [replace(row, layout=fmt.parse_slide(draft.text).layout)
                           for row, draft in zip(plan, parts, strict=True)]
                built = assemble_output(spec, title, "", parts, pool, planned, model, effort)
                if not _matches_plan(run, fmt.split_slides(built.content)):
                    raise ModelError("Citation numbering changed an approved slide heading.")
                return self._project_built_outline(built, rows, planned, run.story.title)

            with checkpoint_stage(emit, state, None):
                built = assemble_deck(drafts)
                try:
                    built, looked, snapshot = look(
                        self, built, drafts, model=model, effort=effort, use=run.use, pool=pool,
                        assemble=assemble_deck, emit=emit, unfence=_unfence,
                        material=run.evidence_material or material,
                        spec=spec, approved=plan,
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
            with checkpoint_stage(emit, state, checkpoint, "write") as publish:
                text = state.get("draft")
                if not isinstance(text, str) or self._report_plan_problems(
                    run, *split_document(text, "report"),
                ):
                    text = self.write_report(model, effort, run, pool, material, publish)
                    state["draft"] = text
            preamble, sections = split_document(text, "report")
            _validate_count(run, run.story, len(sections), "written report")
            opening = None
            with checkpoint_stage(emit, state, None, "verify") as publish:
                drafts = self.verify(model, run, sections, pool, publish, material)
                if preamble:
                    by_ref = pool.by_ref()
                    preamble, _ = check_citations(preamble, set(by_ref))
                    opening = Draft(
                        preamble, self._claims(
                            model, preamble, by_ref, material=run.evidence_material or material,
                        ),
                    )
            with checkpoint_stage(emit, state, None):
                drafts = [
                    self._chart_fallback(replace(draft, text=fmt.ensure_summary_mark(
                        draft.text, enabled=run.output_type.summary_box and index == 0,
                    )), index)
                    for index, draft in enumerate(drafts)
                ]
                title = run.story.title
                built = assemble_output(spec, title, preamble, drafts, pool, plan, model, effort)
                if opening is not None:
                    numbers = {ref: number for number, ref in enumerate(built.pool_refs, 1)}
                    numbered = number_output_part(preamble, numbers)
                    built = replace(
                        built, content=numbered + built.content[len(preamble):],
                        checks=[numbered_check(opening, numbered, numbers), *built.checks],
                    )
                final_preamble, parts = split_document(built.content, "report")
                count = len(parts)
                _validate_count(run, run.story, count, "assembled report")
                if problems := self._report_plan_problems(run, final_preamble, parts):
                    raise ModelError(" ".join(problems))
                built = self._project_built_outline(built, rows, plan, title)
        return self._finish_build(run, pool, built, len(drafts), looked, snapshot)

    @staticmethod
    def _project_built_outline(built, rows, planned, title) -> BuiltOutput:
        outline = [
            row.model_copy(update={"layout": part.layout or None}).model_dump(
                mode="json", exclude_none=True, exclude={"edited_fields"},
            )
            for row, part in zip(rows, planned, strict=True)
        ]
        return replace(built, title=title, outline=outline)

    @staticmethod
    def _matches_story(run: Run, parts: list[str], stage: str) -> bool:
        try:
            _validate_count(run, run.story, len(parts), stage)
        except ModelError:
            return False
        return _matches_plan(run, parts)

    def _restore_checked(self, run: Run, checked: Any) -> list[Draft]:
        if not isinstance(checked, dict) or not isinstance(checked.get("slides"), list):
            return []
        slides = checked["slides"]
        if not all(isinstance(row, dict) and isinstance(row.get("text"), str) for row in slides):
            return []
        if not self._matches_story(run, [row["text"] for row in slides], "checked deck"):
            return []
        try:
            return [
                Draft(
                    row["text"],
                    read_verdicts(row, _marked(row["text"]).body)
                    if row.get("verdicts") is not None or row.get("issues") is not None else None,
                )
                for row in slides
            ]
        except (KeyError, TypeError, ValueError, AttributeError):
            return []

    @staticmethod
    def _scientific_reviews():
        return {
            "scientific": S.ScientificReview, "learner": S.LearnerReview,
            "coverage": S.CoverageReview,
        }

    def _restore_reviews(self, reviews: Any) -> list:
        schemas = self._scientific_reviews()
        if not isinstance(reviews, list):
            return []
        return [
            review for row in reviews if isinstance(row, dict)
            and isinstance(row.get("kind"), str) and row["kind"] in schemas
            if (review := decode_stage(schemas[row["kind"]], row.get("review"))) is not None
        ]

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


__all__ = ["Prepared", "StudioAgents", "tokens"]
