"""Store and read proposals and their saved-knowledge revisions."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from gunther.database import session_scope
from gunther.models import (
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    Revision,
    SessionMessage,
    utc_now,
)
from gunther.schemas import (
    CreateKnowledgeProposalInput,
    KnowledgeProposalOut,
    KnowledgeUnitOut,
    UpdateKnowledgeProposalInput,
)
from gunther.service_records import _id, _timestamp


class KnowledgeUnits:
    def _proposal_out(
        self,
        knowledge_proposal: KnowledgeProposal,
        knowledge_session: KnowledgeSession,
        unit_revision: KnowledgeUnitRevision | None = None,
    ) -> KnowledgeProposalOut:
        return KnowledgeProposalOut(
            id=knowledge_proposal.id,
            knowledge_base_id=knowledge_proposal.knowledge_base_id,
            session_id=knowledge_proposal.session_id,
            message_id=knowledge_proposal.message_id,
            target_chapter_id=knowledge_proposal.target_chapter_id,
            kind=knowledge_proposal.kind,
            title=knowledge_proposal.title,
            content=knowledge_proposal.content,
            status=knowledge_proposal.status,
            decision_reason=knowledge_proposal.decision_reason,
            knowledge_unit_id=(unit_revision.unit_id if unit_revision else None),
            source_session_title=knowledge_session.title,
            created_at=_timestamp(knowledge_proposal.created_at),
            updated_at=_timestamp(knowledge_proposal.updated_at),
        )

    @staticmethod
    def _proposal_unit_revision(session: Session, proposal_id: str) -> KnowledgeUnitRevision | None:
        return session.scalar(
            select(KnowledgeUnitRevision).where(
                KnowledgeUnitRevision.source_proposal_id == proposal_id
            )
        )

    def _ensure_proposal_unit(
        self, session: Session, proposal: KnowledgeProposal
    ) -> KnowledgeUnitRevision:
        existing = self._proposal_unit_revision(session, proposal.id)
        if existing is not None:
            return existing
        knowledge_unit = KnowledgeUnit(
            id=_id("unt"),
            knowledge_base_id=proposal.knowledge_base_id,
            title=proposal.title,
            status="trusted",
        )
        session.add(knowledge_unit)
        session.flush()
        unit_revision = KnowledgeUnitRevision(
            id=_id("urv"),
            unit_id=knowledge_unit.id,
            revision_number=1,
            content=proposal.content,
            source_proposal_id=proposal.id,
        )
        session.add(unit_revision)
        session.flush()
        knowledge_unit.head_revision_id = unit_revision.id
        return unit_revision

    def _set_proposal_status(
        self,
        session: Session,
        proposal: KnowledgeProposal,
        status: str,
        reason: str | None,
    ) -> KnowledgeUnitRevision | None:
        """Record a decision on a saved answer and keep its knowledge unit in step."""

        previous = proposal.status
        proposal.status = status
        proposal.decision_reason = reason
        proposal.updated_at = utc_now()
        unit_revision = self._proposal_unit_revision(session, proposal.id)
        if status == "accepted" and unit_revision is None:
            unit_revision = self._ensure_proposal_unit(session, proposal)
        elif unit_revision is not None:
            knowledge_unit = session.get(KnowledgeUnit, unit_revision.unit_id)
            if knowledge_unit:
                knowledge_unit.status = {
                    "accepted": "trusted",
                    "rejected": "deprecated",
                }.get(status, "provisional")
                knowledge_unit.updated_at = utc_now()
        session.add(
            Revision(
                id=_id("rev"),
                target_type="proposal",
                target_id=proposal.id,
                action=f"status:{previous}->{status}",
                actor="user",
                reason=reason,
            )
        )
        session.flush()
        return unit_revision

    def ensure_saved_knowledge_units(self) -> None:
        """Give every saved answer its knowledge unit.

        Saving makes the unit at once, but databases from before that hold saved answers
        without one, so this runs on every start.
        """

        with session_scope(self.sessions) as session:
            without_unit = session.scalars(
                select(KnowledgeProposal).where(
                    KnowledgeProposal.status == "accepted",
                    KnowledgeProposal.id.not_in(select(KnowledgeUnitRevision.source_proposal_id)),
                )
            ).all()
            for proposal in without_unit:
                self._ensure_proposal_unit(session, proposal)

    def create_knowledge_proposal(
        self,
        session_id: str,
        message_id: str,
        payload: CreateKnowledgeProposalInput,
    ) -> KnowledgeProposalOut:
        """Save an answer as knowledge. This is the only review step: it counts at once."""

        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            message = session.scalar(
                select(SessionMessage).where(
                    SessionMessage.id == message_id,
                    SessionMessage.session_id == session_id,
                )
            )
            if message is None:
                raise LookupError(f"Message {message_id} was not found in this session")
            if message.role != "assistant":
                raise ValueError("Only an assistant synthesis can become a knowledge proposal")
            if not json.loads(message.citations_json or "[]"):
                raise ValueError(
                    "An evidence-backed citation is required before an answer can become knowledge"
                )
            existing = session.scalar(
                select(KnowledgeProposal).where(KnowledgeProposal.message_id == message_id)
            )
            if existing:
                if existing.status == "accepted":
                    unit_revision = self._proposal_unit_revision(
                        session, existing.id
                    ) or self._ensure_proposal_unit(session, existing)
                else:
                    # Saved again after it was removed from knowledge.
                    unit_revision = self._set_proposal_status(
                        session, existing, "accepted", "Saved as knowledge again"
                    )
                return self._proposal_out(existing, knowledge_session, unit_revision)

            generated_title = knowledge_session.title
            if generated_title == "New session":
                generated_title = "Session synthesis"
            proposal = KnowledgeProposal(
                id=_id("prp"),
                knowledge_base_id=knowledge_session.knowledge_base_id,
                session_id=knowledge_session.id,
                message_id=message.id,
                target_chapter_id=payload.target_chapter_id or knowledge_session.focus_chapter_id,
                title=(payload.title or generated_title).strip(),
                content=message.content,
            )
            session.add(proposal)
            session.flush()
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="proposal",
                    target_id=proposal.id,
                    action="created_from_session",
                    actor="user",
                    reason=f"Promoted from assistant message {message.id}",
                )
            )
            unit_revision = self._set_proposal_status(
                session, proposal, "accepted", "Saved as knowledge"
            )
            return self._proposal_out(proposal, knowledge_session, unit_revision)

    def list_knowledge_proposals(
        self, knowledge_base_id: str, status: str | None = None
    ) -> list[KnowledgeProposalOut]:
        with session_scope(self.sessions) as session:
            query = select(KnowledgeProposal).where(
                KnowledgeProposal.knowledge_base_id == knowledge_base_id
            )
            if status:
                query = query.where(KnowledgeProposal.status == status)
            proposals = session.scalars(query.order_by(KnowledgeProposal.updated_at.desc())).all()
            result: list[KnowledgeProposalOut] = []
            for proposal in proposals:
                knowledge_session = session.get(KnowledgeSession, proposal.session_id)
                if knowledge_session:
                    unit_revision = self._proposal_unit_revision(session, proposal.id)
                    result.append(self._proposal_out(proposal, knowledge_session, unit_revision))
            return result

    def update_knowledge_proposal(
        self, proposal_id: str, payload: UpdateKnowledgeProposalInput
    ) -> KnowledgeProposalOut:
        with session_scope(self.sessions) as session:
            proposal = session.get(KnowledgeProposal, proposal_id)
            if proposal is None:
                raise LookupError(f"Proposal {proposal_id} was not found")
            knowledge_session = session.get(KnowledgeSession, proposal.session_id)
            if knowledge_session is None:
                raise LookupError(f"Session {proposal.session_id} was not found")
            unit_revision = self._set_proposal_status(
                session, proposal, payload.status, payload.reason
            )
            return self._proposal_out(proposal, knowledge_session, unit_revision)

    def _knowledge_unit_out(
        self, session: Session, knowledge_unit: KnowledgeUnit
    ) -> KnowledgeUnitOut:
        head = next(
            (
                revision
                for revision in knowledge_unit.revisions
                if revision.id == knowledge_unit.head_revision_id
            ),
            knowledge_unit.revisions[-1] if knowledge_unit.revisions else None,
        )
        if head is None:
            raise ValueError(f"Knowledge Unit {knowledge_unit.id} has no revision")
        proposal = session.get(KnowledgeProposal, head.source_proposal_id)
        if proposal is None:
            raise ValueError(f"Knowledge Unit {knowledge_unit.id} has no source proposal")
        source_message = session.get(SessionMessage, proposal.message_id)
        citations = json.loads(source_message.citations_json or "[]") if source_message else []
        return KnowledgeUnitOut(
            id=knowledge_unit.id,
            knowledge_base_id=knowledge_unit.knowledge_base_id,
            title=knowledge_unit.title,
            kind=knowledge_unit.kind,
            status=knowledge_unit.status,
            content=head.content,
            revision_count=len(knowledge_unit.revisions),
            source_proposal_id=head.source_proposal_id,
            source_session_id=proposal.session_id,
            source_message_id=proposal.message_id,
            target_chapter_id=proposal.target_chapter_id,
            evidence_count=len(citations),
            created_at=_timestamp(knowledge_unit.created_at),
            updated_at=_timestamp(knowledge_unit.updated_at),
        )

    def list_knowledge_units(self, knowledge_base_id: str) -> list[KnowledgeUnitOut]:
        with session_scope(self.sessions) as session:
            knowledge_units = session.scalars(
                select(KnowledgeUnit)
                .options(selectinload(KnowledgeUnit.revisions))
                .where(KnowledgeUnit.knowledge_base_id == knowledge_base_id)
                .order_by(KnowledgeUnit.updated_at.desc())
            ).all()
            return [self._knowledge_unit_out(session, item) for item in knowledge_units]
