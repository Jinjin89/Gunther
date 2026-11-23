"""Open, project, import and recover artifact conversations and their workspaces."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from gunther import artifact_workspace as workspaces
from gunther.artifact_read import artifact_detail, latest_check_records
from gunther.artifact_versions import ArtifactConflictError, output_library
from gunther.database import session_scope
from gunther.models import Artifact, ArtifactConversation, ArtifactMessage, utc_now
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
    UpdateArtifactConversationInput,
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
        cls, work: workspaces.Work, brief: workspaces.ConversationBrief
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
        )

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

    @staticmethod
    def _artifact_conversation_summary(
        row: ArtifactConversation, workspace: workspaces.Workspace | None = None,
        last_turn: ArtifactLastTurnOut | None = None,
    ) -> ArtifactConversationSummaryOut:
        workspace = workspace or workspaces.load_workspace(row.state_json)
        active = workspaces.active_work(workspace)
        output_type = (
            output_type_for(active.spec.kind, active.spec.chosen_output_type) if active else None
        )
        return ArtifactConversationSummaryOut(
            id=row.id,
            title=row.title,
            updated_at=_timestamp(row.updated_at),
            artifact_count=len(workspace.works),
            waiting=workspace.pending is not None,
            output_type=output_type.label if output_type else None,
            shape=output_type.shape if output_type else None,
            lineage_id=active.key if active and active.version_id else None,
            last_turn=last_turn,
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
        ).subquery()
        message = aliased(ArtifactMessage, ranked)
        recent = defaultdict(list)
        for item in session.scalars(select(message).where(ranked.c.position == 1)):
            recent[item.conversation_id].append(item)
        return [
            self._artifact_conversation_summary(
                row, last_turn=self._last_artifact_turn(recent[row.id]),
            )
            for row in rows
        ]

    def _artifact_conversation_out(
        self, session: Session, row: ArtifactConversation
    ) -> ArtifactConversationOut:
        workspace, brief = self._load_workspace(row)
        messages = session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == row.id)
            # A question and its answer can share a second: the user's message comes first.
            .order_by(ArtifactMessage.created_at, ArtifactMessage.role.desc())
        ).all()
        pending = workspace.pending
        return ArtifactConversationOut(
            **self._artifact_conversation_summary(
                row, workspace, self._last_artifact_turn(reversed(messages)),
            ).model_dump(),
            scope=self._conversation_scope(row),
            brief=self._artifact_brief_out(brief),
            messages=[self._artifact_message_out(message) for message in messages],
            works=[self._artifact_work_out(work, brief) for work in workspace.works],
            active=workspace.active,
            pending=(
                ArtifactPendingOut(
                    question=pending.question,
                    options=pending.options,
                    message_id=pending.message_id,
                )
                if pending
                else None
            ),
            archived=row.archived,
            created_at=_timestamp(row.created_at),
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
            .where(Artifact.workspace_id == workspace_id, Artifact.knowledge_base_id == base_id)
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
            head = session.scalar(
                select(Artifact)
                .where(
                    Artifact.lineage_id == lineage_id,
                    Artifact.workspace_id == row.workspace_id,
                    Artifact.knowledge_base_id == row.knowledge_base_id,
                )
                .order_by(Artifact.version_number.desc())
                .limit(1)
            )
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
            rows = session.scalars(
                select(ArtifactConversation)
                .where(
                    ArtifactConversation.workspace_id == workspace_id,
                    ArtifactConversation.knowledge_base_id == base_id,
                    ArtifactConversation.archived.is_(False),
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
            output_library(session, workspace_id, base_id)
            scope = payload.scope or OutputScope()
            self._resolve_output_scope(session, base_id, scope)
            conversation_id = payload.id or str(uuid4())
            existing = session.get(ArtifactConversation, conversation_id)
            if existing is not None:
                if existing.workspace_id != workspace_id or existing.knowledge_base_id != base_id:
                    raise ArtifactConflictError("That conversation id is used somewhere else.")
                return self._artifact_conversation_out(session, existing)
            now = utc_now()
            row = ArtifactConversation(
                id=conversation_id,
                workspace_id=workspace_id,
                knowledge_base_id=base_id,
                title=((payload.title or "").strip() or "New conversation")[:160],
                scope_json=json.dumps(scope.model_dump()),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return self._artifact_conversation_out(session, row)

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

    def update_artifact_conversation(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: UpdateArtifactConversationInput,
    ) -> ArtifactConversationOut:
        """Rename a conversation, change what it reads from, or archive it."""

        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise ValueError("title must not be blank")
                row.title = title[:160]
            if payload.scope is not None:
                self._resolve_output_scope(session, base_id, payload.scope)
                row.scope_json = json.dumps(payload.scope.model_dump())
            if payload.archived is not None:
                row.archived = payload.archived
            row.updated_at = utc_now()
            session.flush()
            return self._artifact_conversation_out(session, row)

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

        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._load_workspace(row)
            work = workspaces.active_work(workspace)
            spec = payload.spec
            brief, edited = workspaces.edit_brief(
                brief,
                work,
                goal=payload.goal,
                audience=payload.audience,
                constraints=payload.constraints,
                open=payload.open,
                use=spec.use if spec else None,
                length=spec.length if spec else None,
                output_type=spec.output_type if spec else None,
            )
            if work is not None and edited is not None:
                work.spec = edited
            self._store_workspace(row, workspace, brief)
            session.flush()
            return ArtifactBriefStateOut(
                brief=self._artifact_brief_out(brief),
                spec=self._artifact_spec_out(work.spec) if work is not None else None,
            )
