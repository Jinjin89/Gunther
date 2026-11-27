"""Open, project, import and recover artifact conversations and their workspaces."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from gunther import artifact_workspace as workspaces
from gunther.answer_runs import AnswerRun, output_key
from gunther.artifact_plans import (
    PlanAvailability,
    PlanConflictError,
    PlanIssue,
    PlanPatchResult,
    PlanValidationError,
    apply_work_plan_patch,
    plan_revision,
    project_plan_options,
    project_studio_work_summary,
    project_work_plan,
)
from gunther.artifact_read import artifact_detail, latest_check_records, live_artifact_message
from gunther.artifact_versions import (
    ArtifactConflictError,
    WorkPlanConflict,
    lineage_head,
    output_library,
)
from gunther.database import session_scope
from gunther.models import Artifact, ArtifactConversation, ArtifactMessage, utc_now
from gunther.output_material import reviewed_studio_material, scope_members, studio_manifest
from gunther.output_types import output_type_for
from gunther.schemas import (
    ArtifactBriefOut,
    ArtifactBriefStateOut,
    ArtifactConversationOut,
    ArtifactConversationSummaryOut,
    ArtifactLastTurnOut,
    ArtifactLineOut,
    ArtifactMessageOut,
    ArtifactPartOut,
    ArtifactPendingOut,
    ArtifactSpecOut,
    ArtifactStepOut,
    ArtifactWorkOut,
    BuildTurnInput,
    CreateArtifactConversationInput,
    EditArtifactBriefInput,
    ImportArtifactConversationsInput,
    ImportedArtifactConversation,
    OutputScope,
    PlanOptionsOut,
    SavedArtifactTurnInput,
    StudioWorkSummaryOut,
    UpdateArtifactConversationInput,
    WorkPlanOut,
    WorkPlanPatchInput,
)
from gunther.service_records import _id, _timestamp


def _device_time(value: str | None) -> datetime | None:
    """A time a device wrote (ISO 8601, usually ending in Z) as the UTC time the database
    keeps; None when it is missing or cannot be read."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


@dataclass(frozen=True, slots=True)
class _OwnedVersion:
    """A version, as much of it as tells which conversation it belongs to and what was asked."""

    id: str
    lineage_id: str
    created_at: datetime
    client_request_id: str
    scope_json: str
    conversation_title: str | None
    request: str | None


class ArtifactConversations:
    # Conversations of the Artifacts agent (see artifact_workspace) ------------------------------

    @staticmethod
    def _artifact_conversation_row(
        session: Session, workspace_id: str, base_id: str, conversation_id: str
    ) -> ArtifactConversation:
        row = session.scalar(
            select(ArtifactConversation).where(
                ArtifactConversation.id == conversation_id,
                ArtifactConversation.workspace_id == workspace_id,
                ArtifactConversation.knowledge_base_id == base_id,
                ArtifactConversation.trashed_at.is_(None),
            )
        )
        if row is None:
            raise LookupError(f"Conversation {conversation_id} was not found")
        return row

    @staticmethod
    def _load_workspace(
        row: ArtifactConversation,
    ) -> tuple[workspaces.Workspace, workspaces.ConversationBrief]:
        """A conversation's workspace and its brief, as stored."""

        return workspaces.load_workspace(row.state_json), workspaces.load_brief(row.brief_json)

    @staticmethod
    def _live_lineages(session: Session, row: ArtifactConversation) -> set[str]:
        return set(session.scalars(
            select(Artifact.lineage_id).where(
                Artifact.workspace_id == row.workspace_id,
                Artifact.knowledge_base_id == row.knowledge_base_id,
                Artifact.trashed_at.is_(None),
            ).distinct()
        ))

    @staticmethod
    def _hidden_works(workspace: workspaces.Workspace, live: set[str]) -> set[str]:
        return {work.key for work in workspace.works if work.version_id and work.key not in live}

    def _visible_workspace(
        self, session: Session, row: ArtifactConversation, live: set[str] | None = None,
    ) -> tuple[workspaces.Workspace, workspaces.ConversationBrief]:
        workspace, brief = self._load_workspace(row)
        self._reconcile_plan_publications(session, row, workspace)
        if live is None:
            live = self._live_lineages(session, row)
        hidden = self._hidden_works(workspace, live) | (workspace.refinement_scopes.keys() - live)
        workspaces.forget_works(workspace, hidden)
        return workspace, brief

    @staticmethod
    def _reconcile_plan_publications(
        session: Session, row: ArtifactConversation, workspace: workspaces.Workspace,
    ) -> None:
        """A sealed first version takes precedence over a leftover attempt record."""

        for work in workspace.works:
            approval = work.plan.approval if work.plan is not None else None
            if work.version_id is not None or approval is None:
                continue
            artifact = session.scalar(select(Artifact).where(
                Artifact.workspace_id == row.workspace_id,
                Artifact.knowledge_base_id == row.knowledge_base_id,
                Artifact.client_request_id == approval.request_id,
                Artifact.version_number == 1,
                Artifact.trashed_at.is_(None),
            ))
            if artifact is None:
                continue
            shown = artifact_detail(session, artifact)
            creation = getattr(shown.provenance, "creation_plan", None)
            if (
                creation is None or shown.conversation_id != row.id
                or creation.plan_id != work.plan.id or creation.revision != approval.revision
                or creation.approval_message_id != approval.user_message_id
                or creation.material_key != approval.material_key
            ):
                continue
            if workspace.active == work.key:
                workspace.active = shown.lineage_id
            work.key, work.version_id = shown.lineage_id, shown.id
            work.plan.attempt = None
            approval.first_version_id = shown.id

    def _plan_availability(self) -> PlanAvailability:
        gateway = self.models
        models = {
            model.ref: model.profile.dialect.levels for model in gateway.models
        } if gateway else {}
        chosen = (gateway.for_role("outputs") or gateway.for_role("ask")) if gateway else None
        return PlanAvailability(
            models, (chosen[0].ref, chosen[1]) if chosen else None,
            self.output_voices() if self.output_voices else None,
        )

    def _attempt_is_live(
        self, row: ArtifactConversation, attempt: workspaces.PlanAttemptRecord,
    ) -> bool:
        return self.answer_runs.matches(
            output_key(row.id), scope=(row.workspace_id, row.knowledge_base_id),
            server_boot_id=attempt.server_boot_id, attempt_id=attempt.attempt_id,
        )

    @staticmethod
    def _live_attempts(
        runs: Iterable[AnswerRun], scope: tuple[str, str], conversation_id: str,
    ) -> set[tuple[str, str]]:
        key = output_key(conversation_id)
        return {
            (run.server_boot_id, run.attempt_id) for run in runs
            if run.session_id == key and run.scope == scope and not run.finished
        }

    @staticmethod
    def _settle_interrupted(
        workspace: workspaces.Workspace, live_attempts: set[tuple[str, str]],
    ) -> None:
        # The registry snapshot was taken before acquiring the SQLite writer lock.
        for work in workspace.works:
            plan = work.plan
            attempt = plan.attempt if plan is not None else None
            if (
                work.version_id is None and attempt and attempt.state == "running"
                and (attempt.server_boot_id, attempt.attempt_id) not in live_attempts
            ):
                attempt.state = "interrupted"
                approval = plan.approval
                if (
                    approval and approval.attempt_id == attempt.attempt_id
                    and not approval.first_version_id
                ):
                    plan.approval = None

    @staticmethod
    def _work_pending(workspace: workspaces.Workspace, work: workspaces.Work):
        return workspace.pending_by_target.get(workspaces.pending_target(work))

    def _plan_projection_inputs(
        self, session: Session, row: ArtifactConversation, workspace: workspaces.Workspace,
        work: workspaces.Work, availability: PlanAvailability,
        live_attempts: set[tuple[str, str]] | None = None,
    ) -> dict:
        if work.plan is None:
            return {}
        attempt = work.plan.attempt
        live = None
        if attempt and attempt.state == "running" and work.version_id is None:
            live = (
                self._attempt_is_live(row, attempt) if live_attempts is None
                else (attempt.server_boot_id, attempt.attempt_id) in live_attempts
            )
        return {
            "material": (
                reviewed_studio_material(
                    session, row.workspace_id, row.knowledge_base_id,
                    work.spec.scope or self._conversation_scope(row), workspace, work,
                    frozen=bool(live and attempt.phase == "generate"),
                )
                if work.version_id is None else None
            ),
            "pending": self._work_pending(workspace, work),
            "availability": availability,
            "attempt_is_live": live,
        }

    @staticmethod
    def _store_workspace(
        row: ArtifactConversation,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        at: datetime | None = None,
    ) -> None:
        """Keep the workspace and the brief in the conversation, which counts as changed."""

        row.state_json = workspaces.dump_workspace(workspace)
        row.brief_json = workspaces.dump_brief(brief)
        row.updated_at = at or utc_now()

    @staticmethod
    def _conversation_scope(row: ArtifactConversation) -> OutputScope:
        try:
            return OutputScope.model_validate(json.loads(row.scope_json))
        except ValueError:
            return OutputScope()

    @staticmethod
    def _artifact_line_out(line: workspaces.Line | None) -> ArtifactLineOut | None:
        return ArtifactLineOut(text=line.text, mark=line.mark) if line else None

    @classmethod
    def _artifact_brief_out(cls, brief: workspaces.ConversationBrief) -> ArtifactBriefOut:
        return ArtifactBriefOut(
            goal=cls._artifact_line_out(brief.goal),
            audience=cls._artifact_line_out(brief.audience),
            constraints=[cls._artifact_line_out(line) for line in brief.constraints],
            open=[cls._artifact_line_out(line) for line in brief.open],
            emphasize=brief.emphasize,
            avoid=brief.avoid,
        )

    @classmethod
    def _artifact_spec_out(cls, spec: workspaces.WorkSpec) -> ArtifactSpecOut:
        return ArtifactSpecOut(
            kind=spec.kind,
            use=cls._artifact_line_out(spec.use),
            length=cls._artifact_line_out(spec.length),
            output_type=cls._artifact_line_out(spec.output_type),
        )

    @classmethod
    def _artifact_work_out(
        cls, work: workspaces.Work, brief: workspaces.ConversationBrief,
        plan: WorkPlanOut | None, pending: workspaces.Pending | None,
        options: PlanOptionsOut | None,
    ) -> ArtifactWorkOut:
        approach = workspaces.approach_state(work, brief)
        outline = workspaces.outline_state(work)
        return ArtifactWorkOut(
            key=work.key,
            kind=work.spec.kind,
            title=work.title,
            version_id=work.version_id,
            spec=cls._artifact_spec_out(work.spec),
            approach=ArtifactStepOut(state=approach.state, why=approach.why),
            outline=ArtifactStepOut(state=outline.state, why=outline.why),
            parts=[ArtifactPartOut(**part.model_dump()) for part in workspaces.status(work, brief)],
            pending=cls._pending_out(pending),
            options=options,
            plan=plan,
        )

    @staticmethod
    def _pending_out(pending: workspaces.Pending | None) -> ArtifactPendingOut | None:
        return ArtifactPendingOut.model_validate(pending.model_dump()) if pending else None

    def _legacy_builds(
        self, row: ArtifactConversation, messages: Iterable[ArtifactMessage],
    ) -> dict[str, SavedArtifactTurnInput]:
        """Associate old interrupted drafts with the exact build request that created them."""

        builds = {}
        for message in messages:
            if message.role != "user" or message.kind != "build":
                continue
            with suppress(ValueError):
                payload = json.loads(message.payload_json or "{}")
                if (
                    not isinstance(payload, dict)
                    or not payload.get("build") or payload.get("targetLineageId")
                ):
                    continue
                command = SavedArtifactTurnInput.model_validate({
                    **payload, "kind": "build",
                    "clientRequestId": message.client_request_id or message.id,
                })
                assert command.build is not None
                key = self._output_fingerprint(
                    row.workspace_id, row.knowledge_base_id, "build-draft",
                    command.build.model_dump(mode="json"),
                )
                builds[key] = command
        return builds

    def _legacy_options(
        self, row: ArtifactConversation, brief: workspaces.ConversationBrief,
        work: workspaces.Work, origin: SavedArtifactTurnInput | None,
    ) -> PlanOptionsOut:
        options = project_plan_options(work, brief)
        build = origin.build if origin else None
        spec = work.spec
        return options.model_copy(update={
            "scope": spec.scope or (build.scope if build else self._conversation_scope(row)),
            "focus": build.focus if build and spec.focus is None else options.focus,
            "title": build.title if build and spec.requested_title is None else options.title,
            "audience": (
                build.audience if build and spec.audience_choice is None else options.audience
            ),
            "voices": spec.voices or (build.voices if build else None),
            "web": spec.web if spec.scope is not None or build is None else build.web,
            "model": spec.model_ref or (origin.model if origin else None),
            "effort": spec.effort or (origin.effort if origin else None),
        })

    @staticmethod
    def _artifact_message_out(message: ArtifactMessage) -> ArtifactMessageOut:
        return ArtifactMessageOut(
            id=message.id,
            role=message.role,
            kind=message.kind,
            text=message.text,
            payload=json.loads(message.payload_json or "{}"),
            artifact_id=message.artifact_id,
            client_request_id=message.client_request_id,
            created_at=_timestamp(message.created_at),
        )

    @staticmethod
    def _last_artifact_turn(messages: Iterable[ArtifactMessage]) -> ArtifactLastTurnOut | None:
        user = reply = None
        for message in messages:
            if message.role == "user" and user is None:
                user = message
            elif message.role == "assistant" and reply is None:
                reply = message
            if user is not None and reply is not None:
                break
        if user is None:
            return None
        asked = json.loads(user.payload_json or "{}")
        answered = json.loads(reply.payload_json or "{}") if reply else {}
        matched = answered.get("to") == user.id or (
            answered.get("imported") and reply is not None and reply.created_at >= user.created_at
        )
        return ArtifactLastTurnOut(
            kind=user.kind,
            error=answered.get("error") if matched else None,
            build=BuildTurnInput.model_validate(asked["build"]) if asked.get("build") else None,
            created_at=_timestamp(user.created_at),
        )

    def _artifact_conversation_summary(
        self, row: ArtifactConversation, workspace: workspaces.Workspace,
        studio_works: list[StudioWorkSummaryOut],
        last_turn: ArtifactLastTurnOut | None = None,
    ) -> ArtifactConversationSummaryOut:
        active = workspaces.active_work(workspace)
        output_type = (
            output_type_for(active.spec.kind, active.spec.chosen_output_type) if active else None
        )
        return ArtifactConversationSummaryOut(
            id=row.id,
            title=row.title,
            updated_at=_timestamp(row.updated_at),
            artifact_count=len(workspace.works),
            waiting=bool(workspace.pending_by_target),
            output_type=output_type.label if output_type else None,
            shape=output_type.shape if output_type else None,
            lineage_id=active.key if active and active.version_id else None,
            last_turn=last_turn,
            studio_works=studio_works,
        )

    def _artifact_conversation_summaries(
        self, session: Session, rows: Iterable[ArtifactConversation],
    ) -> list[ArtifactConversationSummaryOut]:
        rows = list(rows)
        if not rows:
            return []
        position = func.row_number().over(
            partition_by=(ArtifactMessage.conversation_id, ArtifactMessage.role),
            order_by=(ArtifactMessage.created_at.desc(), ArtifactMessage.id.desc()),
        ).label("position")
        ranked = select(ArtifactMessage, position).where(
            ArtifactMessage.conversation_id.in_([row.id for row in rows]),
            live_artifact_message(),
        ).subquery()
        message = aliased(ArtifactMessage, ranked)
        recent = defaultdict(list)
        for item in session.scalars(select(message).where(ranked.c.position == 1)):
            recent[item.conversation_id].append(item)
        live = self._live_lineages(session, rows[0])
        availability = self._plan_availability()
        summaries = []
        for row in rows:
            workspace, brief = self._visible_workspace(session, row, live)
            studio_works = [
                project_studio_work_summary(
                    work, brief, updated_at=_timestamp(row.updated_at),
                    **self._plan_projection_inputs(session, row, workspace, work, availability),
                )
                for work in workspace.works
            ]
            summaries.append(self._artifact_conversation_summary(
                row, workspace, studio_works,
                last_turn=self._last_artifact_turn(recent[row.id]),
            ))
        return summaries

    def _artifact_conversation_out(
        self, session: Session, row: ArtifactConversation, *,
        live_attempts: set[tuple[str, str]] | None = None,
        work_key: str | None = None,
    ) -> ArtifactConversationOut:
        workspace, brief = self._visible_workspace(session, row)
        messages = session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == row.id, live_artifact_message())
            # A question and its answer can share a second: the user's message comes first.
            .order_by(ArtifactMessage.created_at, ArtifactMessage.role.desc())
        ).all()
        active = (
            next((work for work in workspace.works if work.key == work_key), None)
            if work_key is not None else workspaces.active_work(workspace)
        )
        pending = self._work_pending(workspace, active) if active else (
            workspace.pending_by_target.get(workspaces.CONVERSATION_PENDING)
            if work_key is None else None
        )
        availability = self._plan_availability()
        works = []
        summaries = []
        legacy = self._legacy_builds(row, messages) if any(
            work.build_key and work.plan is None and work.version_id is None
            for work in workspace.works
        ) else {}
        for work in workspace.works:
            inputs = self._plan_projection_inputs(
                session, row, workspace, work, availability, live_attempts,
            )
            plan = project_work_plan(work, brief, **inputs)
            options = (
                self._legacy_options(row, brief, work, legacy.get(work.build_key))
                if plan is None and work.version_id is None else None
            )
            works.append(self._artifact_work_out(
                work, brief, plan, self._work_pending(workspace, work), options,
            ))
            summaries.append(project_studio_work_summary(
                work, brief, updated_at=_timestamp(row.updated_at), **inputs,
            ))
        return ArtifactConversationOut(
            **self._artifact_conversation_summary(
                row, workspace, summaries, self._last_artifact_turn(reversed(messages)),
            ).model_dump(),
            scope=self._conversation_scope(row),
            refinement_scopes=workspace.refinement_scopes,
            brief=self._artifact_brief_out(brief),
            messages=[self._artifact_message_out(message) for message in messages],
            works=works,
            active=workspace.active,
            pending=self._pending_out(pending),
            archived=row.archived,
            created_at=_timestamp(row.created_at),
            edit_version=workspace.edit_version,
        )

    @staticmethod
    def _owned_versions(
        session: Session, workspace_id: str, base_id: str
    ) -> dict[str, list[_OwnedVersion]]:
        """The library's versions that name a conversation in their provenance, by that
        conversation, oldest first."""

        owned: dict[str, list[_OwnedVersion]] = {}
        rows = session.execute(
            select(
                Artifact.id,
                Artifact.lineage_id,
                Artifact.created_at,
                Artifact.client_request_id,
                Artifact.scope_json,
                Artifact.provenance_json,
            )
            .where(
                Artifact.workspace_id == workspace_id,
                Artifact.knowledge_base_id == base_id,
                Artifact.trashed_at.is_(None),
            )
            .order_by(Artifact.created_at, Artifact.version_number)
        )
        for row in rows:
            provenance = json.loads(row.provenance_json)
            conversation_id = provenance.get("conversation_id")
            if conversation_id:
                owned.setdefault(conversation_id, []).append(
                    _OwnedVersion(
                        row.id,
                        row.lineage_id,
                        row.created_at,
                        row.client_request_id,
                        row.scope_json,
                        provenance.get("conversation_title"),
                        provenance.get("request"),
                    )
                )
        return owned

    def _add_artifact_turn(
        self,
        session: Session,
        conversation_id: str,
        *,
        instruction: str,
        client_request_id: str | None,
        asked_at: datetime,
        answer: str,
        artifact_id: str | None = None,
        error: str | None = None,
    ) -> datetime:
        """A turn a device (or an earlier version of the app) made, as a user message and the
        reply to it, a moment later; the reply's time is returned. ``answer`` is the reply's
        kind. The reply is marked ``imported``, for it was not made by the agent."""

        answered_at = asked_at + timedelta(milliseconds=1)
        session.add(
            ArtifactMessage(
                id=_id("acm"),
                conversation_id=conversation_id,
                role="user",
                kind="message",
                text=instruction,
                payload_json="{}",
                client_request_id=client_request_id,
                created_at=asked_at,
            )
        )
        session.add(
            ArtifactMessage(
                id=_id("acm"),
                conversation_id=conversation_id,
                role="assistant",
                kind=answer,
                text="",
                payload_json=json.dumps(
                    {"imported": True, **({"error": error} if error else {})}, ensure_ascii=False
                ),
                artifact_id=artifact_id,
                created_at=answered_at,
            )
        )
        return answered_at

    @staticmethod
    def _lineages_by_last_use(versions: list[_OwnedVersion]) -> list[str]:
        """The lineages of these versions (oldest first), the one worked on last at the end."""

        order: list[str] = []
        for version in versions:
            if version.lineage_id in order:
                order.remove(version.lineage_id)
            order.append(version.lineage_id)
        return order

    def _import_lineages(
        self,
        session: Session,
        row: ArtifactConversation,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        lineage_ids: list[str],
    ) -> None:
        """Make a work of the newest version of each lineage that has none yet (see
        artifact_workspace.import_work). The last lineage becomes the active one. Versions
        from the old builder cannot be revised, so they are left out."""

        for lineage_id in lineage_ids:
            if any(work.key == lineage_id for work in workspace.works):
                continue
            head = lineage_head(session, row.workspace_id, row.knowledge_base_id, lineage_id)
            if head is None or head.origin == "legacy":
                continue
            checks = latest_check_records(session, head)
            workspaces.import_work(
                artifact_detail(session, head, check_records=checks),
                checks,
                workspace,
                brief,
            )
        if lineage_ids and any(work.key == lineage_ids[-1] for work in workspace.works):
            workspace.active = lineage_ids[-1]

    def _recover_artifact_conversations(
        self, session: Session, workspace_id: str, base_id: str
    ) -> None:
        """Give a conversation a row when versions name it and it has none (the device it
        lived on is gone, or it never reached the backend), made from what the versions say:
        the title, the scope of the newest one, a turn for each request, a work for each
        lineage. A conversation that has a row, archived or not, is left as it is."""

        owned = self._owned_versions(session, workspace_id, base_id)
        if not owned:
            return
        taken = set(session.scalars(select(ArtifactConversation.id)))
        for conversation_id, versions in owned.items():
            if conversation_id in taken or len(conversation_id) > 80:
                continue
            try:
                scope = OutputScope.model_validate(json.loads(versions[-1].scope_json))
            except ValueError:
                scope = OutputScope()
            title = next(
                (v.conversation_title for v in reversed(versions) if v.conversation_title), None
            )
            row = ArtifactConversation(
                id=conversation_id,
                workspace_id=workspace_id,
                knowledge_base_id=base_id,
                title=(title or "Recovered conversation")[:160],
                scope_json=json.dumps(scope.model_dump()),
                created_at=versions[0].created_at,
                updated_at=versions[-1].created_at,
            )
            session.add(row)
            session.flush()
            for version in versions:
                if version.request:
                    self._add_artifact_turn(
                        session,
                        row.id,
                        instruction=version.request,
                        client_request_id=version.client_request_id or None,
                        asked_at=version.created_at,
                        answer="result",
                        artifact_id=version.id,
                    )
            workspace, brief = self._load_workspace(row)
            self._import_lineages(
                session, row, workspace, brief, self._lineages_by_last_use(versions)
            )
            self._store_workspace(row, workspace, brief, at=versions[-1].created_at)

    def artifact_conversations(
        self, base_id: str, workspace_id: str
    ) -> list[ArtifactConversationSummaryOut]:
        """The library's Artifacts conversations, the latest first. Listing first gives a row to
        any conversation that versions name and that has none."""

        with self._artifact_conversations_lock, session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            self._recover_artifact_conversations(session, workspace_id, base_id)
        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            rows = session.scalars(
                select(ArtifactConversation)
                .where(
                    ArtifactConversation.workspace_id == workspace_id,
                    ArtifactConversation.knowledge_base_id == base_id,
                    ArtifactConversation.archived.is_(False),
                    ArtifactConversation.trashed_at.is_(None),
                )
                .order_by(ArtifactConversation.updated_at.desc(), ArtifactConversation.id)
            ).all()
            return self._artifact_conversation_summaries(session, rows)

    def create_artifact_conversation(
        self, base_id: str, workspace_id: str, payload: CreateArtifactConversationInput
    ) -> ArtifactConversationOut:
        """Start a conversation, under the id the client gives it (a device's conversation
        keeps its own). Asking again for an id that exists returns that conversation."""

        with self._artifact_conversations_lock, session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            output_library(session, workspace_id, base_id)
            scope = payload.scope or OutputScope()
            self._resolve_output_scope(session, base_id, scope)
            conversation_id = payload.id or str(uuid4())
            existing = session.get(ArtifactConversation, conversation_id)
            if existing is not None:
                if existing.workspace_id != workspace_id or existing.knowledge_base_id != base_id:
                    raise ArtifactConflictError("That conversation id is used somewhere else.")
                if existing.trashed_at is not None:
                    raise LookupError(f"Conversation {conversation_id} was not found")
            else:
                now = utc_now()
                session.add(ArtifactConversation(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    knowledge_base_id=base_id,
                    title=((payload.title or "").strip() or "New conversation")[:160],
                    scope_json=json.dumps(scope.model_dump()),
                    created_at=now,
                    updated_at=now,
                ))
        return self.artifact_conversation(base_id, workspace_id, conversation_id)

    def import_artifact_conversations(
        self, base_id: str, workspace_id: str, payload: ImportArtifactConversationsInput
    ) -> list[ArtifactConversationSummaryOut]:
        """Take in conversations a device kept before the backend did, once. A conversation
        whose id has a row is skipped, so asking again changes nothing; the summaries are of
        those that were made.

        Each turn becomes a user message and the reply to it. The newest version of each
        lineage the turns saved (and of any other that names the conversation) becomes a work,
        and the lineage of the last saved turn is the active one.
        """

        with self._artifact_conversations_lock, session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            owned = self._owned_versions(session, workspace_id, base_id)
            made: list[ArtifactConversation] = []
            for imported in payload.conversations:
                if session.get(ArtifactConversation, imported.id) is not None:
                    continue
                made.append(
                    self._import_artifact_conversation(
                        session, workspace_id, base_id, imported, owned.get(imported.id, [])
                    )
                )
            return self._artifact_conversation_summaries(session, made)

    def _import_artifact_conversation(
        self,
        session: Session,
        workspace_id: str,
        base_id: str,
        imported: ImportedArtifactConversation,
        owned: list[_OwnedVersion],
    ) -> ArtifactConversation:
        started = _device_time(imported.created_at) or utc_now()
        try:
            scope = OutputScope.model_validate(imported.scope or {})
        except ValueError:
            scope = OutputScope()
        first = imported.turns[0].instruction if imported.turns else ""
        row = ArtifactConversation(
            id=imported.id,
            workspace_id=workspace_id,
            knowledge_base_id=base_id,
            title=((imported.title or "").strip() or first.strip() or "New conversation")[:160],
            scope_json=json.dumps(scope.model_dump()),
            created_at=started,
            updated_at=started,
        )
        session.add(row)
        session.flush()
        # The turns are in the order they happened; their messages keep that order even when a
        # device's times are missing or run backwards.
        last, lineages, used = started, [], set()
        for turn in imported.turns:
            asked = max(_device_time(turn.created_at) or started, last + timedelta(milliseconds=1))
            artifact = (
                session.scalar(
                    select(Artifact).where(
                        Artifact.id == turn.artifact_id,
                        Artifact.workspace_id == workspace_id,
                        Artifact.knowledge_base_id == base_id,
                        Artifact.trashed_at.is_(None),
                    )
                )
                if turn.status == "saved" and turn.artifact_id
                else None
            )
            if artifact is not None and artifact.lineage_id in lineages:
                lineages.remove(artifact.lineage_id)  # the lineage worked on last comes last
            if artifact is not None:
                lineages.append(artifact.lineage_id)
            request_id = turn.id if turn.id and turn.id not in used else None
            if request_id:
                used.add(request_id)
            last = self._add_artifact_turn(
                session,
                row.id,
                instruction=turn.instruction,
                client_request_id=request_id,
                asked_at=asked,
                answer={"saved": "result", "failed": "failed"}.get(turn.status, "stopped"),
                artifact_id=artifact.id if artifact is not None else None,
                error=turn.error if turn.status == "failed" else None,
            )
        # Versions that name the conversation but that no turn reached still belong to it; the
        # lineage of the last saved turn stays the one worked on last.
        unreached = [v for v in owned if v.lineage_id not in lineages]
        lineages = [*self._lineages_by_last_use(unreached), *lineages]
        workspace, brief = self._load_workspace(row)
        self._import_lineages(session, row, workspace, brief, lineages)
        self._store_workspace(row, workspace, brief, at=last)
        return row

    def artifact_conversation(
        self, base_id: str, workspace_id: str, conversation_id: str
    ) -> ArtifactConversationOut:
        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            return self._artifact_conversation_out(session, row)

    @contextmanager
    def _change_conversation(
        self, base_id: str, workspace_id: str, conversation_id: str,
    ) -> Iterator[tuple[
        Session, ArtifactConversation, workspaces.Workspace, workspaces.ConversationBrief,
        set[tuple[str, str]],
    ]]:
        with self.answer_runs.locked_active() as runs, session_scope(self.sessions) as session:
            live = self._live_attempts(runs, (workspace_id, base_id), conversation_id)
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._load_workspace(row)
            self._reconcile_plan_publications(session, row, workspace)
            self._settle_interrupted(workspace, live)
            yield session, row, workspace, brief, live

    @staticmethod
    def _reject_generation(works: Iterable[workspaces.Work]) -> None:
        for work in works:
            attempt = work.plan.attempt if work.plan is not None else None
            if (
                work.version_id is None and attempt is not None
                and attempt.state == "running" and attempt.phase == "generate"
            ):
                raise PlanConflictError("run_active", "Stop generation before editing this plan.")

    @staticmethod
    def _mark_plan_edit(
        workspace: workspaces.Workspace, work: workspaces.Work, brief: workspaces.ConversationBrief,
    ) -> None:
        plan = work.plan
        if plan is None:
            return
        plan.epoch += 1
        plan.updated_at = _timestamp(utc_now())
        plan.basis = "user_edited"
        if plan.attempt and plan.attempt.state != "running":
            plan.attempt = None
        if work.version_id is None:
            plan.approval = None
        pending = workspace.pending_by_target.get(plan.id)
        if pending is not None:
            pending.revision = plan_revision(work, brief, pending)

    def _work_plan_patch(
        self, session: Session, row: ArtifactConversation, workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief, work: workspaces.Work, patch: WorkPlanPatchInput,
    ) -> PlanPatchResult:
        if patch.base_edit_version != workspace.edit_version:
            if work.plan is not None:
                raise WorkPlanConflict(
                    "edit_conflict", "The plan changed. Review its current fields.", work,
                    revision=plan_revision(work, brief, self._work_pending(workspace, work)),
                    edit_version=workspace.edit_version,
                )
            raise PlanConflictError("edit_conflict", "The plan changed. Review its current fields.")
        scope = work.spec.scope or self._conversation_scope(row)
        if "scope" in patch.options.model_fields_set:
            try:
                scope_members(session, row.knowledge_base_id, patch.options.scope, strict=True)
            except ValueError as error:
                raise PlanValidationError([PlanIssue(("options", "scope"), str(error))]) from error
        target_scope = (row.workspace_id, row.knowledge_base_id, scope, workspace, work)
        material = local_material = None
        if work.plan and work.version_id is None:
            material = reviewed_studio_material(session, *target_scope)
            if work.spec.web and "web" in patch.options.model_fields_set and not patch.options.web:
                local_material = reviewed_studio_material(session, *target_scope, local_only=True)
        candidate = apply_work_plan_patch(
            brief, work, patch, edit_version=workspace.edit_version,
            material=material, local_material=local_material,
            pending=self._work_pending(workspace, work), availability=self._plan_availability(),
            updated_at=_timestamp(utc_now()),
        )
        if not candidate.changed:
            return candidate
        if candidate.brief != brief:
            self._reject_generation(workspace.works)
            for other in workspace.works:
                if other.key != work.key:
                    self._mark_plan_edit(workspace, other, candidate.brief)
        workspace.works[workspace.works.index(work)] = candidate.work
        if candidate.pending is not None:
            workspace.pending_by_target[workspaces.pending_target(work)] = candidate.pending
        return candidate

    def patch_artifact_work(
        self, base_id: str, workspace_id: str, conversation_id: str,
        work_key: str, patch: WorkPlanPatchInput,
    ) -> ArtifactConversationOut:
        with self._change_conversation(base_id, workspace_id, conversation_id) as (
            session, row, workspace, brief, live,
        ):
            hidden = self._hidden_works(workspace, self._live_lineages(session, row))
            work = next((item for item in workspace.works if item.key == work_key), None)
            if work is None or work.key in hidden:
                raise LookupError("This work was not found in this conversation.")
            candidate = self._work_plan_patch(session, row, workspace, brief, work, patch)
            if candidate.changed:
                workspace.edit_version = candidate.edit_version
                self._store_workspace(row, workspace, candidate.brief)
            return self._artifact_conversation_out(
                session, row, live_attempts=live, work_key=work_key,
            )

    def update_artifact_conversation(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: UpdateArtifactConversationInput,
    ) -> ArtifactConversationOut:
        """Rename a conversation, change what it reads from, or archive it."""

        with self._change_conversation(base_id, workspace_id, conversation_id) as (
            session, row, workspace, brief, live,
        ):
            changed = False
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise ValueError("title must not be blank")
                changed = title != row.title
                row.title = title[:160]
            if payload.target_lineage_id and payload.scope is None:
                raise ValueError("Choose a scope for this output.")
            if payload.scope is not None:
                self._resolve_output_scope(session, base_id, payload.scope)
                if payload.target_lineage_id is None:
                    before = self._conversation_scope(row)
                    scope_changed = (
                        studio_manifest(workspace_id, base_id, payload.scope, [])["scope"]
                        != studio_manifest(workspace_id, base_id, before, [])["scope"]
                    )
                    if scope_changed:
                        patch = WorkPlanPatchInput(
                            base_edit_version=workspace.edit_version,
                            options={"scope": payload.scope.model_dump()},
                        )
                        for work in list(workspace.works):
                            if work.version_id is None and (
                                work.key == workspace.active or work.spec.scope in (None, before)
                            ):
                                self._work_plan_patch(session, row, workspace, brief, work, patch)
                        row.scope_json = json.dumps(payload.scope.model_dump())
                    changed |= scope_changed
                else:
                    lineage_id = payload.target_lineage_id
                    head = lineage_head(session, workspace_id, base_id, lineage_id)
                    if head is None:
                        raise LookupError("This artifact was not found in this conversation.")
                    shown = artifact_detail(session, head)
                    if shown.conversation_id not in (None, row.id):
                        raise LookupError("This artifact was not found in this conversation.")
                    self._require_agent_output(head)
                    changed |= workspace.refinement_scopes.get(lineage_id) != payload.scope
                    workspace.refinement_scopes[lineage_id] = payload.scope
            if payload.archived is not None:
                changed |= payload.archived != row.archived
                row.archived = payload.archived
            if changed:
                workspace.edit_version += 1
                self._store_workspace(row, workspace, brief)
            return self._artifact_conversation_out(
                session, row, live_attempts=live, work_key=payload.target_lineage_id,
            )

    def edit_artifact_brief(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: EditArtifactBriefInput,
    ) -> ArtifactBriefStateOut:
        """Set the lines the user typed in the Brief panel. They are marked as theirs, so no
        later guess replaces them. Use, length and output type belong to the artifact being
        worked on."""

        with self._change_conversation(base_id, workspace_id, conversation_id) as (
            session, row, workspace, brief, _,
        ):
            work = workspaces.active_work(workspace)
            if work and work.version_id and work.key not in self._live_lineages(session, row):
                work = None
            spec = payload.spec
            fields = payload.model_fields_set
            try:
                revised, edited = workspaces.edit_brief(
                    brief, work, goal=payload.goal, audience=payload.audience,
                    constraints=payload.constraints, open=payload.open,
                    emphasize=payload.emphasize if "emphasize" in fields else None,
                    avoid=payload.avoid if "avoid" in fields else None,
                    use=spec.use if spec else None,
                    length=spec.length if spec else None,
                    output_type=spec.output_type if spec else None,
                )
            except ValidationError as error:
                raise PlanValidationError([
                    PlanIssue(tuple(item["loc"]), item["msg"]) for item in error.errors()
                ]) from error
            if work is not None and work.plan is not None:
                assert edited is not None
                if edited.chosen_output_type != work.spec.chosen_output_type:
                    raise PlanValidationError([
                        PlanIssue(("spec", "outputType"), "Start a separate plan for another type.")
                    ])
                if work.version_id is None:
                    if payload.goal is not None and revised.goal != brief.goal:
                        edited.focus = revised.goal.text if revised.goal else ""
                    if payload.audience is not None and revised.audience != brief.audience:
                        edited.audience_choice = None
            affected = [work] if work is not None and work.spec != edited else []
            if revised != brief or affected:
                if revised != brief:
                    affected = workspace.works
                self._reject_generation(affected)
                if work is not None and edited is not None:
                    work.spec = edited
                for item in affected:
                    self._mark_plan_edit(workspace, item, revised)
                workspace.edit_version += 1
                self._store_workspace(row, workspace, revised)
            return ArtifactBriefStateOut(
                brief=self._artifact_brief_out(revised),
                spec=self._artifact_spec_out(work.spec) if work is not None else None,
            )
