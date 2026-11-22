"""Own Ask sessions, briefs, streamed turns and interrupted questions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from gunther import brief as briefs
from gunther import trace, usage
from gunther.agent import DEFAULT_STYLE, STYLES, AskAgent
from gunther.artifact_workspace import evidence_from_citation
from gunther.ask_research import Notes as Work
from gunther.database import session_scope
from gunther.evidence import Evidence, Toolbox
from gunther.llm import ModelError, Turn
from gunther.model_profiles import Effort
from gunther.models import (
    ContentBlock,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeSession,
    MessageTrace,
    SessionBranch,
    SessionMessage,
    Source,
    SourceRevision,
    utc_now,
)
from gunther.schemas import (
    BriefOut,
    ConversationCitationOut,
    ConversationContextOut,
    ConversationTurnOut,
    CreateKnowledgeSessionInput,
    CreateSessionMessageInput,
    KnowledgeSessionOut,
    KnowledgeSessionSummaryOut,
    SessionMessageOut,
    UpdateKnowledgeSessionInput,
)
from gunther.service_records import HOME_SCOPE, _id, _timestamp
from gunther.skillbook import AskSkill, ask_skills
from gunther.topics import topic_block_ids, topic_source_ids

# (plan, check) effort for Ask's own steps; see AskAgent. The exam may override them.
ASK_EFFORTS: tuple[Effort, Effort] = ("low", "off")


class AskFailure(RuntimeError):
    def __init__(self, detail: str, status: int = 502, *, needs_model: bool = False) -> None:
        super().__init__(detail)
        self.status = status
        self.needs_model = needs_model
        self.kept_trace = trace.current()
        trace.note("failure", "Ask could not answer", error=detail)


@dataclass
class _Answer:
    """What Ask answered with, and how."""

    content: str
    evidence: list[Evidence]
    model_ref: str | None = None
    model_label: str | None = None
    effort: str | None = None
    effort_label: str | None = None
    notes: tuple[str, ...] = ()
    reasoning: str | None = None
    steps: list = field(default_factory=list)
    checked: bool = False
    support_notes: tuple[str, ...] = ()
    coverage: dict | None = None
    budget_usage: dict | None = None
    work: Work | None = None
    skill: AskSkill | None = None
    research: dict | None = None


# Reasoning is kept for the model that wrote it; past this it is cut.
MAX_KEPT_REASONING = 60_000


# Traces of the latest answers kept for Settings → Developer; older ones are dropped.
KEPT_TRACES = 200


def conversation_history(messages: list[SessionMessage]) -> list[Turn]:
    """A conversation in neutral form, for whichever model answers next."""

    turns: list[Turn] = []
    for message in messages:
        if message.role == "user":
            # A question that was stopped or failed has no answer; the model never saw it.
            if '"interrupted"' in (message.context_json or "") and json.loads(
                message.context_json
            ).get("interrupted"):
                continue
            turns.append(Turn("user", message.content))
        elif message.role == "assistant":
            context = json.loads(message.context_json or "{}")
            content = _in_pool_numbers(message)
            turns.append(Turn("assistant", content, context.get("model"), context.get("reasoning")))
    return turns


def _in_pool_numbers(message: SessionMessage) -> str:
    """An answer as the model should read it: its [1], [2]… (numbered per answer, in the
    order of its source list) turned back into the conversation pool's numbers, which
    are what the model is shown for every source. Answers from before the pool have no
    such numbers, so their markers are left out."""

    refs = [item.get("ref") for item in json.loads(message.citations_json or "[]")]

    def replace(match: re.Match[str]) -> str:
        found = []
        for part in re.split(r"[,，、]", match.group(3)):
            at = int(part) - 1
            if 0 <= at < len(refs) and refs[at] is not None:
                found.append(f"[{refs[at]}]")
        if not found:
            return ""
        if match.group(2) == "d":
            return f"{match.group(1) or ' '}(disputed by {''.join(found)})"
        return f"{match.group(1)}{''.join(found)}"

    # [i:2] (an inference) and [p:2] (partly supported) are read as plain pool numbers;
    # [d:2] says the claim was disputed by that source.
    return re.sub(r"(\s?)\[(?:(i|p|d):)?(\d+(?:\s*[,，、]\s*\d+)*)\]", replace, message.content)


def _work_out(work: Work | None, cited: list[Evidence]) -> dict[str, object] | None:
    """What a question found, for the saved answer: a finding about a source the answer
    cites says its reading number (the one in the text); any other keeps its title."""

    if work is None:
        return None
    reading = {item.ref: number for number, item in enumerate(cited, start=1)}
    return {
        "subQuestions": [
            {"id": sub.id, "text": sub.text, "query": sub.query} for sub in work.sub_questions
        ],
        "findings": [
            {
                **({"ref": reading[f.ref]} if f.ref in reading else {"title": f.title}),
                "serves": f.serves,
                "says": f.says,
            }
            for f in work.findings
        ],
    }


def conversation_pool(messages: list[SessionMessage]) -> list[Evidence]:
    """The sources a conversation holds: everything its answers cited, each under the
    number it was given the first time."""

    held: dict[int, Evidence] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        for item in json.loads(message.citations_json or "[]"):
            ref = item.get("ref")
            if ref is None or ref in held:
                continue
            held[ref] = evidence_from_citation(ConversationCitationOut.model_validate(item))
    return [held[ref] for ref in sorted(held)]


class AskTurns:
    def _session_query(self):
        return select(KnowledgeSession).options(
            selectinload(KnowledgeSession.messages),
            selectinload(KnowledgeSession.branch),
        )

    def _message_out(self, message: SessionMessage) -> SessionMessageOut:
        citations = [
            ConversationCitationOut.model_validate(item)
            for item in json.loads(message.citations_json or "[]")
        ]
        context_payload = json.loads(message.context_json or "{}")
        return SessionMessageOut(
            id=message.id,
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            citations=citations,
            context=ConversationContextOut.model_validate(context_payload),
            created_at=_timestamp(message.created_at),
        )

    def _session_summary(self, knowledge_session: KnowledgeSession) -> KnowledgeSessionSummaryOut:
        return KnowledgeSessionSummaryOut(
            id=knowledge_session.id,
            knowledge_base_id=knowledge_session.knowledge_base_id,
            title=knowledge_session.title,
            summary=knowledge_session.summary,
            focus_chapter_id=knowledge_session.focus_chapter_id,
            selected_source_ids=json.loads(knowledge_session.selected_source_ids_json or "[]"),
            pinned=knowledge_session.pinned,
            archived=knowledge_session.archived,
            parent_session_id=(
                knowledge_session.branch.parent_session_id if knowledge_session.branch else None
            ),
            branched_from_message_id=(
                knowledge_session.branch.branched_from_message_id
                if knowledge_session.branch
                else None
            ),
            message_count=len(knowledge_session.messages),
            created_at=_timestamp(knowledge_session.created_at),
            updated_at=_timestamp(knowledge_session.updated_at),
        )

    def create_knowledge_session(
        self, knowledge_base_id: str, payload: CreateKnowledgeSessionInput
    ) -> KnowledgeSessionOut:
        now = utc_now()
        with session_scope(self.sessions) as session:
            if (
                knowledge_base_id != HOME_SCOPE
                and self._live_knowledge_base(session, knowledge_base_id) is None
            ):
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            knowledge_session = KnowledgeSession(
                id=_id("ses"),
                knowledge_base_id=knowledge_base_id,
                title=(payload.title or "New session").strip(),
                focus_chapter_id=payload.focus_chapter_id,
                selected_source_ids_json=json.dumps(payload.selected_source_ids),
                created_at=now,
                updated_at=now,
            )
            session.add(knowledge_session)
            self._validate_source_scope(session, knowledge_base_id, payload.selected_source_ids)
            session.flush()
            return KnowledgeSessionOut(
                **self._session_summary(knowledge_session).model_dump(), messages=[]
            )

    def list_knowledge_sessions(
        self, knowledge_base_id: str, include_archived: bool = False
    ) -> list[KnowledgeSessionSummaryOut]:
        with session_scope(self.sessions) as session:
            query = self._session_query().where(
                KnowledgeSession.knowledge_base_id == knowledge_base_id
            )
            if not include_archived:
                query = query.where(KnowledgeSession.archived.is_(False))
            query = query.order_by(
                KnowledgeSession.pinned.desc(), KnowledgeSession.updated_at.desc()
            )
            sessions = session.scalars(query).all()
            return [self._session_summary(item) for item in sessions]

    def get_knowledge_session(self, session_id: str) -> KnowledgeSessionOut:
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            return KnowledgeSessionOut(
                **self._session_summary(knowledge_session).model_dump(),
                messages=[self._message_out(message) for message in knowledge_session.messages],
                brief=self._brief_out(briefs.load(knowledge_session.brief_json)),
            )

    @staticmethod
    def _brief_out(stored: briefs.Stored) -> BriefOut:
        return BriefOut(
            goal=stored.brief.goal,
            constraints=stored.brief.constraints,
            settled=stored.brief.settled,
            open=stored.brief.open,
            edited=stored.edited,
            error=stored.error,
        )

    def _brief_session(self, session: Session, session_id: str) -> KnowledgeSession:
        knowledge_session = session.scalar(
            self._session_query().where(KnowledgeSession.id == session_id)
        )
        if knowledge_session is None:
            raise LookupError(f"Session {session_id} was not found")
        return knowledge_session

    def brief(self, session_id: str) -> BriefOut:
        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            return self._brief_out(briefs.load(knowledge_session.brief_json))

    @staticmethod
    def _last_exchange(
        messages: list[SessionMessage],
    ) -> tuple[SessionMessage, SessionMessage] | None:
        """The latest answer and the question it answers."""

        for at in range(len(messages) - 1, 0, -1):
            if messages[at].role == "assistant" and messages[at - 1].role == "user":
                return messages[at - 1], messages[at]
        return None

    def _catch_up_brief(self, session_id: str) -> None:
        """Fold the last answer into the brief before the next question, when the page
        never asked for it (closed too soon, or the update failed)."""

        try:
            with session_scope(self.sessions) as session:
                knowledge_session = self._brief_session(session, session_id)
                last = self._last_exchange(knowledge_session.messages)
                behind = (
                    last is not None
                    and briefs.load(knowledge_session.brief_json).through != last[1].id
                )
            if behind:
                self.refresh_brief(session_id)
        except LookupError:
            return  # the turn itself reports a session that is not there
        except Exception:
            # The brief is a help, never a reason a question cannot be answered.
            from gunther.service import logger

            logger.exception("Catching up the brief of %s failed", session_id)

    def refresh_brief(self, session_id: str) -> BriefOut:
        """Bring the brief up to date with the conversation's last answer. A failed
        update keeps the old brief and records why."""

        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            stored = briefs.load(knowledge_session.brief_json)
            last = self._last_exchange(knowledge_session.messages)
            if last is None or stored.through == last[1].id:
                return self._brief_out(stored)
            question, answered = last[0].content, last[1]
            answer = _in_pool_numbers(answered)
            answered_id = answered.id
            used = json.loads(answered.context_json or "{}").get("model")
            pool_refs = {
                item.ref for item in conversation_pool(knowledge_session.messages) if item.ref
            }
            supported: list[str] = []
            verified_refs: set[int] = set()
            for message in knowledge_session.messages:
                context = json.loads(message.context_json or "{}")
                coverage = context.get("coverage") or {}
                if (
                    coverage.get("answerHash")
                    != hashlib.sha256(message.content.encode()).hexdigest()
                ):
                    continue
                for claim in coverage.get("claims", []):
                    if claim.get("verdict") == "supported" and not re.search(
                        r"\[(?:[ipd]:|\?)", claim.get("text", "")
                    ):
                        supported.append(claim["text"])
                        verified_refs.update(claim.get("refs", []))
            pool_refs &= verified_refs
        chosen = self.models.for_role("ask") if self.models else None
        model = chosen[0] if chosen else (self.models.get(used) if self.models else None)
        if model is None or self.models is None:
            stored.error = "Set up a model under Settings → Models to keep a brief."
            return self._save_brief(session_id, stored, expect=stored.brief)
        _, check_effort = self.agent_efforts or ASK_EFFORTS
        with usage.job("analysis", "brief", ref=session_id):
            updated = briefs.BriefKeeper(self.models, check_effort).update(
                model, stored, question, answer, pool_refs, supported_claims=supported
            )
        if updated.error is None:
            updated.through = answered_id
        return self._save_brief(session_id, updated, expect=stored.brief, was=stored.edited)

    def _save_brief(
        self,
        session_id: str,
        stored: briefs.Stored,
        expect: briefs.Brief,
        was: list[str] | None = None,
    ) -> BriefOut:
        """Keep a brief, unless the user edited it while a model was working on it: then
        theirs stays and the next question catches up."""

        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            current = briefs.load(knowledge_session.brief_json)
            if current.brief != expect or (was is not None and current.edited != was):
                return self._brief_out(current)
            knowledge_session.brief_json = briefs.dump(stored)
            return self._brief_out(stored)

    def edit_brief(self, session_id: str, brief: briefs.Brief) -> BriefOut:
        """Save the user's edit; the lines they changed are theirs from now on."""

        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            before = briefs.load(knowledge_session.brief_json)
            tidy = briefs.Brief(
                goal=" ".join(brief.goal.split()),
                constraints=briefs.unique(brief.constraints),
                settled=[
                    item.model_copy(update={"text": " ".join(item.text.split())})
                    for item in brief.settled
                    if item.text.strip()
                ],
                open=briefs.unique(brief.open),
            )
            stored = briefs.Stored(
                tidy, briefs.edited_paths(before, tidy), before.through, before.error
            )
            knowledge_session.brief_json = briefs.dump(stored)
            return self._brief_out(stored)

    def branch_knowledge_session(self, session_id: str, message_id: str) -> KnowledgeSessionOut:
        with session_scope(self.sessions) as session:
            parent = session.scalar(self._session_query().where(KnowledgeSession.id == session_id))
            if parent is None:
                raise LookupError(f"Session {session_id} was not found")
            branch_index = next(
                (
                    index
                    for index, message in enumerate(parent.messages)
                    if message.id == message_id
                ),
                None,
            )
            if branch_index is None:
                raise LookupError(f"Message {message_id} was not found in this session")

            now = utc_now()
            branch = KnowledgeSession(
                id=_id("ses"),
                knowledge_base_id=parent.knowledge_base_id,
                title=f"Branch · {parent.title}"[:160],
                summary=parent.summary,
                focus_chapter_id=parent.focus_chapter_id,
                selected_source_ids_json=parent.selected_source_ids_json,
                created_at=now,
                updated_at=now,
            )
            for original in parent.messages[: branch_index + 1]:
                branch.messages.append(
                    SessionMessage(
                        id=_id("msg"),
                        role=original.role,
                        content=original.content,
                        citations_json=original.citations_json,
                        context_json=original.context_json,
                        created_at=original.created_at,
                    )
                )
            session.add(branch)
            self._validate_source_scope(
                session,
                branch.knowledge_base_id,
                json.loads(branch.selected_source_ids_json or "[]"),
            )
            session.flush()
            branch.branch = SessionBranch(
                id=_id("brn"),
                session_id=branch.id,
                parent_session_id=parent.id,
                branched_from_message_id=message_id,
            )
            return KnowledgeSessionOut(
                **self._session_summary(branch).model_dump(),
                messages=[self._message_out(message) for message in branch.messages],
            )

    def file_home_session(
        self, session_id: str, knowledge_base_id: str
    ) -> KnowledgeSessionSummaryOut:
        """Move a Home conversation into a library; it keeps its messages and citations."""

        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None or knowledge_session.knowledge_base_id != HOME_SCOPE:
                raise LookupError(f"Home conversation {session_id} was not found")
            if self._live_knowledge_base(session, knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            knowledge_session.knowledge_base_id = knowledge_base_id
            knowledge_session.selected_source_ids_json = "[]"
            knowledge_session.focus_chapter_id = None
            knowledge_session.updated_at = utc_now()
            session.flush()
            return self._session_summary(knowledge_session)

    def update_knowledge_session(
        self, session_id: str, payload: UpdateKnowledgeSessionInput
    ) -> KnowledgeSessionSummaryOut:
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            if knowledge_session.archived and (
                payload.selected_source_ids is not None
                or "focus_chapter_id" in payload.model_fields_set
            ):
                raise ValueError(
                    "Restore the archived session before changing its retrieval context"
                )
            if payload.title is not None:
                knowledge_session.title = payload.title.strip()
            if "focus_chapter_id" in payload.model_fields_set:
                knowledge_session.focus_chapter_id = payload.focus_chapter_id
            if payload.selected_source_ids is not None:
                knowledge_session.selected_source_ids_json = json.dumps(payload.selected_source_ids)
                self._validate_source_scope(
                    session,
                    knowledge_session.knowledge_base_id,
                    payload.selected_source_ids,
                )
            if payload.pinned is not None:
                knowledge_session.pinned = payload.pinned
            if payload.archived is not None:
                knowledge_session.archived = payload.archived
            knowledge_session.updated_at = utc_now()
            session.flush()
            return self._session_summary(knowledge_session)

    def _answer(
        self,
        question: str,
        history: list[Turn],
        toolbox: Toolbox,
        model_ref: str | None,
        effort: Effort | None,
        events: Callable[[dict], None] | None = None,
        pool: list[Evidence] | None = None,
        style: str | None = None,
        numbered: int = 0,
        brief: str = "",
        skill: AskSkill | None = None,
        budget: str | None = None,
        stopping: Callable[[], bool] | None = None,
    ) -> _Answer:
        """Run the agent with the chosen model (or the Ask default) and describe the outcome."""

        model = None
        if model_ref:
            model = self.models.get(model_ref) if self.models else None
            if model is None:
                raise AskFailure(
                    "That model is not set up, or its provider needs a key.",
                    400,
                    needs_model=True,
                )
        elif self.models is not None:
            chosen = self.models.for_role("ask")
            if chosen is not None:
                model, default_effort = chosen
                effort = effort or default_effort
        if model is None or self.models is None:
            raise AskFailure("Ask needs a language model or provider key.", 412, needs_model=True)
        plan_effort, check_effort = self.agent_efforts or ASK_EFFORTS
        agent = AskAgent(self.models, plan_effort=plan_effort, check_effort=check_effort)
        try:
            result = agent.run(
                question, history, toolbox, model, effort, events, pool or (), style, numbered,
                brief=brief, skill=skill, budget_name=budget, stopping=stopping,
            )
        except ModelError as error:
            raise AskFailure(str(error)) from error
        if result.error is not None:
            raise AskFailure(result.error)
        completion = result.completion
        return _Answer(
            content=result.content,
            evidence=result.evidence,
            model_ref=model.ref,
            model_label=model.display,
            effort=effort,
            steps=result.steps,
            notes=result.notes,
            skill=result.skill,
            research=result.research,
            checked=result.checked,
            support_notes=result.support_notes,
            coverage=result.coverage,
            budget_usage=result.budget_usage,
            work=result.work,
            effort_label=completion.effort_label if completion else None,
            reasoning=completion.reasoning if completion else None,
        )

    def create_session_turn(
        self,
        session_id: str,
        payload: CreateSessionMessageInput,
        events: Callable[[dict], None] | None = None,
        stopping: Callable[[], bool] | None = None,
    ) -> ConversationTurnOut:
        # Refused before anything runs.
        skill = None
        if payload.skill:
            skill = ask_skills().get(payload.skill.strip().lstrip("/"))
            if skill is None:
                raise ValueError(f"Unknown skill: /{payload.skill.strip().lstrip('/')}")
        self._catch_up_brief(session_id)
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            library = (
                session.get(KnowledgeBaseRecord, knowledge_session.knowledge_base_id)
                if knowledge_session
                else None
            )
            if knowledge_session is None or (library and library.trashed_at is not None):
                raise LookupError(f"Session {session_id} was not found")
            if knowledge_session.archived:
                raise ValueError("Restore the archived session before adding another message")
            if payload.selected_source_ids is not None:
                knowledge_session.selected_source_ids_json = json.dumps(payload.selected_source_ids)
                self._validate_source_scope(
                    session,
                    knowledge_session.knowledge_base_id,
                    payload.selected_source_ids,
                )
            if "focus_chapter_id" in payload.model_fields_set:
                knowledge_session.focus_chapter_id = payload.focus_chapter_id

            selected_source_ids = json.loads(knowledge_session.selected_source_ids_json or "[]")
            if knowledge_session.knowledge_base_id == HOME_SCOPE:
                scoped_source_ids = self._home_source_ids(
                    session, selected_source_ids, payload.knowledge_base_ids
                )
            else:
                if selected_source_ids:
                    scoped_source_ids = selected_source_ids
                else:
                    scoped_source_ids = list(
                        session.scalars(
                            select(KnowledgeBaseSource.source_id).where(
                                KnowledgeBaseSource.knowledge_base_id
                                == knowledge_session.knowledge_base_id
                            )
                        ).all()
                    )
                # Recheck membership on every turn, including persisted session scope.
                scoped_source_ids = list(
                    session.scalars(
                        select(KnowledgeBaseSource.source_id).where(
                            KnowledgeBaseSource.knowledge_base_id
                            == knowledge_session.knowledge_base_id,
                            KnowledgeBaseSource.source_id.in_(scoped_source_ids),
                            KnowledgeBaseSource.source_id.not_in(
                                select(Source.id).where(Source.trashed_at.is_not(None))
                            ),
                        )
                    )
                )
            block_scope = None
            if knowledge_session.focus_chapter_id and knowledge_session.focus_chapter_id.startswith(
                "topic_"
            ):
                base_id = knowledge_session.knowledge_base_id
                topic_id = knowledge_session.focus_chapter_id
                try:
                    block_scope = topic_block_ids(session, base_id, topic_id)
                    filed = topic_source_ids(session, base_id, topic_id)
                except LookupError:
                    # The section is gone: ask the whole library.
                    block_scope, filed = None, []
                if filed:
                    # A topic of whole papers: ask all of them, and the sources of any
                    # passages linked to it one by one.
                    linked = set(filed) | set(
                        session.scalars(
                            select(SourceRevision.source_id)
                            .join(ContentBlock, ContentBlock.revision_id == SourceRevision.id)
                            .where(ContentBlock.id.in_(block_scope))
                        )
                    )
                    scoped_source_ids = [item for item in scoped_source_ids if item in linked]
                    block_scope = None
            scope = self._library_scope(session, scoped_source_ids, block_scope)
            scoped_sources = (
                list(
                    session.scalars(
                        select(Source)
                        .where(Source.id.in_(scoped_source_ids))
                        .order_by(Source.created_at.desc())
                    ).all()
                )
                if scoped_source_ids
                else []
            )
            available_assertions = scope.assertions

            web_tool, web_off_reason = self._web_tool(payload.web)
            about = f"{library.title}: {library.question}" if library else "all your libraries"
            offered = []
            notes = []
            if scoped_source_ids:
                offered.append(self._library_tool(session, scope, about))
            else:
                notes.append("there is no library to search here")
            if web_tool:
                offered.append(web_tool)
            else:
                notes.append(f"the web was not searched: {web_off_reason}")
            toolbox = Toolbox(
                tuple(offered),
                tuple(notes),
                reader=self._ask_reader(session, web_tool is not None, scope),
            )
            history = conversation_history(knowledge_session.messages)
            stored = briefs.load(knowledge_session.brief_json)
            question = payload.content.strip()
            everything = conversation_pool(knowledge_session.messages)
            # Only what this question may read: a library source that is out of scope now
            # (another library was picked, a source was removed) stays out of the answer.
            in_scope = set(scoped_source_ids)
            pool = [
                item
                for item in everything
                if item.kind != "library" or item.payload[0].source_id in in_scope
            ]
            # Settings → Developer: keep how this answer is made, step by step.
            kept = trace.Trace() if self.tracing else None
            with usage.job("ask", ref=session_id), trace.tracing(kept):
                response = self._answer(
                    question,
                    history,
                    toolbox,
                    payload.model,
                    payload.effort,
                    events,
                    pool,
                    payload.style,
                    max((item.ref or 0 for item in everything), default=0),
                    brief=briefs.render(stored.brief, stored.edited),
                    skill=skill,
                    budget=payload.budget,
                    stopping=stopping,
                )
            citations = [
                item.payload[0].model_copy(update={"ref": item.ref}) for item in response.evidence
            ]
            context = ConversationContextOut(
                sources_considered=len(scoped_sources),
                assertions_considered=len(available_assertions),
                verified_assertions=sum(item.status == "verified" for item in available_assertions),
                retrieval_mode="selected" if selected_source_ids else "all",
                responder_mode="model",
                model=response.model_ref,
                model_label=response.model_label,
                effort=response.effort,
                effort_label=response.effort_label,
                notes=list(response.notes),
                reasoning=(response.reasoning or "")[:MAX_KEPT_REASONING] or None,
                style=payload.style if payload.style in STYLES else DEFAULT_STYLE,
                steps=[step.out() for step in response.steps],
                web_searched=any(step.tool == "search_web" for step in response.steps),
                checked=response.checked,
                support_notes=list(response.support_notes),
                coverage=response.coverage,
                budget_usage=response.budget_usage,
                work=_work_out(response.work, response.evidence),
                skill=(
                    {
                        "name": response.skill.name,
                        "version": response.skill.version,
                        "title": response.skill.title,
                        "auto": False,
                    }
                    if response.skill
                    else None
                ),
                research=response.research,
            )
            if events:
                events({"type": "saving"})  # a reader who left by now gets nothing saved
            now = utc_now()
            user_message = SessionMessage(
                id=_id("msg"),
                session_id=knowledge_session.id,
                role="user",
                content=payload.content.strip(),
                created_at=now,
            )
            assistant_message = SessionMessage(
                id=_id("msg"),
                session_id=knowledge_session.id,
                role="assistant",
                content=response.content,
                citations_json=json.dumps(
                    [item.model_dump() for item in citations], ensure_ascii=False
                ),
                context_json=context.model_dump_json(),
                created_at=now + timedelta(microseconds=1),
            )
            knowledge_session.messages.extend([user_message, assistant_message])
            if knowledge_session.title == "New session":
                title = re.sub(r"\s+", " ", payload.content.strip())
                knowledge_session.title = title[:62] + ("…" if len(title) > 62 else "")
            knowledge_session.summary = re.sub(r"\s+", " ", response.content)[:220]
            knowledge_session.updated_at = now
            session.flush()
            if kept is not None:
                self._keep_trace(session, assistant_message.id, kept)
            return ConversationTurnOut(
                session=self._session_summary(knowledge_session),
                user_message=self._message_out(user_message),
                assistant_message=self._message_out(assistant_message),
            )

    def _keep_trace(self, session: Session, message_id: str, kept: trace.Trace) -> None:
        """Save how an answer was made; only the latest traces stay."""

        text = json.dumps(kept.out(), ensure_ascii=False)
        session.add(MessageTrace(message_id=message_id, trace_json=text))
        session.flush()
        stale = session.scalars(
            select(MessageTrace.message_id)
            .order_by(MessageTrace.created_at.desc())
            .offset(KEPT_TRACES)
        ).all()
        if stale:
            session.execute(delete(MessageTrace).where(MessageTrace.message_id.in_(stale)))

    def message_trace(self, session_id: str, message_id: str) -> dict | None:
        """How an answer in this conversation was made, if it was kept."""

        with session_scope(self.sessions) as session:
            row = session.scalar(
                select(MessageTrace)
                .join(SessionMessage, SessionMessage.id == MessageTrace.message_id)
                .where(
                    MessageTrace.message_id == message_id,
                    SessionMessage.session_id == session_id,
                )
            )
            return json.loads(row.trace_json) if row else None

    def record_interrupted_question(
        self,
        session_id: str,
        payload: CreateSessionMessageInput,
        reason: Literal["stopped", "failed"],
        failure: AskFailure | None = None,
    ) -> str | None:
        """Keep an interrupted question in the history and return its saved ID."""

        text = payload.content.strip()
        if not text:
            return None
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None or knowledge_session.archived:
                return None
            now = utc_now()
            context = ConversationContextOut(interrupted=reason)
            if failure is not None:
                context.model_error = str(failure)
                context.ask_failure = {
                    "needsModel": failure.needs_model,
                    "hasTrace": failure.kept_trace is not None,
                    "options": payload.model_dump(
                        mode="json", by_alias=True, exclude_unset=True,
                        exclude={"content", "model", "effort"},
                    ),
                }
            message = SessionMessage(
                id=_id("msg"), session_id=knowledge_session.id, role="user",
                content=text, context_json=context.model_dump_json(), created_at=now,
            )
            knowledge_session.messages.append(message)
            if knowledge_session.title == "New session":
                title = re.sub(r"\s+", " ", text)
                knowledge_session.title = title[:62] + ("…" if len(title) > 62 else "")
            knowledge_session.updated_at = now
            if failure is not None and failure.kept_trace is not None:
                session.flush()
                self._keep_trace(session, message.id, failure.kept_trace)
            return message.id

    def session_library(self, session_id: str) -> str | None:
        """The library a conversation belongs to (``@home`` for Home), None when missing."""

        with session_scope(self.sessions) as session:
            return session.scalar(
                select(KnowledgeSession.knowledge_base_id).where(KnowledgeSession.id == session_id)
            )
