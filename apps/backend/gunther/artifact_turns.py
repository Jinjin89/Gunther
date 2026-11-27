"""Admit, run and commit artifact conversation turns."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gunther import artifact_agent, key_results, usage
from gunther import artifact_workspace as workspaces
from gunther.answer_runs import AnswerRun, Stopped, check_stop, output_key
from gunther.artifact_plans import (
    PlanConflictError,
    PlanIssue,
    PlanValidationError,
    ReviewedMaterial,
    can_approve_work,
    normalize_plan_outline,
    plan_revision,
    project_plan_draft,
    project_plan_options,
    project_work_plan,
    resolve_plan_options,
    work_spec_for_options,
)
from gunther.artifact_read import artifact_detail, latest_check_records, live_artifact_message
from gunther.artifact_turn_result import TurnResult
from gunther.artifact_versions import (
    ArtifactConflictError,
    WorkPlanConflict,
    commit_version,
    earlier_output,
    lineage_head,
    locked_plan_run,
    matches_attempt,
    output_library,
    output_version,
)
from gunther.database import session_scope
from gunther.evidence import Evidence, KeyResultRefs, key_result_lines, key_result_refs
from gunther.library_lookup import SearchHit, pack_key_results, pack_searched
from gunther.llm import ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.models import ArtifactConversation, ArtifactMessage, utc_now
from gunther.output_material import (
    NOTHING_TO_BUILD_FROM,
    _OutputMaterial,
    pinned_scope,
    restore_studio_material,
    reviewed_studio_material,
    scope_members,
    studio_asset_hashes,
    studio_manifest,
    verify_studio_assets,
)
from gunther.output_types import OUTPUT_TYPES, SHAPES, output_type_for
from gunther.outputs import (
    BuiltOutput,
    Events,
    FirstWrite,
    Notes,
    OutputLimits,
    ParentWrite,
    Planned,
    Pool,
    WriteAuthority,
)
from gunther.run_budget import BudgetedGateway, RunBudget
from gunther.schemas import (
    ARTIFACT_TURN_FIELDS,
    AnswerPlanTurnInput,
    ApprovePlanTurnInput,
    ArtifactChangeOut,
    ArtifactOut,
    ArtifactQuestionOut,
    ArtifactTurnInput,
    ArtifactTurnOut,
    BuildTurnInput,
    CreateArtifactConversationInput,
    InitialPlanTurnInput,
    OutputModelOut,
    OutputScope,
    PlanCountEditInput,
    PlanDraftOut,
    PlanOptionsOut,
    PlanPartEditInput,
    RevisePlanTurnInput,
    SavedArtifactTurnInput,
    SearchedHitOut,
    UsedKeyResultOut,
    WorkPlanOut,
)
from gunther.service_records import _id, _timestamp
from gunther.studio import format as studio_format
from gunther.studio.pipeline import Prepared, StudioAgents
from gunther.studio.work import StudioWorkAgents

NEW_CONVERSATION = "New conversation"
_PLAN_REQUIRED = "Plan this output in Studio and choose Approve & Generate."
_CREATION_INPUT = (
    InitialPlanTurnInput | RevisePlanTurnInput | AnswerPlanTurnInput | ApprovePlanTurnInput
)
_REPLY_KIND = {
    "asked": "question",
    "answered": "answer",
    "saved": "result",
    "replied": "reply",
    "paused": "paused",
    "planned": "planned",
}


class _PlanSuperseded(Exception):
    """A user edit made this model turn's proposal stale."""


@dataclass(frozen=True)
class _PlanTurn:
    run: AnswerRun
    request_id: str
    plan_id: str
    revision: str
    user_id: str
    reply_id: str
    feedback: str
    manifest: dict[str, Any]
    reviewed: ReviewedMaterial | None
    authority: FirstWrite | None
    legacy_build: BuildTurnInput | None = None

    @property
    def phase(self) -> Literal["plan", "generate"]:
        return "generate" if self.authority is not None else "plan"

    def matches(self, work: workspaces.Work) -> bool:
        return matches_attempt(
            work, self.request_id, self.run.server_boot_id, self.run.attempt_id, self.phase,
        )


@dataclass
class _AgentResult:
    result: TurnResult
    brief: workspaces.ConversationBrief
    material: _OutputMaterial
    searched: list[SearchHit]
    key_results: KeyResultRefs
    progress: dict[str, Any] | None


def _plan_text(command: _CREATION_INPUT) -> str:
    match command:
        case InitialPlanTurnInput():
            return command.setup.focus or f"Plan {OUTPUT_TYPES[command.setup.type].label}"
        case RevisePlanTurnInput():
            return command.text or "Update the plan with the current settings."
        case AnswerPlanTurnInput():
            return "Use current settings"
        case ApprovePlanTurnInput():
            return "Approve & Generate"


def _work_for_plan(workspace: workspaces.Workspace, plan_id: str) -> workspaces.Work:
    work = next((item for item in workspace.works if item.plan and item.plan.id == plan_id), None)
    if work is None:
        raise LookupError("This plan was not found in this conversation.")
    return work


def _accept_draft(work: workspaces.Work, draft: PlanDraftOut, *, material_key: str) -> None:
    assert work.plan is not None
    proposal = draft.proposal
    count = proposal.count
    item = output_type_for(work.spec.kind, work.spec.chosen_output_type)
    if item.capabilities.outline:
        if not draft.outline or (count.mode, count.value) != ("exact", len(draft.outline)):
            raise ModelError("The proposed part count does not match its outline.")
        edits = [
            PlanPartEditInput(
                id=row.id,
                heading=row.heading,
                goal=row.goal,
                layout=row.layout,
                notes=row.notes,
            )
            for row in draft.outline
        ]
        # Reuse the edit validator for structural positions, IDs, and dependencies.
        prior = {row.id: row for row in work.outline}
        rows = []
        for row in draft.outline:
            existing = prior.get(row.id)
            values = row.model_dump() | {"layout": row.layout or ""}
            if existing is None:
                rows.append(workspaces.ItemRec.model_validate(values))
            else:
                rows.append(workspaces.ItemRec.model_validate(existing.model_dump() | values))
        work.outline = rows
        normalize_plan_outline(work, edits)
    elif draft.outline is not None or not proposal.coverage or item.part_count_error(count.value):
        raise ModelError("The proposed coverage or count is invalid for this output.")
    else:
        work.outline = []
    work.title = proposal.title
    work.plan.proposal = workspaces.PlanProposalRecord(
        summary=proposal.summary,
        angle=proposal.angle,
        coverage=proposal.coverage,
        count=(
            PlanCountEditInput(mode="auto")
            if count.mode == "auto"
            else PlanCountEditInput(mode=count.mode, value=count.value)
        ),
        target_minutes=proposal.target_minutes,
    )
    work.plan.reviewed_material_key = material_key


class ArtifactTurns:
    def artifact_conversation_title(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        text: str = "",
        *,
        initial: InitialPlanTurnInput | None = None,
    ) -> str:
        with self.sessions() as session:
            output_library(session, workspace_id, base_id)
            row = session.get(ArtifactConversation, conversation_id)
            if row is None and initial is not None:
                return initial.setup.title or OUTPUT_TYPES[initial.setup.type].label
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            if row.title == NEW_CONVERSATION and text.strip():
                return " ".join(text.split())[:60]
            return row.title

    def _turn_fingerprints(
        self,
        workspace_id: str,
        base_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
    ) -> tuple[str, ...]:
        command = payload.root
        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "artifact-turn",
            {
                "conversationId": conversation_id,
                **command.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude={"client_request_id"},
                ),
            },
        )
        if isinstance(command, SavedArtifactTurnInput):
            previous = self._output_fingerprint(
                workspace_id,
                base_id,
                "artifact-turn",
                {
                    "conversationId": conversation_id,
                    "kind": command.kind,
                    "text": command.text,
                    **command.request_fields(),
                },
            )
            return fingerprint, previous
        return (fingerprint,)

    @staticmethod
    def _user_message(session: Session, row: ArtifactConversation, request_id: str):
        return session.scalar(
            select(ArtifactMessage).where(
                ArtifactMessage.conversation_id == row.id,
                ArtifactMessage.client_request_id == request_id,
                ArtifactMessage.role == "user",
            )
        )

    def _earlier_turn(
        self,
        session: Session,
        row: ArtifactConversation,
        payload: ArtifactTurnInput,
        fingerprint: str,
    ) -> ArtifactTurnOut | None:
        """Check the submitted contract even when its earlier turn has no terminal yet."""

        command = payload.root
        user = self._user_message(session, row, command.client_request_id)
        if user is None:
            return None
        stored = json.loads(user.payload_json or "{}")
        saved_fingerprint = stored.get("requestFingerprint")
        if saved_fingerprint is not None:
            same = saved_fingerprint == fingerprint
        else:
            same = (
                isinstance(command, SavedArtifactTurnInput)
                and user.text == command.text
                and user.kind == command.kind
                and {key: stored[key] for key in ARTIFACT_TURN_FIELDS & stored.keys()}
                == command.request_fields()
                and (not command.focus or stored.get("focus") == command.focus.model_dump())
            )
        replies = select(ArtifactMessage).where(
            ArtifactMessage.conversation_id == row.id, ArtifactMessage.role == "assistant",
        )
        reply = session.scalar(replies.where(
            func.json_extract(ArtifactMessage.payload_json, "$.to") == user.id,
        ).order_by(ArtifactMessage.created_at, ArtifactMessage.id).limit(1))
        if reply is None and saved_fingerprint is None and not stored:
            # Imported histories link replies by their position in the recorded exchange.
            following = session.scalar(select(ArtifactMessage).where(
                ArtifactMessage.conversation_id == row.id,
                ArtifactMessage.created_at > user.created_at,
            ).order_by(ArtifactMessage.created_at, ArtifactMessage.id).limit(1))
            if following is not None and following.role == "assistant" and json.loads(
                following.payload_json or "{}"
            ).get("imported"):
                if not same and following.artifact_id is not None:
                    # Bare reconstructed turns lack the original command fields. The
                    # sealed version still has the original request fingerprint.
                    return None
                reply = following
        if not same:
            raise ArtifactConflictError("clientRequestId was already used for a different message")
        if reply is None:
            return None
        data = json.loads(reply.payload_json or "{}")
        if reply.kind == "stopped":
            raise Stopped
        if reply.kind == "failed":
            raise PlanConflictError("retry_required", str(data.get("error") or reply.text))
        return self._turn_out(session, row, reply, data)

    def _turn_out(
        self,
        session: Session,
        row: ArtifactConversation,
        reply: ArtifactMessage,
        data: dict,
    ) -> ArtifactTurnOut:
        artifact = (
            artifact_detail(
                session,
                output_version(
                    session,
                    row.workspace_id,
                    row.knowledge_base_id,
                    reply.artifact_id,
                    latest=False,
                ),
            )
            if reply.artifact_id
            else None
        )
        return ArtifactTurnOut(
            outcome=data.get("outcome")
            or {kind: outcome for outcome, kind in _REPLY_KIND.items()}[reply.kind],
            reply=reply.text,
            question=ArtifactQuestionOut(**data["question"]) if data.get("question") else None,
            artifact=artifact,
            changes=[ArtifactChangeOut(**item) for item in data.get("changes", [])],
            message=self._artifact_message_out(reply),
            plan=WorkPlanOut.model_validate(data["plan"]) if data.get("plan") else None,
            edit_version=data.get("editVersion"),
        )

    @staticmethod
    def _published_turn_out(artifact: ArtifactOut) -> ArtifactTurnOut:
        return ArtifactTurnOut(
            outcome="saved",
            reply=getattr(artifact.provenance, "reply", None) or "",
            artifact=artifact,
            changes=[
                ArtifactChangeOut(**change)
                for change in getattr(artifact.provenance, "changes", None) or ()
            ],
            message=None,
        )

    def artifact_turn_replay(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        *,
        legacy_fingerprint: str | None = None,
    ) -> ArtifactTurnOut | None:
        fingerprint, *previous = self._turn_fingerprints(
            workspace_id, base_id, conversation_id, payload
        )
        with self.sessions() as session:
            output_library(session, workspace_id, base_id)
            row = session.get(ArtifactConversation, conversation_id)
            if row is not None:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id
                )
                if earlier := self._earlier_turn(session, row, payload, fingerprint):
                    return earlier
        version = earlier_output(
            self.sessions,
            workspace_id,
            payload.root.client_request_id,
            legacy_fingerprint or fingerprint,
            previous=previous if legacy_fingerprint is None else (),
        )
        if version is None:
            return None
        if (version.knowledge_base_id, version.conversation_id) != (base_id, conversation_id):
            raise ArtifactConflictError("clientRequestId was already used for a different message")
        return self._published_turn_out(version)

    def artifact_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        events: Events | None = None,
        *,
        run: AnswerRun | None = None,
        legacy_fingerprint: str | None = None,
        legacy_title: str | None = None,
        expected_parent: str | None = None,
    ) -> ArtifactTurnOut:
        emit = events or (lambda _event: None)
        earlier = self.artifact_turn_replay(
            base_id,
            workspace_id,
            conversation_id,
            payload,
            legacy_fingerprint=legacy_fingerprint,
        )
        if earlier is not None:
            return earlier
        fingerprint = self._turn_fingerprints(workspace_id, base_id, conversation_id, payload)[0]
        command = payload.root
        if not isinstance(command, SavedArtifactTurnInput):
            if run is None or not self.answer_runs.matches(
                output_key(conversation_id),
                scope=(workspace_id, base_id),
                server_boot_id=run.server_boot_id,
                attempt_id=run.attempt_id,
            ):
                raise PlanConflictError("run_active", "This turn needs a live Studio run.")
            return self._creation_turn(
                base_id,
                workspace_id,
                conversation_id,
                command,
                run,
                fingerprint,
                emit,
            )
        if command.build is not None and command.target_lineage_id is None:
            raise PlanConflictError("plan_required", _PLAN_REQUIRED)
        return self._saved_turn(
            base_id,
            workspace_id,
            conversation_id,
            command,
            fingerprint,
            emit,
            legacy_fingerprint=legacy_fingerprint,
            legacy_title=legacy_title,
            expected_parent=expected_parent,
        )

    def _add_turn_user(
        self,
        session: Session,
        row: ArtifactConversation,
        command: _CREATION_INPUT | SavedArtifactTurnInput,
        user_id: str,
        text: str,
        fingerprint: str,
    ) -> None:
        fields = {**command.request_fields(), "requestFingerprint": fingerprint}
        if isinstance(command, SavedArtifactTurnInput) and command.focus:
            fields["focus"] = command.focus.model_dump()
        session.add(
            ArtifactMessage(
                id=user_id,
                conversation_id=row.id,
                role="user",
                kind=command.kind,
                text=text,
                payload_json=json.dumps(fields, ensure_ascii=False),
                client_request_id=command.client_request_id,
                created_at=utc_now(),
            )
        )

    def _add_turn_reply(
        self,
        session: Session,
        row: ArtifactConversation,
        user_id: str,
        reply_id: str,
        *,
        kind: str,
        text: str,
        data: dict,
        artifact_id: str | None = None,
    ) -> ArtifactMessage:
        reply = ArtifactMessage(
            id=reply_id,
            conversation_id=row.id,
            role="assistant",
            kind=kind,
            text=text,
            payload_json=json.dumps({"to": user_id, **data}, ensure_ascii=False),
            artifact_id=artifact_id,
            created_at=max(
                utc_now(),
                session.get(ArtifactMessage, user_id).created_at + timedelta(milliseconds=1),
            ),
        )
        session.add(reply)
        session.flush()
        return reply

    def _initial_plan(
        self,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        command: InitialPlanTurnInput,
        *,
        old: workspaces.Work | None,
        old_options: PlanOptionsOut | None,
        legacy_build: BuildTurnInput | None,
    ) -> tuple[workspaces.Work, workspaces.ConversationBrief]:
        setup = command.setup
        options = PlanOptionsOut.model_validate(
            (old_options.model_dump() if old_options else {})
            | setup.model_dump(exclude={"emphasize", "avoid"}, exclude_unset=True)
        )
        options = resolve_plan_options(
            options,
            self._plan_availability(),
            fields={"model", "effort", "voices"},
        )
        if old is not None and (old.plan is not None or old.version_id is not None):
            raise PlanConflictError("plan_changed", "Open the existing plan to update it.")
        work = old or workspaces.Work(key=command.plan_id, spec=work_spec_for_options(options))
        work.spec = work_spec_for_options(options, work.spec)
        work.title = setup.title or work.title
        work.plan = workspaces.PlanRecord(id=command.plan_id, epoch=1)
        outline = command.initial_outline
        if not outline and legacy_build and OUTPUT_TYPES[options.type].capabilities.outline:
            outline = [
                PlanPartEditInput(
                    id=row.id or _id("p"), heading=row.heading.strip(), goal=row.goal,
                    layout=row.layout or None, notes=row.notes,
                )
                for row in legacy_build.outline or ()
            ]
        if outline:
            work.outline = normalize_plan_outline(work, outline)
            work.plan.order_edited = True
            work.plan.basis = "user_edited"
        if old is None:
            workspace.works.append(work)
        return work, workspaces.edit_preferences(
            brief,
            emphasize=setup.emphasize if "emphasize" in setup.model_fields_set else None,
            avoid=setup.avoid if "avoid" in setup.model_fields_set else None,
        )

    @staticmethod
    def _answer_plan(command: AnswerPlanTurnInput, pending: workspaces.Pending | None) -> str:
        if pending is None or pending.message_id != command.question_message_id:
            raise PlanConflictError("question_changed", "Answer the current planning question.")
        answer = command.answer
        if answer.type == "choice":
            if answer.index >= len(pending.options):
                raise PlanValidationError(
                    [PlanIssue(("answer", "index"), "Choose a listed answer.")]
                )
            return pending.options[answer.index]
        return answer.text if answer.type == "text" else "Use current settings"

    def _admit_plan(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        command: _CREATION_INPUT,
        run: AnswerRun,
        fingerprint: str,
    ) -> tuple[_PlanTurn, workspaces.Workspace, workspaces.ConversationBrief]:
        generating = isinstance(command, ApprovePlanTurnInput)
        verified_assets = None
        if generating:
            with self.sessions() as session:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id,
                )
                frozen = _work_for_plan(self._load_workspace(row)[0], command.plan_id)
            verified_assets = verify_studio_assets(frozen, self.asset_reader)
        guard = locked_plan_run(
            self.answer_runs, conversation_id, (workspace_id, base_id),
            server_boot_id=run.server_boot_id, attempt_id=run.attempt_id,
        )
        with guard as live, session_scope(self.sessions) as session:
            if live is None:
                raise PlanConflictError("run_active", "This turn needs a live Studio run.")
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if run.stop_requested.is_set():
                raise Stopped
            output_library(session, workspace_id, base_id)
            row = session.get(ArtifactConversation, conversation_id)
            initial = isinstance(command, InitialPlanTurnInput)
            if row is None and initial:
                try:
                    CreateArtifactConversationInput(id=conversation_id)
                except ValidationError as error:
                    raise PlanValidationError(
                        [PlanIssue(("conversationId",), "Use a valid conversation ID.")]
                    ) from error
                row = ArtifactConversation(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    knowledge_base_id=base_id,
                    title=command.setup.title or OUTPUT_TYPES[command.setup.type].label,
                    scope_json=json.dumps(command.setup.scope.model_dump()),
                )
                session.add(row)
                session.flush()
            else:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id
                )
            workspace, brief = self._load_workspace(row)
            self._reconcile_plan_publications(session, row, workspace)
            self._settle_interrupted(workspace, {(run.server_boot_id, run.attempt_id)})
            self._reject_generation(workspace.works)
            old = next((
                work for work in workspace.works
                if workspaces.pending_target(work) == command.plan_id
            ), None)
            legacy = None
            if old is not None and old.build_key and (
                (initial and old.plan is None)
                or (old.studio_progress or {}).get("request_key")
            ):
                builds = session.scalars(
                    select(ArtifactMessage).where(
                        ArtifactMessage.conversation_id == row.id,
                        ArtifactMessage.role == "user",
                        ArtifactMessage.kind == "build",
                    ).order_by(ArtifactMessage.created_at, ArtifactMessage.id)
                )
                legacy = self._legacy_builds(row, builds).get(old.build_key)
            legacy_build = legacy.build if legacy else None
            user = self._user_message(session, row, command.client_request_id)
            if user is not None:
                if json.loads(user.payload_json or "{}").get("requestFingerprint") != fingerprint:
                    raise ArtifactConflictError(
                        "clientRequestId was already used for a different message"
                    )
                work = _work_for_plan(workspace, command.plan_id)
                attempt = work.plan.attempt
                if (
                    not initial
                    or not attempt
                    or (attempt.request_id, attempt.state)
                    != (command.client_request_id, "interrupted")
                ):
                    raise PlanConflictError(
                        "retry_required", "Start a new turn to retry this request."
                    )
            elif initial:
                before = brief
                first_work = not workspace.works
                old_options = self._legacy_options(row, brief, old, legacy) if old else None
                work, brief = self._initial_plan(
                    workspace, brief, command, old=old, old_options=old_options,
                    legacy_build=legacy_build,
                )
                if first_work:
                    if row.title == NEW_CONVERSATION:
                        row.title = command.setup.title or OUTPUT_TYPES[command.setup.type].label
                    row.scope_json = json.dumps(command.setup.scope.model_dump())
                if brief != before:
                    for other in workspace.works:
                        if other.key != work.key:
                            self._mark_plan_edit(workspace, other, brief)
                workspace.edit_version += 1
            else:
                work = _work_for_plan(workspace, command.plan_id)
            plan = work.plan
            assert plan is not None
            pending = self._work_pending(workspace, work)
            revision = plan_revision(work, brief, pending)

            def conflict(code: str, detail: str) -> WorkPlanConflict:
                return WorkPlanConflict(
                    code, detail, work, revision=revision, edit_version=workspace.edit_version,
                )

            if work.version_id is not None:
                raise conflict("plan_changed", "Open the saved version of this plan.")
            if plan.attempt and plan.attempt.state == "running":
                raise conflict("run_active", "Gunther is already working on this plan.")
            feedback = _plan_text(command)
            if isinstance(command, AnswerPlanTurnInput):
                try:
                    feedback = self._answer_plan(command, pending)
                except PlanConflictError as error:
                    raise conflict(error.code, str(error)) from error
            if not initial and command.plan_revision != revision:
                raise conflict("plan_changed", "Review the current plan before continuing.")
            if isinstance(command, RevisePlanTurnInput) and pending is not None:
                raise conflict("question_changed", "Answer the pending planning question.")
            scope = work.spec.scope or self._conversation_scope(row)
            try:
                members = scope_members(session, base_id, scope, strict=True)
            except ValueError as error:
                if isinstance(command, ApprovePlanTurnInput):
                    raise conflict("plan_changed", str(error)) from error
                raise PlanValidationError([PlanIssue(("options", "scope"), str(error))]) from error
            if not members:
                raise PlanValidationError([PlanIssue(("options", "scope"), NOTHING_TO_BUILD_FROM)])
            manifest = studio_manifest(workspace_id, base_id, scope, members)
            reviewed = reviewed_studio_material(
                session, workspace_id, base_id, scope, workspace, work,
                verified_assets=verified_assets,
            )
            user_id = user.id if user else _id("acm")
            authority = None
            if generating:
                availability = self._plan_availability()
                if not can_approve_work(
                    work,
                    brief,
                    revision=revision,
                    material=reviewed,
                    pending=pending,
                    availability=availability,
                ):
                    projected = project_work_plan(
                        work,
                        brief,
                        material=reviewed,
                        pending=pending,
                        availability=availability,
                    )
                    raise conflict(
                        "plan_changed",
                        projected.blocker or "Review the current plan before generating.",
                    )
                assert reviewed is not None and reviewed.key is not None
                authority = FirstWrite(
                    workspace_id,
                    base_id,
                    conversation_id,
                    work.key,
                    revision,
                    command.client_request_id,
                    run.server_boot_id,
                    run.attempt_id,
                )
                plan.approval = workspaces.PlanApprovalRecord(
                    request_id=command.client_request_id,
                    attempt_id=run.attempt_id,
                    user_message_id=user_id,
                    revision=revision,
                    material_key=reviewed.key,
                    approved_at=_timestamp(utc_now()),
                )
            elif isinstance(command, AnswerPlanTurnInput) or (
                isinstance(command, RevisePlanTurnInput) and command.text
            ):
                workspace.pending_by_target.pop(plan.id, None)
                plan.round_message_ids.append(user_id)
                plan.epoch += 1
                workspace.edit_version += 1
                revision = plan_revision(work, brief)
            if not generating:
                plan.approval = None
                plan.round_message_ids = list(dict.fromkeys(plan.round_message_ids))
            plan.attempt = workspaces.PlanAttemptRecord(
                request_id=command.client_request_id,
                server_boot_id=run.server_boot_id,
                attempt_id=run.attempt_id,
                phase="generate" if generating else "plan",
                state="running",
            )
            plan.updated_at = _timestamp(utc_now())
            workspace.active = work.key
            if user is None:
                self._add_turn_user(session, row, command, user_id, feedback, fingerprint)
            self._store_workspace(row, workspace, brief)
            return (
                _PlanTurn(
                    run,
                    command.client_request_id,
                    plan.id,
                    revision,
                    user_id,
                    _id("acm"),
                    feedback,
                    manifest,
                    reviewed,
                    authority,
                    legacy_build,
                ),
                workspace,
                brief,
            )

    def _checkpoint_plan(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        turn: _PlanTurn,
        progress: dict,
        pool: Pool,
    ) -> None:
        guard = locked_plan_run(
            self.answer_runs, conversation_id, (workspace_id, base_id),
            server_boot_id=turn.run.server_boot_id, attempt_id=turn.run.attempt_id,
        )
        with guard as live, session_scope(self.sessions) as session:
            self._require_plan_run(turn, live)
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._load_workspace(row)
            work = _work_for_plan(workspace, turn.plan_id)
            if not turn.matches(work):
                raise PlanConflictError("plan_changed", "A different attempt now owns this plan.")
            if plan_revision(work, brief, self._work_pending(workspace, work)) != turn.revision:
                raise _PlanSuperseded
            retained = workspaces.pool_from(workspace)
            for item in pool.items:
                if retained.adopt(item).ref != item.ref:
                    raise PlanConflictError(
                        "plan_changed", "The evidence pool changed. Plan again."
                    )
            workspaces.store_pool(workspace, retained)
            work.studio_progress = progress
            row.state_json = workspaces.dump_workspace(workspace)

    @staticmethod
    def _require_plan_run(turn: _PlanTurn, live: AnswerRun | None) -> None:
        if turn.run.stop_requested.is_set():
            raise Stopped
        if live is None:
            raise PlanConflictError("plan_changed", "A different attempt now owns this plan.")

    def stop_artifact_plan(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        plan_id: str,
        request_id: str,
        run: AnswerRun,
    ) -> None:
        self._end_plan_attempt(
            base_id,
            workspace_id,
            conversation_id,
            plan_id,
            request_id,
            run,
            state="stopped",
            detail="Stopped.",
        )

    def _end_plan_attempt(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        plan_id: str,
        request_id: str,
        run: AnswerRun,
        *,
        state: str,
        detail: str,
    ) -> None:
        guard = locked_plan_run(
            self.answer_runs, conversation_id, (workspace_id, base_id),
            server_boot_id=run.server_boot_id, attempt_id=run.attempt_id,
        )
        with guard as live, session_scope(self.sessions) as session:
            if live is None:
                return
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id
                )
                workspace, brief = self._load_workspace(row)
                work = _work_for_plan(workspace, plan_id)
            except LookupError:
                return
            user = self._user_message(session, row, request_id)
            if user is None or user.kind not in ("plan", "answer", "approve"):
                return
            phase = "generate" if user.kind == "approve" else "plan"
            if not matches_attempt(work, request_id, run.server_boot_id, run.attempt_id, phase):
                return
            plan = work.plan
            assert plan is not None and plan.attempt is not None
            plan.attempt.state, plan.attempt.detail = state, detail
            if plan.approval and plan.approval.attempt_id == run.attempt_id:
                plan.approval = None
            plan.updated_at = _timestamp(utc_now())
            self._add_turn_reply(
                session,
                row,
                user.id,
                _id("acm"),
                kind=state,
                text=detail,
                data={"error": detail} if state == "failed" else {},
            )
            self._store_workspace(row, workspace, brief)

    def _complete_plan(
        self,
        session: Session,
        row: ArtifactConversation,
        turn: _PlanTurn,
        workspace: workspaces.Workspace,
        baseline: workspaces.ConversationBrief,
        proposed_brief: workspaces.ConversationBrief,
        result: TurnResult,
        artifact: ArtifactOut | None = None,
    ) -> ArtifactTurnOut:
        stored, brief = self._load_workspace(row)
        work = _work_for_plan(stored, turn.plan_id)
        if not turn.matches(work):
            if turn.run.stop_requested.is_set():
                raise Stopped
            raise PlanConflictError("plan_changed", "A different attempt now owns this plan.")
        plan = work.plan
        assert plan is not None
        pending = self._work_pending(stored, work)
        stale = plan_revision(work, brief, pending) != turn.revision
        scope = work.spec.scope or self._conversation_scope(row)
        material = (
            reviewed_studio_material(
                session,
                row.workspace_id,
                row.knowledge_base_id,
                scope,
                stored,
                work,
            )
            if result.draft is not None
            else None
        )
        if result.draft is not None and (material is None or material.key is None):
            stale = True
        if stale:
            if artifact is not None:
                raise PlanConflictError(
                    "plan_changed", "The plan changed before it could be saved."
                )
            result = TurnResult(
                "paused",
                "Your plan or selected material changed during planning. Review the current plan.",
                steps=result.steps,
            )
        else:
            before_brief = brief
            brief = workspaces.keep_edits(baseline, proposed_brief, brief)
            candidate = _work_for_plan(workspace, turn.plan_id)
            if artifact is not None:
                assert result.save is not None
                candidate = result.save.work.model_copy(deep=True)
                candidate.key, candidate.version_id = artifact.lineage_id, artifact.id
                candidate.title, candidate.skipped = artifact.title, []
                stored.log.append(f"v{artifact.version_number}: {result.save.log}")
            else:
                candidate = work.model_copy(deep=True)
                incoming = _work_for_plan(workspace, turn.plan_id)
                # Apply the checked brief and preparatory work. The public draft owns the
                # proposal and outline so incremental model actions cannot silently replace them.
                for field in ("spec", "approach", "approach_inputs", "approach_top", "cards"):
                    setattr(candidate, field, getattr(incoming, field))
                if result.draft is not None:
                    assert material is not None and material.key is not None
                    _accept_draft(candidate, result.draft, material_key=material.key)
                    for item in candidate.outline:
                        item.refs = [ref for ref in item.refs if ref in material.refs]
            candidate.studio_progress = work.studio_progress
            candidate.plan.approval = plan.approval
            if result.question:
                pending = workspaces.Pending(
                    question=result.question["text"],
                    options=result.question["options"],
                    message_id=turn.reply_id,
                )
                stored.pending_by_target[plan.id] = pending
            else:
                pending = None
                stored.pending_by_target.pop(plan.id, None)
            if plan_revision(candidate, brief, pending) != turn.revision or artifact is not None:
                candidate.plan.epoch += 1
                stored.edit_version += 1
            if pending is not None:
                pending.revision = plan_revision(candidate, brief, pending)
            stored.asked_in_a_row = workspace.asked_in_a_row
            if brief != before_brief:
                for other in stored.works:
                    if other.key != work.key and other.plan is not None:
                        other.plan.epoch += 1
                        other.plan.updated_at = _timestamp(utc_now())
                        other_pending = self._work_pending(stored, other)
                        if other_pending:
                            other_pending.revision = plan_revision(other, brief, other_pending)
            if stored.active == work.key:
                stored.active = candidate.key
            stored.works[stored.works.index(work)] = work = candidate
            # The registered run owns pool numbering throughout the turn. Keep pins written
            # by checkpoints even when the agent's final view excludes some of them.
            pool = workspaces.pool_from(stored)
            for item in workspaces.pool_from(workspace).items:
                if pool.adopt(item).ref != item.ref:
                    raise PlanConflictError(
                        "plan_changed", "The evidence pool changed. Plan again."
                    )
            workspaces.store_pool(stored, pool)
        work.plan.attempt = None
        work.plan.updated_at = _timestamp(utc_now())
        if artifact is None and material is None:
            material = reviewed_studio_material(
                session,
                row.workspace_id,
                row.knowledge_base_id,
                work.spec.scope or scope,
                stored,
                work,
            )
        shown = project_work_plan(
            work,
            brief,
            material=material,
            pending=pending,
            availability=self._plan_availability(),
        )
        assert shown is not None
        response = self._commit_turn_reply(
            session,
            row,
            turn.user_id,
            turn.reply_id,
            result,
            artifact=artifact,
            plan=shown,
            edit_version=stored.edit_version,
        )
        self._store_workspace(row, stored, brief)
        return response

    def _creation_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        command: _CREATION_INPUT,
        run: AnswerRun,
        fingerprint: str,
        emit: Events,
    ) -> ArtifactTurnOut:
        turn, workspace, brief = self._admit_plan(
            base_id,
            workspace_id,
            conversation_id,
            command,
            run,
            fingerprint,
        )
        baseline = brief.model_copy(deep=True)
        work = _work_for_plan(workspace, turn.plan_id)
        scope = work.spec.scope or OutputScope()
        try:
            emit({"type": "turn", "conversationId": conversation_id, "messageId": turn.user_id})
            model, effort = self._output_model(work.spec.model_ref, work.spec.effort)
            draft = None
            if turn.authority is not None:
                shown = project_work_plan(
                    work, brief, material=turn.reviewed, attempt_is_live=False
                )
                assert shown is not None
                draft = project_plan_draft(shown)
                assert draft is not None
            try:
                kind = (
                    "start" if isinstance(command, AnswerPlanTurnInput)
                    and command.answer.type == "use_settings" else command.kind
                )
                built = self._run_output_agent(
                    base_id,
                    workspace_id,
                    conversation_id,
                    workspace,
                    brief,
                    scope,
                    model,
                    effort,
                    emit,
                    kind=kind,
                    text=turn.feedback,
                    reply_id=turn.reply_id,
                    web_enabled=work.spec.web,
                    authority=turn.authority,
                    admitted=turn,
                    planning_work=work if draft is None else None,
                    approved=(work, draft) if draft is not None else None,
                )
                result, proposed_brief = built.result, built.brief
            except _PlanSuperseded:
                result = TurnResult(
                    "paused",
                    "Your plan changed during planning. Review the current plan.",
                )
                proposed_brief = baseline

            def finish(session: Session, artifact: ArtifactOut | None = None) -> ArtifactTurnOut:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id
                )
                return self._complete_plan(
                    session,
                    row,
                    turn,
                    workspace,
                    baseline,
                    proposed_brief,
                    result,
                    artifact,
                )

            if result.save is not None:
                if turn.authority is None:
                    raise PlanConflictError("plan_required", _PLAN_REQUIRED)
                response = self._publish_turn(
                    base_id,
                    workspace_id,
                    conversation_id,
                    command.client_request_id,
                    fingerprint,
                    turn.authority,
                    scope,
                    model,
                    effort,
                    proposed_brief,
                    built,
                    turn.feedback,
                    emit,
                    finish,
                    origin="build",
                )
            else:
                if turn.authority is not None:
                    raise ModelError(result.reply or "The approved output could not be generated.")
                guard = locked_plan_run(
                    self.answer_runs, conversation_id, (workspace_id, base_id),
                    server_boot_id=run.server_boot_id, attempt_id=run.attempt_id,
                )
                with guard as live, session_scope(self.sessions) as session:
                    self._require_plan_run(turn, live)
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    response = finish(session)
            assert response.plan is not None
            emit(
                {
                    "type": "plan",
                    "conversationId": conversation_id,
                    "workKey": response.artifact.lineage_id if response.artifact else work.key,
                    "editVersion": response.edit_version,
                    "plan": response.plan.model_dump(mode="json", by_alias=True),
                }
            )
            return response
        except Exception as error:
            stopped = isinstance(error, Stopped)
            self._end_plan_attempt(
                base_id,
                workspace_id,
                conversation_id,
                turn.plan_id,
                turn.request_id,
                run,
                state="stopped" if stopped else "failed",
                detail="Stopped." if stopped else self._turn_error(error),
            )
            raise

    def _read_studio_material(
        self,
        base_id: str,
        material: _OutputMaterial,
        pool: Pool,
        query: str,
        limits: OutputLimits,
        searched: list[SearchHit],
        key_results_read: KeyResultRefs,
    ) -> Notes:
        with self.sessions() as session:
            matches = key_results.retrieve(
                session,
                base_id,
                material.source_revisions.keys(),
                query,
                embedder=self.index.embedder,
            )
            pinned = {item.key: item for item in material.seeds}
            remaining = max(0, limits.seed - len(pinned))
            for match in matches:
                for group in match.groups:
                    fresh = {item.key: item for item in group if item.key not in pinned}
                    if len(fresh) > remaining:
                        continue
                    for item in group:
                        pool.adopt(item)
                        searched.append(SearchHit(item, query, True))
                    pinned.update(fresh)
                    remaining -= len(fresh)
            reading_gaps = self._studio_reading(
                session,
                material.source_revisions,
                pool,
                pinned=list(pinned.values()),
                searched=searched,
            )
        orientation = key_result_refs(matches, pool.items)
        for result_id, (sentence, refs) in orientation.items():
            key_results_read.setdefault(result_id, (sentence, {}))[1].update(refs)
        return replace(
            material.notes,
            extraction_gaps=tuple(reading_gaps),
            key_results=key_result_lines(orientation),
        )

    def _run_output_agent(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        scope: OutputScope,
        model: ModelInfo,
        effort: Effort,
        emit: Events,
        *,
        kind: str,
        text: str,
        reply_id: str,
        web_enabled: bool,
        authority: WriteAuthority | None = None,
        admitted: _PlanTurn | None = None,
        planning_work: workspaces.Work | None = None,
        approved: tuple[workspaces.Work, PlanDraftOut] | None = None,
        rebuild: tuple[workspaces.Work, list[Planned]] | None = None,
        focus: dict | None = None,
        versions: dict[str, int] | None = None,
        saved: dict[str, str] | None = None,
    ) -> _AgentResult:
        limits = OutputLimits()
        agent_limits = self.artifact_limits
        retained = admitted.reviewed if admitted is not None else None
        progress = retained.progress if retained and retained.key else None
        frozen = isinstance(authority, FirstWrite)
        web, web_reason = self._web_tool(web_enabled and not frozen)
        searched: list[SearchHit] = []
        key_results_read: KeyResultRefs = {}
        with self.sessions() as session:
            library = output_library(session, workspace_id, base_id)
            library_title, library_question = library.title, library.question
            manifest = (
                admitted.manifest
                if admitted
                else studio_manifest(
                    workspace_id,
                    base_id,
                    scope,
                    scope_members(session, base_id, scope, strict=rebuild is not None),
                )
            )
            legacy_build = admitted.legacy_build if admitted is not None else None
            original_scope = legacy_build.scope if legacy_build else None
            if original_scope is not None and studio_manifest(
                workspace_id, base_id, original_scope, [],
            )["scope"] != manifest["scope"]:
                original_scope = None
            if progress is not None:
                manifest = progress["manifest"]
                material = (
                    self._output_material(
                        session,
                        base_id,
                        pinned_scope(manifest),
                        frozen_manifest=manifest,
                    )
                    if frozen
                    else _OutputMaterial(
                        {
                            pin["id"]: pin["revision"]
                            for pin in manifest["members"]
                            if pin["kind"] == "source"
                        },
                        Notes(),
                        [],
                        {},
                        [],
                    )
                )
                pinned, notes = restore_studio_material(
                    workspace,
                    progress,
                    limit=workspaces.POOL_LIMIT,
                )
            else:
                material = self._output_material(
                    session,
                    base_id,
                    original_scope or (pinned_scope(manifest) if admitted else scope),
                    strict=admitted is not None or rebuild is not None,
                )
                if not web_enabled:
                    material.seeds = [item for item in material.seeds if item.kind != "web"]
                pinned, notes = None, material.notes
            tool = self._output_search_tool(
                session,
                [] if frozen else list(material.source_revisions),
                f"{library_title}: {library_question}",
            )
            history = self._turn_history(
                session,
                conversation_id,
                agent_limits.history,
                before=admitted.user_id if admitted else None,
            )
            figure_readings = self._reusable_figure_readings(session, base_id)

        pinned_keys = {
            item.key for item in (pinned.items if pinned is not None else material.seeds)
        }
        reader = self._source_reader(material.source_revisions, pinned_keys=pinned_keys)
        local_reading: tuple[list[Evidence], Notes] | None = None
        web_event: dict | None = None

        def evidence_allowed(item: Evidence) -> bool:
            if frozen or pinned is not None:
                return item.key in pinned_keys
            if item.kind == "web" and item.key not in pinned_keys:
                return web is not None and any(hit.item.key == item.key for hit in searched)
            return self._output_evidence_in_scope(item, material.source_revisions, pinned_keys)

        def studio_reading(made: Pool, query: str = "") -> Notes:
            nonlocal local_reading
            if pinned is not None:
                made.items, made.known = [], {}
                made.restore(pinned.items)
                return notes
            if local_reading is not None:
                items, retained_notes = local_reading
                made.items, made.known = [], {}
                made.restore(items)
                return retained_notes
            scoped_notes = self._read_studio_material(
                base_id,
                material,
                made,
                query or library_question or library_title,
                limits,
                searched,
                key_results_read,
            )
            if admitted is not None:
                # Understand and Propose can both read in one turn. Reserve the same pool
                # numbers so their notes, stage refs and citation order agree.
                for item in made.items:
                    if agent.pool.adopt(item).ref != item.ref:
                        raise PlanConflictError("plan_changed", "The evidence pool changed.")
                local_reading = list(made.items), scoped_notes
            return scoped_notes

        def studio_inputs(work: workspaces.Work, pool: Pool):
            nonlocal pinned, notes, manifest, web_event
            if pinned is not None and manifest["web_pass"] != "unavailable":
                studio_reading(pool)
                return notes, manifest
            options = project_plan_options(work, brief)
            query = options.focus or options.title or library_question or library_title
            scoped_notes = studio_reading(pool, query)
            if web_enabled:
                stage = {"type": "stage", "id": "web", "label": "Searching the web"}
                emit({**stage, "state": "running"})
                before_web = len(searched)
                warning = self._studio_web(
                    web,
                    web_reason,
                    query,
                    pool,
                    limits.per_section,
                    searched=searched,
                )
                manifest = {
                    **manifest,
                    "web_pass": "unavailable" if warning else "complete",
                    "web_query": query,
                }
                found = len(searched) - before_web
                web_event = {
                    **stage, "state": "failed" if warning else "done",
                    "detail": warning["reason"] if warning else (
                        f"Reviewed {found} web results" if found else "No web results found"
                    ),
                }
            elif not web_enabled and manifest["web_pass"] != "off":
                raise PlanConflictError("plan_changed", "Review the material with Web turned off.")
            for item in material.seeds:
                pinned_keys.add(item.key)
            # Canonicalize before computing the material key, using the same representation
            # that a future worker will load from the durable pool.
            canonical = [
                workspaces.evidence_from_citation(
                    item.citation.model_copy(update={"ref": item.ref})
                )
                for item in pool.items
            ]
            top = pool.top
            pool.items, pool.known = [], {}
            pool.restore(canonical)
            pool.top = top
            packed = pack_searched(searched, ())
            packed_results = [
                UsedKeyResultOut(id=id_, text=text, refs=sorted(refs.values()))
                for id_, (text, refs) in key_results_read.items()
            ]
            if progress is not None and manifest["web_pass"] in ("complete", "unavailable"):
                packed = [
                    SearchedHitOut.model_validate(hit)
                    for hit in progress["manifest"].get("searched", ())
                    if hit["kind"] != "web"
                ] + packed
                packed_results = [
                    UsedKeyResultOut.model_validate(item)
                    for item in progress["manifest"].get("key_results_used", ())
                ]
            with self.sessions() as session:
                assets = studio_asset_hashes(session, pool)
            manifest = {
                **manifest,
                "assets": assets,
                "discussion_inputs": material.inputs.get(
                    "sessions", manifest.get("discussion_inputs", []),
                ),
                "unit_evidence_counts": (
                    {item.unit_id: item.evidence_count for item in material.snapshots}
                    if material.inputs else manifest.get("unit_evidence_counts", {})
                ),
                "searched": [hit.model_dump(mode="json") for hit in packed],
                "key_results_used": [item.model_dump(mode="json") for item in packed_results],
            }
            if legacy_build is not None:
                manifest |= {
                    "legacy_scope": {
                        "selection": (original_scope or scope).model_dump(),
                        "inputs": material.inputs,
                    },
                    "legacy_web": web_enabled,
                }
            pinned = Pool(pool.limit)
            pinned.restore(pool.items)
            pinned_keys.update(pinned.known)
            notes = scoped_notes
            return notes, manifest

        target_work = planning_work
        if approved is not None:
            target_work = approved[0]
        elif rebuild is not None:
            target_work = rebuild[0]
        with usage.job(
            "outputs", detail=target_work.spec.kind if target_work else None, ref=conversation_id,
        ):
            budget = RunBudget(
                agent_limits.searches,
                agent_limits.searches,
                agent_limits.calls,
                agent_limits.calls,
                phase="turn",
            )
            gateway = BudgetedGateway(self.models, budget)

            def make_studio_agents(shape: str) -> StudioWorkAgents:
                return StudioWorkAgents(
                    gateway,
                    shape,
                    limits,
                    tools=[tool, *([web] if web else [])],
                    asset_reader=self.asset_reader,
                    reusable_figures=figure_readings,
                )

            def prepare(work: workspaces.Work, pool: Pool):
                options = project_plan_options(work, brief)
                if work.spec.scope is None:
                    options = options.model_copy(update={"scope": scope, "web": web_enabled})
                agents = StudioAgents(
                    gateway,
                    limits,
                    use=options.use,
                    renderer=self.slide_renderer,
                    asset_reader=self.asset_reader,
                    reusable_figures=figure_readings,
                )

                def checkpoint(state: dict) -> None:
                    nonlocal progress, web_event
                    if admitted is not None:
                        full = Pool(agent.pool.limit)
                        full.restore(agent.pool.items)
                        for item in pool.items:
                            if full.adopt(item).ref != item.ref:
                                raise PlanConflictError(
                                    "plan_changed", "The evidence pool changed."
                                )
                        self._checkpoint_plan(
                            base_id,
                            workspace_id,
                            conversation_id,
                            admitted,
                            state,
                            full,
                        )
                    progress = state
                    work.studio_progress = state
                    if web_event is not None:
                        emit(web_event)
                        web_event = None

                scoped_notes, manifest = studio_inputs(work, pool)
                spec = workspaces.spec_of(brief, work)
                if work.build_key and work.version_id is None and not brief.constraints:
                    # Older builds supplied their focus directly. Keep this representation
                    # while a restored draft has the same structured brief.
                    spec = replace(spec, brief=options.focus)
                if spec.kind == "slides" and isinstance(authority, ParentWrite):
                    parent_text = (saved or {}).get(authority.lineage_id, "")
                    covers = studio_format.split_slides(parent_text)
                    if covers:
                        style = studio_format.parse_slide(covers[0]).attrs.get("style")
                        spec = replace(spec, deck_style=style)
                prepared = agents.prepare(
                    model,
                    effort,
                    spec,
                    pool,
                    scoped_notes,
                    manifest=manifest,
                    resume=work.studio_progress or progress,
                    emit=emit,
                    checkpoint=checkpoint,
                    legacy_outline=tuple(
                        Planned(row.heading, row.goal, id=row.id)
                        for row in (legacy_build.outline or ())
                    ) if legacy_build else (),
                )
                return agents, prepared, options, checkpoint

            def prepare_work_cb(work: workspaces.Work, pool: Pool) -> Prepared:
                return prepare(work, pool)[1]

            def propose_plan(work: workspaces.Work, pool: Pool, feedback: str) -> PlanDraftOut:
                if work.plan is None and work.version_id is None:
                    work.plan = workspaces.PlanRecord(id=work.key, epoch=1)
                    options = project_plan_options(work, brief).model_copy(
                        update={
                            "model": model.ref,
                            "effort": effort,
                            "scope": scope,
                            "web": web_enabled,
                        }
                    )
                    work.spec = work_spec_for_options(options, work.spec)
                shown = project_work_plan(work, brief)
                previous = project_plan_draft(shown) if shown is not None else None
                order_fixed = bool(work.plan and work.plan.order_edited)
                rows = shown.outline or [] if shown is not None else []
                fixed = tuple(row for row in rows if order_fixed or row.edited_fields)
                studio, prepared, options, checkpoint = prepare(work, pool)
                return studio.propose(
                    prepared,
                    model=model,
                    effort=effort,
                    options=options,
                    previous=previous,
                    feedback=feedback,
                    fixed_rows=fixed,
                    order_fixed=order_fixed,
                    coverage_fixed=bool(work.plan and work.plan.coverage_edited),
                    emit=emit,
                    checkpoint=checkpoint,
                )

            def write_plan(
                work: workspaces.Work,
                pool: Pool,
                plan: PlanDraftOut,
                permit: WriteAuthority,
            ) -> BuiltOutput:
                if permit != authority:
                    raise PlanConflictError(
                        "plan_required", "This work has no current write authority."
                    )
                studio, prepared, options, checkpoint = prepare(work, pool)
                revision = permit.revision if isinstance(permit, FirstWrite) else permit.parent_id
                return studio.generate(
                    prepared,
                    model=model,
                    effort=effort,
                    options=options,
                    plan=plan,
                    revision=revision,
                    authority=permit,
                    emit=emit,
                    checkpoint=checkpoint,
                )

            agent = artifact_agent.ArtifactAgent(
                artifact_agent.TurnContext(
                    workspace=workspace,
                    brief=brief,
                    gateway=gateway,
                    budget=budget,
                    model=model,
                    effort=effort,
                    make_studio_agents=make_studio_agents,
                    studio_reading=studio_reading,
                    notes=notes,
                    library_tool=tool,
                    web_tool=web,
                    web_reason=web_reason or "",
                    reader=reader,
                    library=library_title,
                    kind=kind,
                    text=text,
                    focus=focus,
                    history=history,
                    reply_id=reply_id,
                    versions=versions or {},
                    saved=saved or {},
                    seeds=material.seeds[: limits.seed],
                    emit=emit,
                    limits=agent_limits,
                    evidence_allowed=evidence_allowed,
                    searched=searched,
                    authority=authority,
                    prepare_work=prepare_work_cb,
                    propose_plan=propose_plan,
                    write_plan=write_plan,
                )
            )
            if isinstance(authority, ParentWrite):
                # Checkpoint-only evidence belongs to the plan that read it. Keep the pool
                # numbers for bound citations and any material eligible for this saved turn.
                checkpoint_refs = {
                    ref for work in workspace.works
                    for ref in (work.studio_progress or {}).get("pool_refs", ())
                }
                bound = {
                    ref for work in workspace.works if work.version_id is not None
                    for ref in (
                        *work.version_map, *workspaces.approach_refs(work.approach),
                        *(ref for row in work.outline for ref in row.refs),
                        *(ref for card in work.cards for ref in card.refs),
                    )
                }
                agent.pool.items = [
                    item for item in agent.pool.items
                    if item.ref not in checkpoint_refs or item.ref in bound
                    or (item.kind == "web" and web is not None) or evidence_allowed(item)
                ]
                agent.pool.known = {item.key: item for item in agent.pool.items}
            if isinstance(authority, FirstWrite) and approved is not None:
                result = agent.generate(approved[0], plan=approved[1], authority=authority)
            elif planning_work is not None:
                result = agent.plan(planning_work, feedback=text)
            elif rebuild is not None:
                result = agent.rebuild(*rebuild)
            else:
                result = agent.run()
            return _AgentResult(result, agent.brief, material, searched, key_results_read, progress)

    def _saved_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        command: SavedArtifactTurnInput,
        fingerprint: str,
        emit: Events,
        *,
        legacy_fingerprint: str | None,
        legacy_title: str | None,
        expected_parent: str | None,
    ) -> ArtifactTurnOut:
        with self.sessions() as session:
            output_library(session, workspace_id, base_id)
            row = session.get(ArtifactConversation, conversation_id)
            needs_conversation = row is None and legacy_fingerprint is not None
            if needs_conversation:
                if expected_parent is None or command.build is None:
                    raise PlanConflictError("plan_required", _PLAN_REQUIRED)
                parent = output_version(
                    session, workspace_id, base_id, expected_parent, latest=True
                )
                shown = artifact_detail(session, parent)
                if shown.lineage_id != command.target_lineage_id or shown.conversation_id not in (
                    None,
                    conversation_id,
                ):
                    raise LookupError("This artifact was not found in this conversation.")
        if needs_conversation:
            self.create_artifact_conversation(
                base_id,
                workspace_id,
                CreateArtifactConversationInput(
                    id=conversation_id,
                    title=legacy_title,
                    scope=command.build.scope,
                ),
            )

        with self.sessions() as session:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._visible_workspace(session, row)
            baseline = workspace.model_copy(deep=True)
            baseline_brief = brief.model_copy(deep=True)
            if command.rebuild is not None:
                parent = output_version(
                    session,
                    workspace_id,
                    base_id,
                    command.rebuild.artifact_id,
                    latest=True,
                )
                if command.target_lineage_id not in (None, parent.lineage_id):
                    raise LookupError("This artifact was not found in the targeted output.")
                target, expected_parent = parent.lineage_id, parent.id
            else:
                target = command.target_lineage_id or workspace.active
            works = {work.key: work for work in workspace.works}
            if target is not None:
                works.setdefault(target, None)
            scope = self._conversation_scope(row)
            versions, saved = {}, {}
            authority = None
            parent_shown = None
            for lineage, work in works.items():
                if work is not None and work.version_id is None:
                    if lineage == target and command.build is None and command.rebuild is None:
                        if work.plan is not None:
                            pending = self._work_pending(workspace, work)
                            raise WorkPlanConflict(
                                "question_changed" if pending else "plan_changed",
                                "Answer the pending planning question."
                                if pending else "Open the plan to update it.",
                                work,
                                revision=plan_revision(work, brief, pending),
                                edit_version=workspace.edit_version,
                            )
                        workspace.active = lineage
                        scope = work.spec.scope or scope
                        continue
                    if lineage != target:
                        continue
                head = lineage_head(session, workspace_id, base_id, lineage)
                if head is None:
                    if lineage == target:
                        raise LookupError("This artifact was not found in this conversation.")
                    continue
                checks = latest_check_records(session, head)
                shown = artifact_detail(session, head, check_records=checks)
                if shown.conversation_id not in (None, conversation_id):
                    if lineage == target:
                        raise LookupError("This artifact was not found in this conversation.")
                    continue
                if lineage == target:
                    if expected_parent is not None and expected_parent != head.id:
                        raise ArtifactConflictError(
                            "That is not the latest version of this output. Open the latest one."
                        )
                    if command.build is None:
                        self._require_agent_output(head)
                    authority = ParentWrite(
                        workspace_id, base_id, conversation_id, lineage, head.id
                    )
                    workspace.active = lineage
                    scope = workspace.refinement_scopes.get(lineage) or shown.scope or scope
                    parent_shown = shown
                if work is None:
                    workspaces.import_work(shown, checks, workspace, brief)
                else:
                    workspaces.sync(work, shown, checks, workspace, brief)
                versions[lineage], saved[lineage] = shown.version_number, shown.content

        work = workspaces.active_work(workspace)
        options = command.build
        rebuild = None
        if options is not None or command.rebuild is not None:
            if authority is None or work is None or parent_shown is None:
                raise LookupError("This artifact was not found in this conversation.")
            if options is not None:
                current = project_plan_options(work, brief)
                scope = options.scope
                fields = options.model_dump(exclude={"outline"})
                if options.type == current.type and "theme" not in options.model_fields_set:
                    fields["theme"] = current.theme
                new = PlanOptionsOut.model_validate(
                    current.model_dump()
                    | fields
                    | {
                        "model": command.model,
                        "effort": command.effort,
                    }
                )
                same_type = current.type == new.type
                if not same_type and work.plan is not None:
                    raise PlanConflictError(
                        "plan_changed", "Start a separate plan for another type.",
                    )
                current_rows = list(work.outline) if same_type else []
                matching_outline = (
                    same_type
                    and (parent_shown.brief == options.focus)
                    and (current.length, current.language, current.use)
                    == (new.length, new.language, new.use)
                )
                work.spec = work_spec_for_options(new, work.spec)
                work.outline = current_rows
                submitted = options.outline
                if not matching_outline and submitted is None:
                    current_rows = []
            else:
                current_rows = list(work.outline)
                submitted = command.rebuild.outline
            by_id = {row.id: row for row in current_rows}
            if len(parent_shown.outline) == len(current_rows):
                # The reader can give an old version positional IDs even when its
                # conversation already assigned different IDs to those same rows.
                for shown_row, stored_row in zip(parent_shown.outline, current_rows, strict=True):
                    if shown_row.heading == stored_row.heading and shown_row.id:
                        by_id.setdefault(shown_row.id, stored_row)
            if submitted is not None:
                rows = []
                for position, item in enumerate(submitted):
                    previous = by_id.get(item.id)
                    if not item.id and position < len(current_rows):
                        previous = current_rows[position]
                    rows.append(
                        Planned(
                            item.heading,
                            item.goal,
                            (item.layout or "")
                            if "layout" in item.model_fields_set
                            else previous.layout
                            if previous
                            else "",
                            id=previous.id if previous else item.id,
                            notes=item.notes
                            if "notes" in item.model_fields_set
                            else previous.notes
                            if previous
                            else "",
                        )
                    )
            else:
                rows = [
                    Planned(row.heading, row.goal, row.layout, id=row.id, notes=row.notes)
                    for row in current_rows
                    if SHAPES[work.spec.kind].outline
                ]
            rebuild = work, rows

        model_ref = command.model or (work.spec.model_ref if work is not None else None)
        requested_effort = command.effort or (work.spec.effort if work is not None else None)
        model, effort = self._output_model(model_ref, requested_effort)
        user_id, reply_id = _id("acm"), _id("acm")
        emit({"type": "turn", "conversationId": conversation_id, "messageId": user_id})
        web_enabled = options.web if options is not None else command.web
        def keep_failure(error: Exception) -> None:
            with session_scope(self.sessions) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id
                )
                if self._user_message(session, row, command.client_request_id) is None:
                    self._add_turn_user(session, row, command, user_id, command.text, fingerprint)
                    detail = self._turn_error(error)
                    kind = "stopped" if isinstance(error, Stopped) else "failed"
                    self._add_turn_reply(
                        session,
                        row,
                        user_id,
                        reply_id,
                        kind=kind,
                        text=detail,
                        data={"error": detail} if kind == "failed" else {},
                    )
                    row.updated_at = utc_now()

        try:
            built = self._run_output_agent(
                base_id,
                workspace_id,
                conversation_id,
                workspace,
                brief,
                scope,
                model,
                effort,
                emit,
                kind=command.kind,
                text=command.text,
                reply_id=reply_id,
                web_enabled=web_enabled,
                authority=authority,
                rebuild=rebuild,
                focus=command.focus.model_dump() if command.focus else None,
                versions=versions,
                saved=saved,
            )
        except Exception as error:
            keep_failure(error)
            raise

        def finish(session: Session, artifact: ArtifactOut | None = None) -> ArtifactTurnOut:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            earlier = self._earlier_turn(session, row, ArtifactTurnInput(command), fingerprint)
            if earlier is not None:
                return earlier
            stored, current_brief = self._load_workspace(row)
            self._reconcile_plan_publications(session, row, stored)
            merged = workspaces.keep_edits(baseline_brief, built.brief, current_brief)
            result = built.result
            candidate = result.save.work if result.save else workspaces.active_work(workspace)
            plan = None
            stored_work = None
            if candidate is not None:
                before = next((item for item in baseline.works if item.key == candidate.key), None)
                stored_work = next(
                    (item for item in stored.works if item.key == candidate.key), None
                )
                if before is not None and stored_work is not None:
                    candidate.spec = workspaces.keep_spec_edits(
                        before.spec,
                        candidate.spec,
                        stored_work.spec,
                    )
                    if (
                        stored_work.plan is not None
                        and stored_work.version_id is None
                        and (
                            before.plan is None
                            or plan_revision(
                                stored_work, current_brief, self._work_pending(stored, stored_work)
                            )
                            != plan_revision(
                                before, baseline_brief, self._work_pending(baseline, before)
                            )
                        )
                    ):
                        result = TurnResult("paused", "Your plan changed. Review the current plan.")
                        candidate, merged = stored_work, current_brief
                        stored.pending_by_target = dict(stored.pending_by_target)
                if result.draft is not None:
                    material_key = (candidate.studio_progress or {}).get("material_key")
                    if material_key and candidate.plan is not None:
                        _accept_draft(candidate, result.draft, material_key=material_key)
                if artifact is not None:
                    candidate = candidate.model_copy(
                        update={
                            "key": artifact.lineage_id,
                            "version_id": artifact.id,
                            "title": artifact.title,
                            "skipped": [],
                        }
                    )
                    stored.log.append(f"v{artifact.version_number}: {result.save.log}")
                if stored_work is None:
                    stored.works.append(candidate)
                else:
                    stored.works[stored.works.index(stored_work)] = candidate
                stored.active = candidate.key
            elif stored.active is None:
                stored.active = workspace.active

            if candidate is not stored_work or result is built.result:
                stored.asked_in_a_row = workspace.asked_in_a_row
                for key in {workspaces.pending_target(candidate), workspaces.CONVERSATION_PENDING}:
                    before = baseline.pending_by_target.get(key)
                    pending = workspace.pending_by_target.get(key)
                    if pending == before or stored.pending_by_target.get(key) != before:
                        continue
                    if pending is None:
                        stored.pending_by_target.pop(key, None)
                    else:
                        stored.pending_by_target[key] = pending
                if workspace.pool != baseline.pool:
                    pool = workspaces.pool_from(stored)
                    for item in workspaces.pool_from(workspace).items:
                        if pool.adopt(item).ref != item.ref:
                            raise ArtifactConflictError(
                                "The evidence pool changed. Try the turn again."
                            )
                    workspaces.store_pool(stored, pool)
            if (
                merged != current_brief
                or stored.works != baseline.works
                or (stored.pending_by_target != baseline.pending_by_target)
            ):
                stored.edit_version += 1
            if merged != current_brief:
                for item in stored.works:
                    if item.plan is not None:
                        item.plan.epoch += 1
            if candidate is not None and candidate.plan is not None and result.draft is not None:
                candidate.plan.updated_at = _timestamp(utc_now())
                material = reviewed_studio_material(
                    session,
                    workspace_id,
                    base_id,
                    scope,
                    stored,
                    candidate,
                )
                plan = project_work_plan(
                    candidate,
                    merged,
                    material=material,
                    pending=self._work_pending(stored, candidate),
                    availability=self._plan_availability(),
                )
            if row.title == NEW_CONVERSATION:
                row.title = (
                    artifact.title
                    if artifact is not None and options is not None
                    else " ".join(command.text.split())[:160] or NEW_CONVERSATION
                )
            self._add_turn_user(session, row, command, user_id, command.text, fingerprint)
            result_out = self._commit_turn_reply(
                session,
                row,
                user_id,
                reply_id,
                result,
                artifact=artifact,
                plan=plan,
                edit_version=stored.edit_version if plan else None,
            )
            self._store_workspace(row, stored, merged)
            return result_out

        try:
            if built.result.save is not None:
                if authority is None:
                    raise PlanConflictError("plan_required", _PLAN_REQUIRED)
                response = self._publish_turn(
                    base_id,
                    workspace_id,
                    conversation_id,
                    command.client_request_id,
                    legacy_fingerprint or fingerprint,
                    authority,
                    scope,
                    model,
                    effort,
                    built.brief,
                    built,
                    command.text,
                    emit,
                    finish,
                    origin="rebuild" if rebuild is not None else "revise",
                )
            else:
                check_stop()
                with session_scope(self.sessions) as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    response = finish(session)
        except Exception as error:
            keep_failure(error)
            raise
        if response.plan is not None:
            emit(
                {
                    "type": "plan",
                    "conversationId": conversation_id,
                    "workKey": workspace.active,
                    "editVersion": response.edit_version,
                    "plan": response.plan.model_dump(mode="json", by_alias=True),
                }
            )
        return response

    def _turn_history(
        self,
        session: Session,
        conversation_id: str,
        count: int,
        *,
        before: str | None = None,
    ) -> list[tuple[str, str]]:
        messages = session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == conversation_id, live_artifact_message())
            .order_by(ArtifactMessage.created_at, ArtifactMessage.role.desc(), ArtifactMessage.id)
        ).all()
        pairs: list[tuple[str, str]] = []
        users = {}
        asked = None
        for message in messages:
            if message.id == before:
                break
            if message.role == "user":
                users[message.id] = asked = message.text
            elif message.text.strip():
                data = json.loads(message.payload_json or "{}")
                text = users.get(data["to"]) if "to" in data else asked
                if text is not None:
                    pairs.append((text, message.text))
                asked = None
        return pairs[-count:]

    @staticmethod
    def _turn_error(error: Exception) -> str:
        if isinstance(error, (ModelError, ValueError, LookupError)):
            return str(error)
        if isinstance(error, Stopped):
            return "Stopped."
        return "The message could not be handled."

    def _commit_turn_reply(
        self,
        session: Session,
        row: ArtifactConversation,
        user_id: str,
        reply_id: str,
        result: TurnResult,
        *,
        artifact: ArtifactOut | None,
        plan: WorkPlanOut | None,
        edit_version: int | None,
    ) -> ArtifactTurnOut:
        extra: dict[str, Any] = {"steps": result.steps, "outcome": result.outcome}
        if result.question:
            extra.update(question=result.question, options=result.question["options"])
        if result.changes:
            extra["changes"] = result.changes
        if plan is not None:
            extra.update(plan=plan.model_dump(mode="json", by_alias=True), editVersion=edit_version)
        reply = self._add_turn_reply(
            session,
            row,
            user_id,
            reply_id,
            kind=_REPLY_KIND[result.outcome],
            text=result.reply,
            data=extra,
            artifact_id=artifact.id if artifact else None,
        )
        return ArtifactTurnOut(
            outcome=result.outcome,
            reply=result.reply,
            question=ArtifactQuestionOut(**result.question) if result.question else None,
            artifact=artifact,
            changes=[ArtifactChangeOut(**item) for item in result.changes],
            message=self._artifact_message_out(reply),
            plan=plan,
            edit_version=edit_version,
        )

    def _publish_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        request_id: str,
        fingerprint: str,
        authority: WriteAuthority,
        scope: OutputScope,
        model: ModelInfo,
        effort: Effort,
        brief: workspaces.ConversationBrief,
        attempt: _AgentResult,
        instruction: str,
        emit: Events,
        finish: Callable[[Session, ArtifactOut], ArtifactTurnOut],
        *,
        origin: str,
    ) -> ArtifactTurnOut:
        request = attempt.result.save
        assert request is not None
        work, built = request.work, request.built
        if isinstance(authority, FirstWrite):
            if authority.work_key != work.key or work.version_id is not None:
                raise PlanConflictError("plan_required", "Approve the plan that will be saved.")
            parent = None
        else:
            if (authority.lineage_id, authority.parent_id) != (work.key, work.version_id):
                raise ArtifactConflictError("This output is outside the turn's verified version.")
            parent = authority.parent_id
        spec = workspaces.spec_of(brief, work)
        preferences = {"emphasize": brief.emphasize, "avoid": brief.avoid}
        title = self.artifact_conversation_title(base_id, workspace_id, conversation_id)
        cited = [workspaces.evidence_from_citation(item) for item in built.citations]
        if attempt.progress is not None:
            manifest = attempt.progress["manifest"]
            cited_keys = {
                (item.kind, item.citation.source_id or None, item.url, item.locator)
                for item in cited
            }
            searched = [
                hit
                for value in manifest.get("searched", ())
                if (hit := SearchedHitOut.model_validate(value))
                and (hit.kind, hit.source_id, hit.url, hit.locator) not in cited_keys
            ]
            refs = {ref: index for index, ref in enumerate(built.pool_refs, 1)}
            results = [
                UsedKeyResultOut.model_validate(
                    value | {"refs": [refs[ref] for ref in value["refs"] if ref in refs]}
                )
                for value in manifest.get("key_results_used", ())
            ]
        else:
            searched = pack_searched(attempt.searched, cited)
            results = pack_key_results(attempt.key_results, cited)
        response = None

        def publish(session: Session, artifact: ArtifactOut) -> None:
            nonlocal response
            response = finish(session, artifact)

        emit({"type": "saving"})
        artifact = commit_version(
            self.sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=request_id,
            fingerprint=fingerprint,
            parent_id=parent,
            kind=spec.kind,
            style=spec.output_type,
            audience=project_plan_options(work, brief).audience,
            title=built.title,
            brief=spec.brief,
            origin=origin,
            content=built.content,
            scope=scope.model_dump(),
            inputs=attempt.material.inputs,
            outline=built.outline,
            citations=[item.model_dump() for item in built.citations],
            snapshots=attempt.material.snapshots,
            checks=built.checks,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            made_by={
                "generator": "gunther.studio.v1"
                if origin != "revise"
                else "gunther.artifact-agent.v1",
                "skill": built.skill,
                "approach": built.approach,
                "use": work.spec.use.text
                if work.spec.use and work.spec.use.text in ("talk", "read")
                else None,
                "length": spec.length,
                "language": spec.language,
                "conversation_id": conversation_id,
                "conversation_title": title,
                "request": instruction,
                "reply": request.summary,
                "changes": request.changes,
                "searched": searched,
                "key_results_used": results,
                "work": work.model_dump(mode="json"),
                "effective_preferences": preferences,
            },
            publish=publish,
            voices=work.spec.voices,
            first_write=(authority if isinstance(authority, FirstWrite) else None),
            answer_runs=self.answer_runs,
            asset_reader=self.asset_reader,
        )
        return response or self._published_turn_out(artifact)
