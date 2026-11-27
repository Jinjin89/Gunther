"""Editable plan contracts. The service supplies verified material and live attempt state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, get_args

from pydantic import ValidationError

from gunther.artifact_workspace import (
    DEFAULT_AUDIENCE,
    ConversationBrief,
    ItemRec,
    Line,
    Pending,
    Work,
    WorkSpec,
    _layout_of,
    edit_preferences,
    rebind_approach_refs,
    spec_of,
)
from gunther.model_profiles import Effort, nearest
from gunther.output_types import DECK_USES, OUTPUT_TYPES, SHAPES, OutputType, output_type_for
from gunther.outputs import OutputUnit, _marked, document_units, heading_of, section_hash
from gunther.schemas import (
    ArtifactAudience,
    OutlineItem,
    OutputScope,
    OutputVoicesOut,
    PlanAttemptOut,
    PlanBasis,
    PlanCountOut,
    PlanDraftOut,
    PlanOptionsOut,
    PlanPartEditInput,
    PlanPartOut,
    PlanProposalOut,
    PlanQuestionOut,
    PlanStatus,
    StudioWorkSummaryOut,
    WorkPlanOut,
    WorkPlanPatchInput,
)
from gunther.studio import format as studio_format
from gunther.studio.prompts import LANGUAGE_FALLBACK


@dataclass(frozen=True)
class ReviewedMaterial:
    """Current eligibility of a Studio material snapshot, in conversation pool numbers."""

    key: str | None
    refs: frozenset[int] = frozenset()
    web_completed: bool = False
    blocker: str | None = None
    progress: dict[str, Any] | None = None  # rebuilt Studio checkpoint when removing web


@dataclass(frozen=True)
class PlanAvailability:
    models: Mapping[str, Sequence[Effort]]
    default_model: tuple[str, Effort | None] | None = None
    voices: OutputVoicesOut | None = None


@dataclass(frozen=True)
class PlanIssue:
    loc: tuple[str | int, ...]
    message: str
    row_id: str | None = None


class PlanValidationError(ValueError):
    def __init__(self, issues: Sequence[PlanIssue]) -> None:
        self.issues = tuple(issues)
        self.detail = [
            {"loc": list(issue.loc), "msg": issue.message}
            | ({"rowId": issue.row_id} if issue.row_id else {})
            for issue in issues
        ]
        super().__init__(issues[0].message)


class PlanConflictError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True)
class PlanPatchResult:
    brief: ConversationBrief
    work: Work
    pending: Pending | None
    edit_version: int
    changed: bool
    needs_material: bool
    basis: PlanBasis
    blocker: str | None


def inputs_key(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scope_inputs(scope: OutputScope) -> dict[str, object]:
    return {
        "mode": scope.mode,
        "source_ids": sorted(set(scope.source_ids)),
        "unit_ids": sorted(set(scope.unit_ids)),
        "session_ids": sorted(set(scope.session_ids)),
    }


def _option_inputs(options: PlanOptionsOut) -> dict[str, object]:
    return options.model_dump(mode="json", exclude={"scope"}) | {
        "scope": _scope_inputs(options.scope)
    }


def _type_of(work: Work) -> OutputType:
    return output_type_for(work.spec.kind, work.spec.chosen_output_type)


def project_plan_options(work: Work, brief: ConversationBrief) -> PlanOptionsOut:
    spec = spec_of(brief, work)
    item = _type_of(work)
    audience = spec.audience if spec.audience in get_args(ArtifactAudience) else DEFAULT_AUDIENCE
    use = work.spec.use.text if work.spec.use else None
    focus = (
        work.spec.focus if work.spec.focus is not None else (brief.goal.text if brief.goal else "")
    )
    return PlanOptionsOut(
        type=item.id,
        scope=work.spec.scope or OutputScope(),
        audience=audience,
        focus=focus,
        title=spec.title,
        length=spec.length,
        language=spec.language,
        use=use if use in DECK_USES else None,
        theme=spec.theme,
        design_version=spec.design_version,
        voices=work.spec.voices,
        web=work.spec.web,
        model=work.spec.model_ref,
        effort=work.spec.effort,
    )


def work_spec_for_options(options: PlanOptionsOut, previous: WorkSpec | None = None) -> WorkSpec:
    def marked(name: str, value: str | None) -> Line | None:
        old = getattr(previous, name) if previous is not None else None
        if old is not None and old.text == value:
            return old.model_copy()
        return Line(text=value, mark="edited") if value is not None else None

    return WorkSpec(
        kind=OUTPUT_TYPES[options.type].shape,
        output_type=marked("output_type", options.type),
        use=marked("use", options.use),
        length=marked("length", options.length),
        language=options.language,
        theme=options.theme,
        design_version=options.design_version,
        focus=options.focus,
        requested_title=options.title,
        audience_choice=options.audience,
        scope=options.scope.model_copy(deep=True),
        web=options.web,
        voices=options.voices,
        model_ref=options.model,
        effort=options.effort,
    )


def resolve_plan_options(
    options: PlanOptionsOut,
    availability: PlanAvailability,
    *,
    fields: set[str],
) -> PlanOptionsOut:
    """Resolve changed provider defaults and validate requested model, effort and ordered voices."""

    model, effort, voices = options.model, options.effort, options.voices
    issues: list[PlanIssue] = []
    if fields & {"model", "effort"}:
        default_model, default_effort = availability.default_model or (None, None)
        model = model or default_model
        if model is not None:
            levels = availability.models.get(model)
            if levels is None:
                issues.append(PlanIssue(("options", "model"), "Choose an available model."))
            elif "effort" in fields and effort is not None and effort not in levels:
                issues.append(
                    PlanIssue(("options", "effort"), "Choose a supported effort for this model.")
                )
            elif effort not in levels:
                if model == default_model and default_effort in levels:
                    effort = default_effort
                else:
                    effort = nearest("high", tuple(levels)) if levels else None
    if "voices" in fields and "voices" in OUTPUT_TYPES[options.type].options:
        available = availability.voices
        voices = voices or (available.defaults if available else None)
        if voices is not None and not _voices_available(voices, available):
            issues.append(PlanIssue(("options", "voices"), "Choose available voices for A and B."))
    if issues:
        raise PlanValidationError(issues)
    return options.model_copy(update={"model": model, "effort": effort, "voices": voices})


def _voices_available(voices: tuple[str, str], available: OutputVoicesOut | None) -> bool:
    if available is None:
        return False
    choices = {choice.value for choice in available.choices} | set(available.defaults or ())
    return available.allow_custom or set(voices) <= choices


def _validation_issues(error: ValidationError, *prefix: str | int) -> list[PlanIssue]:
    return [PlanIssue((*prefix, *issue["loc"]), issue["msg"]) for issue in error.errors()]


def _row_edits(row: ItemRec) -> dict[str, str | None]:
    return {
        "heading": row.heading,
        "goal": row.goal,
        "layout": row.layout or None,
        "notes": row.notes,
    }


def _outline_issues(
    work: Work,
    *,
    legacy_layouts: Mapping[str, str] | None = None,
) -> list[PlanIssue]:
    item = _type_of(work)
    if issue := item.part_count_error(len(work.outline)):
        return [PlanIssue(("outline",), issue)]
    issues = []
    locations = {row.id: position for position, row in enumerate(work.outline)}
    graph = TopologicalSorter()
    for position, row in enumerate(work.outline):
        loc = ("outline", position)
        if locations[row.id] != position:
            issues.append(PlanIssue((*loc, "id"), f"Duplicate row ID: {row.id}", row.id))
        try:
            _project_row(row)
        except ValidationError as error:
            issues.extend(
                PlanIssue(issue.loc, issue.message, row.id)
                for issue in _validation_issues(error, *loc)
            )
        layout_problem = item.capabilities.outline_layout_error(
            row.layout or None,
            position,
            keep_legacy=bool(legacy_layouts and legacy_layouts.get(row.id) == row.layout),
        )
        if layout_problem:
            issues.append(PlanIssue((*loc, "layout"), layout_problem, row.id))
        if missing := [identity for identity in row.requires if identity not in locations]:
            issues.append(
                PlanIssue(
                    (*loc, "requires"), f"Missing outline rows: {', '.join(missing)}.", row.id
                )
            )
        graph.add(row.id, *row.requires)
    try:
        graph.prepare()
    except CycleError as error:
        row_id = error.args[1][0]
        issues.append(
            PlanIssue(
                ("outline", locations[row_id], "requires"),
                "Outline dependencies form a cycle.",
                row_id,
            )
        )
    return issues


def normalize_plan_outline(work: Work, edits: Sequence[PlanPartEditInput]) -> list[ItemRec]:
    """Apply an ordered outline edit while preserving existing row metadata by identity."""

    item = _type_of(work)
    if not item.capabilities.outline:
        raise PlanValidationError([PlanIssue(("outline",), f"{item.label} uses coverage topics.")])
    previous = {row.id: row for row in work.outline}
    rows = []
    for edit in edits:
        old = previous.get(edit.id)
        values = edit.model_dump(exclude={"id"})
        if old is None:
            rows.append(
                ItemRec(
                    id=edit.id,
                    **(values | {"layout": edit.layout or ""}),
                    edited_fields=list(values),
                    layout_locked=edit.layout is not None,
                )
            )
            continue
        before = _row_edits(old)
        changed = [name for name, value in values.items() if value != before[name]]
        update = {name: values[name] for name in changed}
        if "layout" in update:
            update.update(layout=edit.layout or "", layout_locked=edit.layout is not None)
        if update.keys() & {"heading", "goal"}:
            # Evidence from the prior claim belongs to the prior wording.
            update.update(refs=[], required_refs=[])
        rows.append(
            old.model_copy(
                update={
                    **update,
                    "edited_fields": list(dict.fromkeys([*old.edited_fields, *changed])),
                },
                deep=True,
            )
        )
    candidate = work.model_copy(update={"outline": rows})
    legacy_layouts = (
        {identity: row.layout for identity, row in previous.items()} if work.version_id else None
    )
    issues = _outline_issues(candidate, legacy_layouts=legacy_layouts)
    if item.first_part_role and work.outline and rows and rows[0].id != work.outline[0].id:
        issues.append(PlanIssue(
            ("outline", 0, "id"), f"Keep the {item.first_part_role.lower()} first.",
            work.outline[0].id,
        ))
    if issues:
        raise PlanValidationError(issues)
    return rows


def _project_row(row: ItemRec) -> PlanPartOut:
    return PlanPartOut.model_validate(
        row.model_dump(exclude={"cards", "queries"}) | {"layout": row.layout or None},
    )


def _outline_body(unit: OutputUnit) -> str:
    """Read the body below the leading Markdown heading, if present."""

    heading, _, body = _marked(unit.text).body.lstrip().partition("\n")
    marker = heading.split(maxsplit=1)
    return body.strip() if marker and 1 <= len(marker[0]) <= 6 and not marker[0].strip("#") else ""


def project_saved_outline(
    kind: str, source_id: str, content: str, saved: Sequence[OutlineItem], *,
    previous: str | None = None, assign_ids: bool = False,
) -> list[OutlineItem]:
    """Project rows in Markdown order, retaining metadata only for matched sections."""

    units = document_units(content, kind)
    before = document_units(previous, kind) if previous is not None else []
    matched: dict[int, int] = {}
    available = set(range(len(saved)))

    def align(old_keys: Sequence[str], new_keys: Sequence[str], *, unique: bool = False) -> None:
        old: dict[str, list[int]] = {}
        new: dict[str, list[int]] = {}
        for position, key in enumerate(old_keys):
            if key and position in available:
                old.setdefault(key, []).append(position)
        for position, key in enumerate(new_keys):
            if key and position not in matched:
                new.setdefault(key, []).append(position)
        for key, earlier in old.items():
            current = new.get(key, [])
            if unique and (len(earlier) != 1 or len(current) != 1):
                continue
            for old_position, new_position in zip(earlier, current, strict=False):
                matched[new_position] = old_position
                available.remove(old_position)

    if before and len(before) == len(saved):
        align([section_hash(unit) for unit in before], [section_hash(unit) for unit in units])
        # A unique unchanged body identifies a section whose heading was renamed.
        align([_outline_body(unit) for unit in before], [_outline_body(unit) for unit in units],
              unique=True)
        headings = [heading_of(unit) for unit in before]
    else:
        headings = [row.heading for row in saved]
    align(
        [heading.strip().casefold() for heading in headings],
        [heading_of(unit).strip().casefold() for unit in units],
    )

    rows: list[OutlineItem] = []
    used: set[str] = set()
    reserved = {row.id for row in saved if row.id}
    version_key = inputs_key([source_id, content]) if previous is not None else source_id
    for position, unit in enumerate(units):
        row = saved[matched[position]] if position in matched else None
        identity = (row.id or "").strip() if row is not None else ""
        if row is None or (assign_ids and not identity) or (identity and identity in used):
            nonce = 0
            while True:
                identity = "p_" + inputs_key([version_key, position, nonce])[:62]
                if identity not in reserved and identity not in used:
                    break
                nonce += 1
        if identity:
            used.add(identity)
        heading = heading_of(unit).strip() or f"{SHAPES[kind].unit_label.title()} {position + 1}"
        layout = _layout_of(kind, position, unit.text, row.layout if row else None) or None
        if row is None:
            rows.append(OutlineItem(id=identity, heading=heading, layout=layout))
        else:
            rows.append(row.model_copy(
                update={"id": identity, "heading": heading, "layout": layout},
            ))
    return [
        row.model_copy(update={
            "requires": [identity for identity in row.requires if identity in used],
        })
        for row in rows
    ]


def _plan_title(work: Work) -> str:
    return work.spec.requested_title or work.title or _type_of(work).label


def _row_inputs(row: ItemRec) -> dict[str, object]:
    return {
        "id": row.id,
        **_row_edits(row),
        "message": row.message,
        "question": row.question,
        "link": row.link,
        "requires": row.requires,
        "refs": row.refs,
        **({"required_refs": row.required_refs} if row.required_refs else {}),
        "edited_fields": sorted(set(row.edited_fields)),
        "layout_locked": row.layout_locked,
        "fixed_design": row.design if row.design_locked else None,
    }


def _count(work: Work, item: OutputType, options: PlanOptionsOut) -> PlanCountOut:
    assert work.plan is not None and work.plan.proposal is not None
    if item.capabilities.outline:
        return PlanCountOut(mode="exact", value=len(work.outline))
    count = work.plan.proposal.count
    return PlanCountOut(mode=count.mode, value=count.value or item.automatic_target(options.length))


def plan_revision(work: Work, brief: ConversationBrief, pending: Pending | None = None) -> str:
    if work.plan is None:
        raise ValueError("The work has no plan identity.")
    plan = work.plan
    options = project_plan_options(work, brief)
    item = OUTPUT_TYPES[options.type]
    count = (
        _count(work, item, options).model_dump()
        if plan.proposal is not None and (work.outline or not item.capabilities.outline)
        else None
    )
    sha256 = inputs_key(
        {
            "plan": plan.id,
            "options": _option_inputs(options),
            "defaults": {
                "language": LANGUAGE_FALLBACK if options.language is None else None,
                "use": "talk" if item.capabilities.design and options.use is None else None,
                "theme": "paper" if item.capabilities.design and options.theme is None else None,
                "slide_format": studio_format.DESIGN if item.capabilities.design else None,
            },
            "brief": brief.model_dump(mode="json"),
            "material": plan.reviewed_material_key,
            "title": _plan_title(work),
            "proposal": plan.proposal.model_dump(mode="json") if plan.proposal else None,
            "count": count,
            "outline": [_row_inputs(row) for row in work.outline]
            if item.capabilities.outline
            else None,
            "order_edited": plan.order_edited,
            "coverage_edited": plan.coverage_edited,
            "pending": pending.message_id if pending else None,
            "rounds": plan.round_message_ids,
        }
    )
    return f"p{plan.epoch}-{sha256}"


def _material_blocker(work: Work, material: ReviewedMaterial | None) -> str | None:
    if (
        work.plan is None
        or not work.plan.reviewed_material_key
        or material is None
        or material.key != work.plan.reviewed_material_key
        or material.blocker
        or (work.spec.web and not material.web_completed)
    ):
        return (
            material.blocker if material else None
        ) or "Plan with these sources before generating."
    return None


def _plan_blocker(
    work: Work,
    options: PlanOptionsOut,
    pending: Pending | None,
    material: ReviewedMaterial | None,
    availability: PlanAvailability | None,
) -> str | None:
    if work.version_id is not None:
        return None
    if pending is not None:
        return "Answer the pending question or choose Use current settings."
    if work.plan is None or work.plan.proposal is None:
        return "Plan this output before generating."
    if work.plan.approval and work.plan.approval.first_version_id:
        return "This plan has already generated a version."
    if blocker := _material_blocker(work, material):
        return blocker
    item = OUTPUT_TYPES[options.type]
    if item.capabilities.outline:
        if issues := _outline_issues(work):
            first = issues[0]
            return f"{first.row_id}: {first.message}" if first.row_id else first.message
        assert material is not None
        unbound = [
            row.id for row in work.outline
            if set(row.required_refs) - material.refs
            or (row.refs and not material.refs.intersection(row.refs))
        ]
        if unbound:
            return (
                f"Evidence is unavailable for outline rows {', '.join(unbound)}. "
                "Edit the outline or plan again."
            )
    else:
        if not work.plan.proposal.coverage:
            return "Add at least one coverage topic."
        if problem := item.part_count_error(_count(work, item, options).value):
            return problem
    levels = availability.models.get(options.model) if availability and options.model else None
    if options.model is None or (availability is not None and levels is None):
        return "Choose an available model."
    if levels is not None and options.effort is not None and options.effort not in levels:
        return "Choose a supported effort for this model."
    if "voices" in item.options and (
        options.voices is None
        or (availability is not None and not _voices_available(options.voices, availability.voices))
    ):
        return "Choose available voices for A and B."
    return None


def _visible_attempt(work: Work, attempt_is_live: bool | None) -> PlanAttemptOut | None:
    attempt = work.plan.attempt if work.plan is not None else None
    if attempt is None:
        return None
    projected = PlanAttemptOut.model_validate(attempt)
    if projected.state == "running" and attempt_is_live is False:
        projected = projected.model_copy(update={"state": "interrupted"})
    return projected


def derive_plan_status(
    work: Work,
    *,
    pending: Pending | None,
    blocker: str | None,
    attempt: PlanAttemptOut | None,
) -> PlanStatus:
    if work.version_id is not None:
        return "saved"
    if attempt and attempt.state == "running" and attempt.phase == "generate":
        return "generating"
    if pending is not None:
        return "needs_answer"
    if attempt and attempt.state == "running":
        return "planning"
    complete = work.plan is not None and work.plan.proposal is not None
    if attempt and attempt.state == "failed" and (not complete or blocker is None):
        return "failed"
    return "ready" if complete and blocker is None else "planning"


def project_work_plan(
    work: Work,
    brief: ConversationBrief,
    *,
    material: ReviewedMaterial | None = None,
    pending: Pending | None = None,
    availability: PlanAvailability | None = None,
    attempt_is_live: bool | None = None,
) -> WorkPlanOut | None:
    plan = work.plan
    if plan is None:
        return None
    options = project_plan_options(work, brief)
    item = OUTPUT_TYPES[options.type]
    blocker = _plan_blocker(work, options, pending, material, availability)
    attempt = _visible_attempt(work, attempt_is_live)
    status = derive_plan_status(work, pending=pending, blocker=blocker, attempt=attempt)
    revision = plan_revision(work, brief, pending)
    proposal = None
    if plan.proposal is not None and (work.outline or not item.capabilities.outline):
        proposal = PlanProposalOut(
            title=_plan_title(work),
            summary=plan.proposal.summary,
            angle=plan.proposal.angle,
            coverage=plan.proposal.coverage,
            count=_count(work, item, options),
            target_minutes=plan.proposal.target_minutes,
        )
    outline = None
    if item.capabilities.outline:
        # The blocker carries the invalid stored row's identity.
        with suppress(ValidationError):
            outline = [_project_row(row) for row in work.outline] or None
    return WorkPlanOut(
        plan_id=plan.id,
        plan_revision=revision,
        plan_status=status,
        basis=plan.basis,
        blocker=blocker,
        options=options,
        preferences=brief,
        proposal=proposal,
        outline=outline,
        pending=PlanQuestionOut(
            question=pending.question,
            options=pending.options,
            message_id=pending.message_id,
            plan_id=plan.id,
            plan_revision=revision,
        )
        if pending is not None
        else None,
        attempt=attempt,
        approved_revision=plan.approval.revision if plan.approval else None,
    )


def project_plan_draft(plan: WorkPlanOut) -> PlanDraftOut | None:
    return PlanDraftOut(proposal=plan.proposal, outline=plan.outline) if plan.proposal else None


def project_studio_work_summary(
    work: Work,
    brief: ConversationBrief,
    *,
    updated_at: str,
    material: ReviewedMaterial | None = None,
    pending: Pending | None = None,
    availability: PlanAvailability | None = None,
    attempt_is_live: bool | None = None,
) -> StudioWorkSummaryOut:
    plan = project_work_plan(
        work,
        brief,
        material=material,
        pending=pending,
        availability=availability,
        attempt_is_live=attempt_is_live,
    )
    return StudioWorkSummaryOut(
        work_key=work.key,
        plan_id=plan.plan_id if plan else None,
        plan_revision=plan.plan_revision if plan else None,
        type=_type_of(work).id,
        title=_plan_title(work),
        status=plan.plan_status if plan else "saved" if work.version_id else "planning",
        basis=plan.basis if plan else "assistant",
        version_id=work.version_id,
        lineage_id=work.key if work.version_id else None,
        updated_at=(work.plan.updated_at if work.plan else "") or updated_at,
        attempt=plan.attempt if plan else None,
    )


def can_approve_work(
    work: Work,
    brief: ConversationBrief,
    *,
    revision: str,
    material: ReviewedMaterial | None,
    pending: Pending | None = None,
    availability: PlanAvailability | None = None,
    attempt_is_live: bool | None = None,
) -> bool:
    plan = project_work_plan(
        work,
        brief,
        material=material,
        pending=pending,
        availability=availability,
        attempt_is_live=attempt_is_live,
    )
    return bool(
        plan
        and plan.plan_revision == revision
        and plan.proposal is not None
        and plan.blocker is None
        and plan.plan_status in ("ready", "failed")
    )


def _remove_web_material(work: Work, material: ReviewedMaterial | None) -> None:
    refs = material.refs if material else frozenset()
    work.studio_progress = deepcopy(material.progress) if material else None
    # Keep hard bindings and rows that lost all support so the editor can explain the blocker.
    for row in work.outline:
        if refs.intersection(row.refs):
            row.refs = [ref for ref in row.refs if ref in refs or ref in row.required_refs]
    # Cards and approach references are derived annotations.
    work.cards = [
        card.model_copy(update={"refs": [ref for ref in card.refs if ref in refs]})
        for card in work.cards
    ]
    work.approach = rebind_approach_refs(work.approach, {ref: ref for ref in refs})
    work.approach_top = max(refs, default=0)


def apply_work_plan_patch(
    brief: ConversationBrief,
    work: Work,
    patch: WorkPlanPatchInput,
    *,
    edit_version: int,
    material: ReviewedMaterial | None = None,
    local_material: ReviewedMaterial | None = None,
    pending: Pending | None = None,
    availability: PlanAvailability | None = None,
    attempt_is_live: bool | None = None,
    updated_at: str = "",
) -> PlanPatchResult:
    """Build an atomic candidate. Caller commits it under its edit-version and attempt lock."""

    if patch.base_edit_version != edit_version:
        raise PlanConflictError("edit_conflict", "The plan changed. Review its current fields.")
    fields = patch.model_fields_set
    option_fields = patch.options.model_fields_set if "options" in fields else set()
    edit_fields = fields & {"outline", "coverage", "count"}
    if work.version_id is not None and (option_fields or edit_fields):
        raise PlanValidationError([PlanIssue(("options",), "Edit saved work through its version.")])
    emphasize = patch.emphasize if "emphasize" in fields else None
    avoid = patch.avoid if "avoid" in fields else None
    try:
        edited_brief = edit_preferences(brief, emphasize=emphasize, avoid=avoid)
    except ValidationError as error:
        raise PlanValidationError(_validation_issues(error)) from error

    before = project_plan_options(work, brief)
    options = before
    if option_fields:
        try:
            options = PlanOptionsOut.model_validate(
                before.model_dump() | patch.options.model_dump(exclude_unset=True),
            )
        except ValidationError as error:
            raise PlanValidationError(_validation_issues(error, "options")) from error
        if availability is not None:
            options = resolve_plan_options(options, availability, fields=option_fields)
    candidate = work.model_copy(deep=True)
    option_changed = _option_inputs(before) != _option_inputs(options)
    if option_changed:
        candidate.spec = work_spec_for_options(options, work.spec)
    _apply_plan_edits(candidate, patch, edit_fields)

    changed = candidate != work or edited_brief != brief
    attempt = _visible_attempt(work, attempt_is_live)
    if changed and attempt and attempt.state == "running" and attempt.phase == "generate":
        raise PlanConflictError("run_active", "Stop generation before editing this plan.")

    if changed and candidate.plan is not None:
        plan = candidate.plan
        scope_changed = _scope_inputs(before.scope) != _scope_inputs(options.scope)
        if before.web and not options.web:
            material = local_material if not scope_changed else None
            _remove_web_material(candidate, material)
            can_retain = plan.reviewed_material_key and material and not material.blocker
            plan.reviewed_material_key = material.key if can_retain else None
        if scope_changed or (options.web and not before.web):
            plan.reviewed_material_key = None
            material = None
            candidate.studio_progress = None
        plan.epoch += 1
        plan.updated_at = updated_at or plan.updated_at
        plan.basis = "user_edited"
        if not (attempt and attempt.state == "running"):
            plan.attempt = None
        if work.version_id is None:
            plan.approval = None
        if pending is not None:
            pending = pending.model_copy(
                update={"revision": plan_revision(candidate, edited_brief, pending)}
            )

    return PlanPatchResult(
        brief=edited_brief if changed else brief,
        work=candidate if changed else work,
        pending=pending,
        edit_version=edit_version + int(changed),
        changed=changed,
        needs_material=candidate.version_id is None
        and _material_blocker(candidate, material) is not None,
        basis=candidate.plan.basis if candidate.plan else "user_edited" if changed else "assistant",
        blocker=_plan_blocker(candidate, options, pending, material, availability),
    )


def _apply_plan_edits(work: Work, patch: WorkPlanPatchInput, fields: set[str]) -> None:
    item = _type_of(work)
    if "outline" in fields:
        previous = [row.id for row in work.outline]
        prior_title_row = work.outline[0] if work.outline else None
        work.outline = normalize_plan_outline(work, patch.outline)
        identities = [row.id for row in work.outline]
        work.parts = {
            identity: part for identity, part in work.parts.items() if identity in identities
        }
        if item.capabilities.first_layout and work.outline:
            cover = work.outline[0]
            if prior_title_row is None or (cover.id, cover.heading) != (
                prior_title_row.id,
                prior_title_row.heading,
            ):
                work.title = cover.heading
        if work.plan is not None and previous != identities:
            work.plan.order_edited = True
    for name in ("coverage", "count"):
        if name not in fields:
            continue
        if item.capabilities.outline:
            raise PlanValidationError(
                [PlanIssue((name,), f"{item.label} uses an ordered outline.")]
            )
        if work.plan is None or work.plan.proposal is None:
            raise PlanValidationError(
                [PlanIssue((name,), "Plan this output before editing its coverage or size.")]
            )
        proposal = work.plan.proposal
        if name == "coverage":
            if not patch.coverage:
                raise PlanValidationError([PlanIssue((name,), "Add at least one coverage topic.")])
            if patch.coverage != proposal.coverage:
                proposal.coverage = patch.coverage
                work.plan.coverage_edited = True
        else:
            if patch.count.value is not None and (
                error := item.part_count_error(patch.count.value)
            ):
                raise PlanValidationError([PlanIssue(("count", "value"), error)])
            proposal.count = patch.count
