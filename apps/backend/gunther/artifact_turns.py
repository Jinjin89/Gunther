"""Run and commit an artifact conversation turn and replay completed requests."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther import artifact_agent, usage
from gunther import artifact_workspace as workspaces
from gunther.answer_runs import Stopped
from gunther.artifact_read import artifact_detail, latest_check_records
from gunther.artifact_turn_result import TurnResult, audience_of
from gunther.artifact_versions import (
    ArtifactConflictError,
    commit_version,
    earlier_output,
    output_library,
    output_version,
)
from gunther.database import session_scope
from gunther.evidence import Evidence
from gunther.llm import ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.models import Artifact, ArtifactConversation, ArtifactMessage, utc_now
from gunther.outputs import BuiltOutput, Notes, OutputLimits, Planned, Pool, Spec
from gunther.run_budget import BudgetedGateway, RunBudget
from gunther.schemas import (
    ArtifactChangeOut,
    ArtifactMessageOut,
    ArtifactOut,
    ArtifactQuestionOut,
    ArtifactTurnInput,
    ArtifactTurnOut,
    ArtifactUnitSnapshotOut,
    OutputModelOut,
    OutputScope,
)
from gunther.service_records import _id

NEW_CONVERSATION = "New conversation"


class ArtifactTurns:
    # A turn of the Artifacts agent (see artifact_agent) -----------------------------------------

    def artifact_conversation_title(
        self, base_id: str, workspace_id: str, conversation_id: str, text: str = ""
    ) -> str:
        """What a conversation is called, for the top bar; a conversation not yet named takes
        its first message's words."""

        with session_scope(self.sessions) as session:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            if row.title == NEW_CONVERSATION and text.strip():
                return " ".join(text.split())[:60]
            return row.title

    def _turn_fingerprint(
        self, workspace_id: str, base_id: str, conversation_id: str, payload: ArtifactTurnInput,
    ) -> str:
        return self._output_fingerprint(
            workspace_id, base_id, "artifact-turn",
            {"conversationId": conversation_id, "kind": payload.kind, "text": payload.text,
             **payload.rebuild_fields()},
        )

    def _earlier_turn(
        self, session: Session, row: ArtifactConversation, payload: ArtifactTurnInput
    ) -> ArtifactTurnOut | None:
        """The result of an earlier try of this same message, if the turn was finished."""

        user = session.scalar(
            select(ArtifactMessage).where(
                ArtifactMessage.conversation_id == row.id,
                ArtifactMessage.client_request_id == payload.client_request_id,
            )
        )
        if user is None:
            return None
        linked: tuple[ArtifactMessage, dict] | None = None
        for reply in session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == row.id, ArtifactMessage.role == "assistant")
            .order_by(ArtifactMessage.created_at)
        ):
            found = json.loads(reply.payload_json or "{}")
            if found.get("to") == user.id:
                linked = reply, found
                break
        if linked is None:
            return None
        stored = json.loads(user.payload_json or "{}")
        same = (
            user.text == payload.text and user.kind == payload.kind and all(
                stored.get(key) == value for key, value in payload.rebuild_fields().items()
            )
        )
        if not same:
            raise ArtifactConflictError("clientRequestId was already used for a different message")
        return self._turn_out(session, *linked)

    def _turn_out(
        self, session: Session, reply: ArtifactMessage, found: dict | None = None
    ) -> ArtifactTurnOut:
        found = found if found is not None else json.loads(reply.payload_json or "{}")
        outcome = {
            "question": "asked",
            "answer": "answered",
            "result": "saved",
            "reply": "replied",
            "paused": "paused",
        }[reply.kind]
        artifact = (
            artifact_detail(session, session.get(Artifact, reply.artifact_id))
            if reply.kind == "result" and reply.artifact_id
            else None
        )
        asked = found.get("question")
        return ArtifactTurnOut(
            outcome=outcome,  # type: ignore[arg-type]
            reply=reply.text,
            question=ArtifactQuestionOut(**asked) if asked else None,
            artifact=artifact,
            changes=[ArtifactChangeOut(**item) for item in found.get("changes", [])],
            message=self._artifact_message_out(reply),
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

    def artifact_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactTurnOut:
        """One message of the user to the Artifacts agent: it decides what the message needs
        and does that, in small steps, until it asks, answers, saves a version or replies.

        The workspace of the conversation is brought in line with each artifact's newest
        version first (a hand edit may have changed it). Searches use the conversation's scope.
        Whatever ends the turn, the workspace keeps the progress made, except that a version is
        saved only by a turn that ends with one. Sending the same ``clientRequestId`` again
        returns the result of the first try. ``events`` hears the progress and may raise to stop.
        """

        emit = events or (lambda _event: None)
        limits = OutputLimits()
        agent_limits = self.artifact_limits
        with usage.job("outputs", ref=conversation_id), session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            earlier = self._earlier_turn(session, row, payload)
            if earlier is not None:
                return earlier
        published = earlier_output(
            self.sessions, workspace_id, payload.client_request_id,
            self._turn_fingerprint(workspace_id, base_id, conversation_id, payload),
        )
        if published is not None:
            if (
                published.knowledge_base_id != base_id
                or published.conversation_id != conversation_id
            ):
                raise ArtifactConflictError(
                    "clientRequestId was already used for a different message"
                )
            return self._published_turn_out(published)
        model, effort = self._output_model(payload.model, payload.effort)
        user_id, reply_id = _id("acm"), _id("acm")
        emit({"type": "turn", "conversationId": conversation_id, "messageId": user_id})
        failure: BaseException | None = None
        result: TurnResult | None = None
        preserve_on_failure = payload.rebuild is not None
        with usage.job("outputs", ref=conversation_id), session_scope(self.sessions) as session:
            library = output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._load_workspace(row)
            began = (
                brief.model_copy(deep=True),
                {work.key: work.spec.model_copy(deep=True) for work in workspace.works},
            )
            scope = self._conversation_scope(row)
            material = self._output_material(session, base_id, scope, strict=False)
            tool = self._output_search_tool(
                session, list(material.source_revisions), f"{library.title}: {library.question}"
            )
            web, web_reason = self._web_tool(payload.web)
            pinned_keys = {item.key for item in material.seeds}
            reader = self._source_reader(
                session, material.source_revisions, pinned_keys=pinned_keys,
            )
            versions: dict[str, int] = {}
            saved: dict[str, str] = {}
            heads: dict[str, str] = {}
            for work in workspace.works:
                if work.key.startswith("draft_"):
                    continue
                head = session.scalar(
                    select(Artifact).where(
                        Artifact.lineage_id == work.key,
                        Artifact.workspace_id == workspace_id,
                        Artifact.knowledge_base_id == base_id,
                    ).order_by(Artifact.version_number.desc()).limit(1)
                )
                if head is None:
                    continue
                checks = latest_check_records(session, head)
                shown = artifact_detail(session, head, check_records=checks)
                workspaces.sync(work, shown, checks, workspace, brief)
                versions[work.key], saved[work.key], heads[work.key] = (
                    shown.version_number, shown.content, head.id,
                )
            history = self._turn_history(session, row.id, agent_limits.history)
            budget = RunBudget(
                agent_limits.searches,
                agent_limits.searches,
                agent_limits.calls,
                agent_limits.calls,
                phase="turn",
            )
            gateway = BudgetedGateway(self.models, budget)

            from gunther.studio.pipeline import StudioAgents
            from gunther.studio.work import StudioWorkAgents

            def make_studio_agents(kind: str) -> StudioWorkAgents:
                return StudioWorkAgents(
                    gateway, kind, limits, tools=[tool, *([web] if web else [])],
                    asset_reader=self.asset_reader,
                    reusable_figures=self._reusable_figure_readings(session, base_id),
                )

            def evidence_allowed(item: Evidence) -> bool:
                return self._output_evidence_in_scope(
                    item, material.source_revisions, pinned_keys, web=web is not None,
                )

            def studio_reading(made: Pool) -> Notes:
                reading_gaps = self._studio_reading(
                    session, material.source_revisions, made, pinned=material.seeds
                )
                return replace(material.notes, extraction_gaps=tuple(reading_gaps))

            def studio(
                spec: Spec, made: Pool, use: str | None, resume=None, checkpoint=None,
                *, purpose=None, fixed_outline=None,
            ) -> BuiltOutput:
                scoped_notes = studio_reading(made)
                agents = StudioAgents(
                    gateway,
                    limits,
                    use=use,
                    renderer=self.slide_renderer,
                    asset_reader=self.asset_reader,
                    reusable_figures=self._reusable_figure_readings(session, base_id),
                )
                if payload.rebuild and payload.web:
                    warning = self._studio_web(
                        web, web_reason, spec.brief, made, limits.per_section,
                    )
                    if warning:
                        agents.skipped.append(warning)
                return agents.make(  # type: ignore[arg-type]
                    model,
                    effort,
                    spec,
                    made,
                    scoped_notes,
                    emit,
                    fallback_title=library.title,
                    purpose=purpose,
                    fixed_outline=fixed_outline,
                    scope={"selection": scope.model_dump(), "inputs": material.inputs},
                    web=payload.web,
                    resume=resume,
                    checkpoint=checkpoint,
                )

            agent = artifact_agent.ArtifactAgent(
                artifact_agent.TurnContext(
                    studio=studio,
                    make_studio_agents=make_studio_agents,
                    studio_reading=studio_reading,
                    evidence_allowed=evidence_allowed,
                    workspace=workspace,
                    brief=brief,
                    gateway=gateway,  # type: ignore[arg-type]
                    budget=budget,
                    model=model,
                    effort=effort,
                    notes=material.notes,
                    library_tool=tool,
                    web_tool=web,
                    web_reason=web_reason or "",
                    reader=reader,
                    library=library.title,
                    kind=payload.kind,
                    text=payload.text,
                    focus=payload.focus.model_dump() if payload.focus else None,
                    history=history,
                    reply_id=reply_id,
                    versions=versions,
                    saved=saved,
                    seeds=material.seeds[: limits.seed],
                    emit=emit,
                    limits=agent_limits,
                )
            )
            try:
                if payload.rebuild is None:
                    result = agent.run()
                else:
                    action = payload.rebuild
                    parent = output_version(
                        session, workspace_id, base_id, action.artifact_id, latest=True,
                    )
                    owner = json.loads(parent.provenance_json).get("conversation_id")
                    work = next((w for w in workspace.works if w.key == parent.lineage_id), None)
                    if work is None or (owner and owner != conversation_id):
                        raise LookupError("This artifact was not found in this conversation.")
                    result = agent.rebuild(
                        work, [Planned(i.heading, i.goal, id=i.id) for i in action.outline],
                    )
            except Exception as error:
                failure = error
            brief = agent.brief

        def keep(
            session: Session | None, artifact: ArtifactOut | None, state: workspaces.Workspace,
        ) -> ArtifactMessageOut:
            assert result is not None
            kind = {
                "asked": "question", "answered": "answer", "saved": "result",
                "replied": "reply", "paused": "paused",
            }[result.outcome]
            extra: dict[str, object] = {"steps": result.steps}
            if result.question:
                extra.update(question=result.question, options=result.question["options"])
            if result.changes:
                extra["changes"] = result.changes
            return self._store_turn(
                base_id, workspace_id, conversation_id, payload, user_id, reply_id, state, brief,
                began, kind=kind, text=result.reply, extra=extra,
                artifact_id=artifact.id if artifact else None, request_id=payload.client_request_id,
                existing_session=session,
            )  # fmt: skip

        artifact = message = None
        if result is not None and result.save is not None:
            try:
                artifact, message = self._save_turn_version(
                    base_id, workspace_id, conversation_id, payload, model, effort, result,
                    workspace, brief, scope, material.inputs, material.snapshots, heads, emit, keep,
                )
            except Exception as error:
                preserve_on_failure = True
                result, failure = None, error
        if failure is not None:
            stopped = isinstance(failure, Stopped)
            error = (
                ""
                if stopped
                else (
                    str(failure)
                    if isinstance(failure, (ModelError, ValueError, LookupError))
                    else "The message could not be handled."
                )
            )
            if not stopped and not isinstance(failure, (ModelError, ValueError, LookupError)):
                from gunther.service import logger

                logger.error("An Artifacts turn failed", exc_info=failure)
            self._store_turn(
                base_id, workspace_id, conversation_id, payload, user_id, reply_id,
                None if preserve_on_failure else workspace,
                brief, began, kind="stopped" if stopped else "failed", text=error,
                extra={"error": error} if error else {}, artifact_id=None,
                request_id=None,
            )  # fmt: skip
            raise failure
        assert result is not None
        if artifact and (message is None or message.artifact_id != artifact.id):
            with session_scope(self.sessions) as session:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id,
                )
                earlier = self._earlier_turn(session, row, payload)
                return earlier if earlier is not None else self._published_turn_out(artifact)
        return ArtifactTurnOut(
            outcome=result.outcome,
            reply=result.reply,
            question=ArtifactQuestionOut(**result.question) if result.question else None,
            artifact=artifact,
            changes=[ArtifactChangeOut(**item) for item in result.changes],
            message=message or keep(None, artifact, workspace),
        )

    def _turn_history(
        self, session: Session, conversation_id: str, count: int
    ) -> list[tuple[str, str]]:
        """The last exchanges of a conversation, word for word: what the user said and what
        the agent replied (a question, an answer, a summary of a change)."""

        messages = session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == conversation_id)
            .order_by(ArtifactMessage.created_at, ArtifactMessage.role.desc())
        ).all()
        pairs: list[tuple[str, str]] = []
        asked: str | None = None
        for message in messages:
            if message.role == "user":
                asked = message.text
            elif asked is not None and message.text.strip():
                pairs.append((asked, message.text))
                asked = None
        return pairs[-count:]

    def _save_turn_version(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        model: ModelInfo,
        effort: Effort,
        result: TurnResult,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        scope: OutputScope,
        inputs: dict[str, list[dict[str, object]]],
        snapshots: list[ArtifactUnitSnapshotOut],
        heads: dict[str, str],
        emit: Callable[[dict], None],
        keep: Callable[[Session, ArtifactOut, workspaces.Workspace], ArtifactMessageOut],
    ) -> tuple[ArtifactOut, ArtifactMessageOut | None]:
        """Seal the artifact a turn finished as a version, and make its work match it."""

        request = result.save
        assert request is not None
        work, built = request.work, request.built
        kind = work.spec.kind
        title = self.artifact_conversation_title(base_id, workspace_id, conversation_id)
        record = work.model_copy(deep=True)
        record.version_id, record.version_map, record.skipped = None, request.version_map, []
        parent = heads.get(work.key)
        message = None

        def publish(session: Session, artifact: ArtifactOut) -> None:
            nonlocal message
            saved = record.model_copy(update={
                "key": artifact.lineage_id, "version_id": artifact.id, "title": artifact.title,
            })
            name = "deck" if kind == "slides" else "report"
            selects = payload.rebuild is not None or workspace.active == work.key
            state = workspace.model_copy(deep=True)
            state.works = [saved if item.key == work.key else item for item in state.works]
            if selects:
                state.active = saved.key
            state.log.append(f"v{artifact.version_number} ({name}): {request.log}")
            message = keep(session, artifact, state)

        emit({"type": "saving"})  # a reader who pressed Stop by now gets nothing saved
        artifact = commit_version(
            self.sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=self._turn_fingerprint(workspace_id, base_id, conversation_id, payload),
            parent_id=parent,
            kind=kind,
            style=(workspaces._style_of(work) or "auto") if kind == "report" else None,
            audience=audience_of(brief),
            title=built.title,
            brief=workspaces.render_brief(brief, work),
            origin="rebuild" if payload.rebuild else "revise" if parent else "build",
            content=built.content,
            scope=scope.model_dump(),
            inputs=inputs,
            outline=built.outline,
            citations=[item.model_dump() for item in built.citations],
            snapshots=snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by={
                "generator": "gunther.artifact-agent.v1",
                "skill": built.skill,
                "approach": built.approach,
                "use": (
                    work.spec.use.text
                    if work.spec.use and work.spec.use.text in ("talk", "read")
                    else None
                ),
                "conversation_id": conversation_id,
                "conversation_title": title,
                "request": payload.text,
                "reply": request.summary,
                "changes": request.changes,
                "work": record.model_dump(mode="json"),
            },
            publish=publish,
        )
        return artifact, message

    def _store_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        user_id: str,
        reply_id: str,
        workspace: workspaces.Workspace | None,
        brief: workspaces.ConversationBrief,
        began: tuple[workspaces.ConversationBrief, dict[str, workspaces.WorkSpec]],
        *,
        kind: str,
        text: str,
        extra: dict[str, object],
        artifact_id: str | None,
        request_id: str | None,
        existing_session: Session | None = None,
    ) -> ArtifactMessageOut:
        """Keep the turn: both messages, the workspace and the brief. What the user typed in the
        panel while it ran is kept over the turn's version of it. A failed publication records
        its messages without writing any staged workspace or brief."""

        transaction = (
            nullcontext(existing_session)
            if existing_session is not None else session_scope(self.sessions)
        )
        with transaction as session:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            asked = utc_now()
            if workspace is not None:
                stored, now = self._load_workspace(row)
                brief = workspaces.keep_edits(began[0], brief, now)
                theirs = {work.key: work.spec for work in stored.works}
                for work in workspace.works:
                    before = began[1].get(work.key)
                    if before is not None and work.key in theirs:
                        work.spec = workspaces.keep_spec_edits(before, work.spec, theirs[work.key])
                if row.title == NEW_CONVERSATION:
                    row.title = " ".join(payload.text.split())[:160] or NEW_CONVERSATION
                self._store_workspace(row, workspace, brief, at=asked + timedelta(milliseconds=1))
            focus = payload.focus.model_dump() if payload.focus else None
            session.add(
                ArtifactMessage(
                    id=user_id,
                    conversation_id=row.id,
                    role="user",
                    kind=payload.kind,
                    text=payload.text,
                    payload_json=json.dumps(
                        {**payload.rebuild_fields(), **({"focus": focus} if focus else {})},
                        ensure_ascii=False,
                    ),
                    client_request_id=request_id,
                    created_at=asked,
                )
            )
            reply = ArtifactMessage(
                id=reply_id,
                conversation_id=row.id,
                role="assistant",
                kind=kind,
                text=text,
                payload_json=json.dumps({"to": user_id, **extra}, ensure_ascii=False),
                artifact_id=artifact_id,
                created_at=asked + timedelta(milliseconds=1),
            )
            session.add(reply)
            session.flush()
            return self._artifact_message_out(reply)
