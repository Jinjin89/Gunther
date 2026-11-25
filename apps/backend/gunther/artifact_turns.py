"""Run and commit an artifact conversation turn and replay completed requests."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther import artifact_agent, key_results, usage
from gunther import artifact_workspace as workspaces
from gunther.answer_runs import Stopped
from gunther.artifact_read import artifact_detail, latest_check_records, live_artifact_message
from gunther.artifact_turn_result import TurnResult, audience_of
from gunther.artifact_versions import (
    ArtifactConflictError,
    commit_version,
    earlier_output,
    lineage_head,
    output_library,
    output_version,
)
from gunther.database import session_scope
from gunther.evidence import Evidence, KeyResultRefs, key_result_lines, key_result_refs
from gunther.library_lookup import SearchHit, pack_key_results, pack_searched
from gunther.llm import ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.models import ArtifactConversation, ArtifactMessage, utc_now
from gunther.output_types import OUTPUT_TYPES, SHAPES, output_type_for
from gunther.outputs import BuiltOutput, Notes, OutputLimits, Planned, Pool, Spec
from gunther.run_budget import BudgetedGateway, RunBudget
from gunther.schemas import (
    ARTIFACT_TURN_FIELDS,
    ArtifactChangeOut,
    ArtifactMessageOut,
    ArtifactOut,
    ArtifactQuestionOut,
    ArtifactTurnInput,
    ArtifactTurnOut,
    ArtifactUnitSnapshotOut,
    BuildTurnInput,
    CreateArtifactConversationInput,
    OutputModelOut,
    OutputScope,
    SearchedHitOut,
    UsedKeyResultOut,
)
from gunther.service_records import _id

NEW_CONVERSATION = "New conversation"


def _build_work(
    build: BuildTurnInput, workspace: workspaces.Workspace, brief: workspaces.ConversationBrief,
    target: str | None, key: str,
) -> tuple[workspaces.Work, workspaces.Work, Spec]:
    kind = OUTPUT_TYPES[build.type].shape
    options = workspaces.WorkSpec(
        kind=kind,
        output_type=workspaces.Line(text=build.type, mark="you"),
        use=workspaces.Line(text=build.use, mark="you") if build.use else None,
        length=workspaces.Line(text=build.length, mark="you") if build.length else None,
        language=build.language,
        theme=build.theme,
        design_version=build.design_version,
    )
    draft = next((item for item in workspace.works if item.build_key == key), None)
    if draft is None:
        draft = workspaces.Work(key=_id("draft"), spec=options, build_key=key)
        if target is None:
            workspace.works.append(draft)
    else:
        draft.spec = options
        if target is not None:
            workspace.works.remove(draft)
    if target is None:
        workspace.active = draft.key
    work = workspaces.active_work(workspace)
    assert work is not None
    brief.goal = workspaces.Line(text=build.focus[:300], mark="you") if build.focus else None
    brief.audience = workspaces.Line(text=build.audience, mark="you")
    spec = Spec(
        kind, build.audience, build.type, build.focus, build.title, build.theme,
        build.design_version, length=build.length, language=build.language,
    )
    return work, draft, spec


class ArtifactTurns:
    # A turn of the Artifacts agent (see artifact_agent) -----------------------------------------

    def artifact_conversation_title(
        self, base_id: str, workspace_id: str, conversation_id: str, text: str = ""
    ) -> str:
        """What a conversation is called, for the top bar; a conversation not yet named takes
        its first message's words."""

        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
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
             **payload.request_fields()},
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
            user.text == payload.text and user.kind == payload.kind
            and {key: stored[key] for key in ARTIFACT_TURN_FIELDS & stored.keys()}
            == payload.request_fields()
        )
        if not same:
            raise ArtifactConflictError("clientRequestId was already used for a different message")
        return self._turn_out(session, row, *linked)

    def _turn_out(
        self, session: Session, row: ArtifactConversation, reply: ArtifactMessage, found: dict
    ) -> ArtifactTurnOut:
        outcome = {
            "question": "asked",
            "answer": "answered",
            "result": "saved",
            "reply": "replied",
            "paused": "paused",
        }[reply.kind]
        artifact = (
            artifact_detail(session, output_version(
                session, row.workspace_id, row.knowledge_base_id, reply.artifact_id, latest=False,
            ))
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
        *,
        legacy_fingerprint: str | None = None,
        legacy_title: str | None = None,
        expected_parent: str | None = None,
    ) -> ArtifactTurnOut:
        """One message of the user to the Artifacts agent: it decides what the message needs
        and does that, in small steps, until it asks, answers, saves a version or replies.

        The workspace of the conversation is brought in line with each artifact's newest
        version first (a hand edit may have changed it). A targeted turn reads from the output's
        scope, including any explicit update for that lineage.
        Whatever ends the turn, the workspace keeps the progress made, except that a version is
        saved only by a turn that ends with one. Sending the same ``clientRequestId`` again
        returns the result of the first try. ``events`` hears the progress and may raise to stop.
        """

        emit = events or (lambda _event: None)
        limits = OutputLimits()
        agent_limits = self.artifact_limits
        build = payload.build
        build_scope = build.scope.model_copy(deep=True) if build is not None else None
        web_enabled = build.web if build is not None else payload.web
        turn_key = self._turn_fingerprint(workspace_id, base_id, conversation_id, payload)
        fingerprint = legacy_fingerprint or turn_key
        needs_conversation = False
        with self.sessions() as session:
            output_library(session, workspace_id, base_id)
            try:
                row = self._artifact_conversation_row(
                    session, workspace_id, base_id, conversation_id,
                )
            except LookupError:
                if legacy_fingerprint is None:
                    raise
                if session.get(ArtifactConversation, conversation_id) is not None:
                    raise ArtifactConflictError(
                        "That conversation id is used somewhere else."
                    ) from None
                needs_conversation = True
            else:
                earlier = self._earlier_turn(session, row, payload)
                if earlier is not None:
                    return earlier
        published = earlier_output(
            self.sessions, workspace_id, payload.client_request_id,
            fingerprint,
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
        material = None
        searched: list[SearchHit] = []
        key_results_read: KeyResultRefs = {}
        if needs_conversation:
            assert build_scope is not None
            with session_scope(self.sessions) as session:
                material = self._output_material(session, base_id, build_scope)
            # The compatibility route has already validated the older conversation fields.
            self.create_artifact_conversation(
                base_id, workspace_id,
                CreateArtifactConversationInput.model_construct(
                    id=conversation_id, title=legacy_title,
                ),
            )
        user_id, reply_id = _id("acm"), _id("acm")
        emit({"type": "turn", "conversationId": conversation_id, "messageId": user_id})
        failure: BaseException | None = None
        result: TurnResult | None = None
        draft: workspaces.Work | None = None
        preserve_on_failure = build is not None or payload.rebuild is not None
        with self.sessions() as session:
            library = output_library(session, workspace_id, base_id)
            library_title = library.title
            library_question = library.question
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._visible_workspace(session, row)
            began = (
                brief.model_copy(deep=True),
                {work.key: work.spec.model_copy(deep=True) for work in workspace.works},
            )
            versions: dict[str, int] = {}
            saved: dict[str, str] = {}
            heads: dict[str, str] = {}
            target = payload.target_lineage_id
            if payload.rebuild is not None:
                parent = output_version(
                    session, workspace_id, base_id, payload.rebuild.artifact_id, latest=True,
                )
                if target is not None and target != parent.lineage_id:
                    raise LookupError("This artifact was not found in the targeted output.")
                target, expected_parent = parent.lineage_id, parent.id
            target_scope = None
            reuse_plan = False
            works: dict[str, workspaces.Work | None] = {
                w.key: w for w in workspace.works if not w.key.startswith("draft_")
            }
            if target is not None:
                works.setdefault(target, None)
            for lineage, work in works.items():
                head = lineage_head(session, workspace_id, base_id, lineage)
                if lineage == target:
                    prior = json.loads(head.provenance_json) if head else {}
                    owner = prior.get("conversation_id") or conversation_id
                    if head is None or owner != conversation_id:
                        raise LookupError("This artifact was not found in this conversation.")
                    if expected_parent is not None and expected_parent != head.id:
                        raise ArtifactConflictError(
                            "That is not the latest version of this output. Open the latest one."
                        )
                    if build is None:
                        self._require_agent_output(head)
                    else:
                        reuse_plan = (
                            output_type_for(head.kind, head.style).id == build.type
                            and head.brief == build.focus
                            and prior.get("length") == build.length
                            and prior.get("language") == build.language
                            and prior.get("use") == build.use
                            and bool(json.loads(head.outline_json))
                        )
                    workspace.active = lineage
                if head is None:
                    continue
                checks = latest_check_records(session, head)
                shown = artifact_detail(session, head, check_records=checks)
                if lineage == target:
                    target_scope = workspace.refinement_scopes.get(lineage) or shown.scope
                if work is None:
                    workspaces.import_work(shown, checks, workspace, brief)
                else:
                    workspaces.sync(work, shown, checks, workspace, brief)
                versions[lineage], saved[lineage], heads[lineage] = (
                    shown.version_number, shown.content, head.id,
                )
            scope = build_scope or target_scope or self._conversation_scope(row)
            if material is None:
                material = self._output_material(session, base_id, scope, strict=build is not None)
            tool = self._output_search_tool(
                session, list(material.source_revisions), f"{library.title}: {library.question}"
            )
            web, web_reason = self._web_tool(web_enabled)
            pinned_keys = {item.key for item in material.seeds}
            reader = self._source_reader(material.source_revisions, pinned_keys=pinned_keys)
            history = self._turn_history(session, row.id, agent_limits.history)
            figure_readings = self._reusable_figure_readings(session, base_id)

        with usage.job("outputs", ref=conversation_id):
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
                    reusable_figures=figure_readings,
                )

            def evidence_allowed(item: Evidence) -> bool:
                return self._output_evidence_in_scope(
                    item, material.source_revisions, pinned_keys, web=web is not None,
                )

            def studio_reading(made: Pool, query: str = "") -> Notes:
                with self.sessions() as session:
                    matches = key_results.retrieve(
                        session, base_id, material.source_revisions.keys(), query,
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
                                made.adopt(item)
                                searched.append(SearchHit(item, query, True))
                            pinned.update(fresh)
                            # Later steps of this turn read and cite these pinned readings too.
                            pinned_keys.update(fresh)
                            remaining -= len(fresh)
                    reading_gaps = self._studio_reading(
                        session, material.source_revisions, made, pinned=list(pinned.values()),
                        searched=searched,
                    )
                orientation = key_result_refs(matches, made.items)
                for result_id, (sentence, refs) in orientation.items():
                    key_results_read.setdefault(result_id, (sentence, {}))[1].update(refs)
                return replace(
                    material.notes, extraction_gaps=tuple(reading_gaps),
                    key_results=key_result_lines(orientation),
                )

            def studio(
                spec: Spec, made: Pool, use: str | None, resume=None, checkpoint=None,
                *, purpose=None, fixed_outline=None,
            ) -> BuiltOutput:
                scoped_notes = studio_reading(
                    made, spec.brief or spec.title or library_question or library_title,
                )
                agents = StudioAgents(
                    gateway,
                    limits,
                    use=use,
                    renderer=self.slide_renderer,
                    asset_reader=self.asset_reader,
                    reusable_figures=figure_readings,
                )
                if (build is not None or payload.rebuild) and web_enabled:
                    warning = self._studio_web(
                        web, web_reason, spec.brief or spec.title or "", made, limits.per_section,
                        searched=searched,
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
                    fallback_title=library_title,
                    purpose=purpose,
                    fixed_outline=fixed_outline,
                    outline_approved=payload.rebuild is not None or bool(build and build.outline),
                    scope={"selection": scope.model_dump(), "inputs": material.inputs},
                    web=web_enabled,
                    resume=resume,
                    checkpoint=checkpoint,
                )

            prepared = None
            if build is not None:
                identity = build.model_dump(mode="json")
                if target is not None:
                    identity["parent"] = heads[target]
                build_key = self._output_fingerprint(
                    workspace_id, base_id, "build-draft", identity,
                )
                brief = brief.model_copy(deep=True)
                prepared = _build_work(build, workspace, brief, target, build_key)
                draft = prepared[1]
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
                    library=library_title,
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
                    searched=searched,
                )
            )
            try:
                if build is not None and prepared is not None:
                    approved = [
                        Planned(i.heading, i.goal, id=i.id) for i in build.outline or ()
                    ]
                    with usage.job(detail=OUTPUT_TYPES[build.type].shape):
                        result = agent.build(*prepared, approved or None, reuse_plan=reuse_plan)
                elif payload.rebuild is None:
                    result = agent.run()
                else:
                    action = payload.rebuild
                    work = next((w for w in workspace.works if w.key == target), None)
                    if work is None:
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
                artifact_title=artifact.title if artifact else None,
                build_scope=build_scope if artifact else None,
                existing_session=session,
            )  # fmt: skip

        artifact = message = None
        if result is not None and result.save is not None:
            try:
                cited = [
                    workspaces.evidence_from_citation(item) for item in result.save.built.citations
                ]
                artifact, message = self._save_turn_version(
                    base_id, workspace_id, conversation_id, payload, model, effort, result,
                    workspace, brief, scope, material.inputs, material.snapshots, heads, emit, keep,
                    fingerprint,
                    searched=pack_searched(searched, cited),
                    key_results_used=pack_key_results(key_results_read, cited),
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
                request_id=None, build_draft=draft,
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
            .where(ArtifactMessage.conversation_id == conversation_id, live_artifact_message())
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
        fingerprint: str,
        *,
        searched: list[SearchedHitOut],
        key_results_used: list[UsedKeyResultOut],
    ) -> tuple[ArtifactOut, ArtifactMessageOut | None]:
        """Seal the artifact a turn finished as a version, and make its work match it."""

        request = result.save
        assert request is not None
        work, built = request.work, request.built
        spec = workspaces.spec_of(brief, work)
        kind = spec.kind
        build = payload.build
        title = self.artifact_conversation_title(base_id, workspace_id, conversation_id)
        if build is not None and title == NEW_CONVERSATION:
            title = built.title
        record = work.model_copy(deep=True)
        record.version_id, record.version_map, record.skipped = None, request.version_map, []
        parent = heads.get(work.key)
        origin = "revise" if parent else "build"
        if payload.rebuild or (build and parent):
            origin = "rebuild"
        generator = "gunther.studio.v1" if build is not None else "gunther.artifact-agent.v1"
        message = None

        def publish(session: Session, artifact: ArtifactOut) -> None:
            nonlocal message
            saved = record.model_copy(update={
                "key": artifact.lineage_id, "version_id": artifact.id, "title": artifact.title,
            })
            name = SHAPES[kind].name
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
            fingerprint=fingerprint,
            parent_id=parent,
            kind=kind,
            style=spec.output_type,
            audience=build.audience if build is not None else audience_of(brief),
            title=built.title,
            brief=build.focus if build is not None else spec.brief,
            origin=origin,
            content=built.content,
            scope=scope.model_dump(),
            inputs=inputs,
            outline=built.outline,
            citations=[item.model_dump() for item in built.citations],
            snapshots=snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by={
                "generator": generator,
                "skill": built.skill,
                "approach": built.approach,
                "use": (
                    work.spec.use.text
                    if work.spec.use and work.spec.use.text in ("talk", "read")
                    else None
                ),
                "length": spec.length,
                "language": spec.language,
                "conversation_id": conversation_id,
                "conversation_title": title,
                "request": payload.text,
                "reply": request.summary,
                "changes": request.changes,
                "searched": searched,
                "key_results_used": key_results_used,
                "work": record.model_dump(mode="json"),
            },
            publish=publish,
            voices=build.voices if build else None,
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
        artifact_title: str | None = None,
        build_scope: OutputScope | None = None,
        build_draft: workspaces.Work | None = None,
        existing_session: Session | None = None,
    ) -> ArtifactMessageOut:
        """Keep the turn: both messages, the workspace and the brief. What the user typed in the
        panel while it ran is kept over the turn's version of it. A failed build records its
        messages and draft checkpoints without publishing its staged workspace or brief."""

        transaction = (
            nullcontext(existing_session)
            if existing_session is not None else session_scope(self.sessions)
        )
        with transaction as session:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            asked = utc_now()
            if workspace is not None:
                stored, now = self._load_workspace(row)
                workspace.refinement_scopes = stored.refinement_scopes
                brief = workspaces.keep_edits(began[0], brief, now)
                theirs = {work.key: work.spec for work in stored.works}
                for work in workspace.works:
                    before = began[1].get(work.key)
                    if before is not None and work.key in theirs:
                        work.spec = workspaces.keep_spec_edits(before, work.spec, theirs[work.key])
                hidden = self._hidden_works(stored, self._live_lineages(session, row))
                present = {work.key for work in workspace.works}
                workspace.works.extend(
                    work for work in stored.works if work.key in hidden and work.key not in present
                )
                if build_scope is not None:
                    row.scope_json = json.dumps(build_scope.model_dump())
                    if artifact_title and row.title == NEW_CONVERSATION:
                        row.title = artifact_title
                elif row.title == NEW_CONVERSATION:
                    row.title = " ".join(payload.text.split())[:160] or NEW_CONVERSATION
                self._store_workspace(row, workspace, brief, at=asked + timedelta(milliseconds=1))
            elif build_draft is not None and (
                progress := (build_draft.approach or {}).get("studio_progress")
            ) is not None:
                stored = workspaces.load_workspace(row.state_json)
                saved = next((work for work in stored.works if work.key == build_draft.key), None)
                if saved is None:
                    if not stored.works and stored.active is None:
                        stored.active = build_draft.key
                    stored.works.append(build_draft)
                else:
                    saved.approach = {
                        **(saved.approach or {}),
                        "studio_progress": progress,
                    }
                row.state_json = workspaces.dump_workspace(stored)
            focus = payload.focus.model_dump() if payload.focus else None
            session.add(
                ArtifactMessage(
                    id=user_id,
                    conversation_id=row.id,
                    role="user",
                    kind=payload.kind,
                    text=payload.text,
                    payload_json=json.dumps(
                        {**payload.request_fields(), **({"focus": focus} if focus else {})},
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
