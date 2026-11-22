"""Manage libraries and read their Inbox, search, graph and overview."""

from __future__ import annotations

import json
import re
from collections import Counter
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session, selectinload

from gunther.database import session_scope
from gunther.models import (
    Assertion,
    Entity,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeSession,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    NotebookNote,
    Revision,
    Source,
    utc_now,
)
from gunther.schemas import (
    CreateKnowledgeBaseInput,
    GraphEdgeOut,
    GraphNodeOut,
    InboxItemOut,
    InboxKnowledgeBaseRefOut,
    KnowledgeBaseOut,
    KnowledgeGraphOut,
    KnowledgeSearchResultOut,
    OverviewCountsOut,
    OverviewOut,
    SourceSummaryOut,
    UpdateKnowledgeBaseInput,
)
from gunther.service_records import HOME_SCOPE, _id, _timestamp
from gunther.trash import live_membership_source_ids, trashed_library_ids

# Sources found by meaning, added after the exact matches of a search.
MEANING_RESULTS = 6


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:120] or "knowledge-base"


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or f'{singular}s'}"


class LibraryService:
    @staticmethod
    def _live_knowledge_base(
        session: Session, knowledge_base_id: str
    ) -> KnowledgeBaseRecord | None:
        """A library that exists and is not in Trash."""

        knowledge_base = session.get(KnowledgeBaseRecord, knowledge_base_id)
        return knowledge_base if knowledge_base and knowledge_base.trashed_at is None else None

    def _live_assertion_query(self):
        """Claims whose source is not in Trash."""

        return self._assertion_query().where(
            Assertion.source_id.not_in(select(Source.id).where(Source.trashed_at.is_not(None)))
        )

    def _knowledge_base_out(
        self, session: Session, knowledge_base: KnowledgeBaseRecord
    ) -> KnowledgeBaseOut:
        source_count = (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeBaseSource)
                .join(Source, Source.id == KnowledgeBaseSource.source_id)
                .where(
                    KnowledgeBaseSource.knowledge_base_id == knowledge_base.id,
                    Source.trashed_at.is_(None),
                )
            )
            or 0
        )
        session_count = (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeSession)
                .where(KnowledgeSession.knowledge_base_id == knowledge_base.id)
            )
            or 0
        )
        return KnowledgeBaseOut(
            id=knowledge_base.id,
            title=knowledge_base.title,
            eyebrow=knowledge_base.eyebrow,
            subtitle=knowledge_base.subtitle,
            question=knowledge_base.question,
            description=knowledge_base.description,
            color=knowledge_base.color,
            status=knowledge_base.status,
            source_count=source_count,
            session_count=session_count,
            created_at=_timestamp(knowledge_base.created_at),
            updated_at=_timestamp(knowledge_base.updated_at),
        )

    def list_knowledge_bases(self) -> list[KnowledgeBaseOut]:
        with session_scope(self.sessions) as session:
            knowledge_bases = session.scalars(
                select(KnowledgeBaseRecord)
                .where(KnowledgeBaseRecord.trashed_at.is_(None))
                .order_by(KnowledgeBaseRecord.updated_at.desc())
            ).all()
            return [self._knowledge_base_out(session, item) for item in knowledge_bases]

    def create_knowledge_base(self, payload: CreateKnowledgeBaseInput) -> KnowledgeBaseOut:
        with session_scope(self.sessions) as session:
            base_id = _slug(payload.title)
            if session.get(KnowledgeBaseRecord, base_id):
                base_id = f"{base_id}-{uuid4().hex[:6]}"
            knowledge_base = KnowledgeBaseRecord(
                id=base_id,
                title=payload.title.strip(),
                eyebrow=payload.eyebrow.strip(),
                subtitle=payload.subtitle.strip(),
                question=payload.question.strip(),
                description=payload.description.strip(),
                color=payload.color,
            )
            session.add(knowledge_base)
            session.flush()
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="knowledge_base",
                    target_id=knowledge_base.id,
                    action="created",
                    actor="user",
                    reason="Created from the library",
                )
            )
            return self._knowledge_base_out(session, knowledge_base)

    def update_knowledge_base(
        self, knowledge_base_id: str, payload: UpdateKnowledgeBaseInput
    ) -> KnowledgeBaseOut:
        with session_scope(self.sessions) as session:
            knowledge_base = self._live_knowledge_base(session, knowledge_base_id)
            if knowledge_base is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            changed: list[str] = []
            for field in (
                "title",
                "eyebrow",
                "subtitle",
                "question",
                "description",
                "color",
            ):
                value = getattr(payload, field)
                if value is None:
                    continue
                normalized = value.strip() if isinstance(value, str) else value
                if getattr(knowledge_base, field) != normalized:
                    setattr(knowledge_base, field, normalized)
                    changed.append(field)
            if changed:
                knowledge_base.updated_at = utc_now()
                session.add(
                    Revision(
                        id=_id("rev"),
                        target_type="knowledge_base",
                        target_id=knowledge_base.id,
                        action="metadata_updated",
                        actor="user",
                        reason=f"Changed: {', '.join(changed)}",
                    )
                )
            session.flush()
            return self._knowledge_base_out(session, knowledge_base)

    @staticmethod
    def _inbox_preview(content: str, limit: int = 240) -> str:
        preview = re.sub(r"\s+", " ", content).strip()
        return preview[: limit - 1] + "…" if len(preview) > limit else preview

    @classmethod
    def _table_preview(cls, content: str) -> str | None:
        """Summarise pasted rows by shape; collapsing whitespace would merge their cells."""

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        for delimiter in ("\t", "|", ";", ","):
            cells = [cell.strip() for cell in lines[0].strip("|").split(delimiter)]
            if len(cells) >= 2:
                break
        else:
            return None
        rows = sum(1 for line in lines[1:] if not re.fullmatch(r"[\s|:\-]+", line))
        columns = ", ".join(cell for cell in cells if cell)
        return cls._inbox_preview(f"{rows} {'row' if rows == 1 else 'rows'} · Columns: {columns}")

    def list_inbox(
        self,
        state: str | None = None,
        item_type: str | None = None,
    ) -> list[InboxItemOut]:
        """Build one review queue from the existing durable domain records.

        The inbox is deliberately a read model rather than another table: capture records
        remain the source of truth, and an item disappears as soon as its underlying state
        no longer needs filing or review.
        """

        with session_scope(self.sessions) as session:
            knowledge_bases = {
                knowledge_base.id: InboxKnowledgeBaseRefOut(
                    id=knowledge_base.id,
                    title=knowledge_base.title,
                )
                for knowledge_base in session.scalars(
                    select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.trashed_at.is_(None))
                ).all()
            }

            # Filed means filed in a library that is not in Trash.
            unfiled_sources = session.scalars(
                select(Source).where(
                    Source.id.not_in(live_membership_source_ids()),
                    Source.trashed_at.is_(None),
                )
            ).all()
            review_sources = session.scalars(
                select(Source)
                .join(Assertion, Assertion.source_id == Source.id)
                .where(
                    Source.id.in_(live_membership_source_ids()),
                    Source.trashed_at.is_(None),
                    Assertion.status == "provisional",
                )
                .distinct()
            ).all()

            source_ids = {source.id for source in (*unfiled_sources, *review_sources)}
            provisional_counts: dict[str, int] = {}
            source_knowledge_bases: dict[str, list[InboxKnowledgeBaseRefOut]] = {
                source_id: [] for source_id in source_ids
            }
            if source_ids:
                provisional_counts = {
                    source_id: count
                    for source_id, count in session.execute(
                        select(Assertion.source_id, func.count(Assertion.id))
                        .where(
                            Assertion.source_id.in_(source_ids),
                            Assertion.status == "provisional",
                        )
                        .group_by(Assertion.source_id)
                    ).all()
                }
                for source_id, knowledge_base_id in session.execute(
                    select(
                        KnowledgeBaseSource.source_id,
                        KnowledgeBaseSource.knowledge_base_id,
                    )
                    .where(KnowledgeBaseSource.source_id.in_(source_ids))
                    .order_by(KnowledgeBaseSource.created_at.asc())
                ).all():
                    knowledge_base = knowledge_bases.get(knowledge_base_id)
                    if knowledge_base:
                        source_knowledge_bases[source_id].append(knowledge_base)

            items: list[InboxItemOut] = []
            for source in unfiled_sources:
                timestamp = _timestamp(source.created_at)
                items.append(
                    InboxItemOut(
                        id=source.id,
                        item_type="source",
                        state="unfiled",
                        title=source.title,
                        preview=(source.kind == "table" and self._table_preview(source.content))
                        or self._inbox_preview(source.content),
                        source_kind=source.kind,
                        source_id=source.id,
                        assertion_count=provisional_counts.get(source.id, 0),
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )

            for source in review_sources:
                timestamp = _timestamp(source.created_at)
                items.append(
                    InboxItemOut(
                        id=source.id,
                        item_type="source",
                        state="needs_review",
                        title=source.title,
                        preview=(source.kind == "table" and self._table_preview(source.content))
                        or self._inbox_preview(source.content),
                        source_kind=source.kind,
                        knowledge_bases=source_knowledge_bases[source.id],
                        source_id=source.id,
                        assertion_count=provisional_counts.get(source.id, 0),
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )

            notes = session.scalars(
                select(NotebookNote).where(
                    NotebookNote.status == "inbox", NotebookNote.trashed_at.is_(None)
                )
            ).all()
            for note in notes:
                note_knowledge_bases = []
                if note.knowledge_base_id and note.knowledge_base_id in knowledge_bases:
                    note_knowledge_bases.append(knowledge_bases[note.knowledge_base_id])
                items.append(
                    InboxItemOut(
                        id=note.id,
                        item_type="quick_note",
                        state="unfiled",
                        title=note.title,
                        preview=self._inbox_preview(note.content),
                        source_kind="note",
                        knowledge_bases=note_knowledge_bases,
                        note_id=note.id,
                        created_at=_timestamp(note.created_at),
                        updated_at=_timestamp(note.updated_at),
                    )
                )

            if state:
                items = [item for item in items if item.state == state]
            if item_type:
                items = [item for item in items if item.item_type == item_type]
            return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def list_knowledge_base_sources(self, knowledge_base_id: str) -> list[SourceSummaryOut]:
        with session_scope(self.sessions) as session:
            source_ids = set(
                session.scalars(
                    select(KnowledgeBaseSource.source_id).where(
                        KnowledgeBaseSource.knowledge_base_id == knowledge_base_id
                    )
                ).all()
            )
            knowledge_sessions = session.scalars(
                select(KnowledgeSession).where(
                    KnowledgeSession.knowledge_base_id == knowledge_base_id
                )
            ).all()
            for knowledge_session in knowledge_sessions:
                source_ids.update(json.loads(knowledge_session.selected_source_ids_json or "[]"))
            if not source_ids:
                return []
            sources = session.scalars(
                select(Source)
                .where(Source.id.in_(source_ids), Source.trashed_at.is_(None))
                .order_by(Source.created_at.desc())
            ).all()
            return [self._source_summary(session, source) for source in sources]

    def library_title(self, base_id: str) -> str:
        """A library's title, or its id when there is none."""

        with session_scope(self.sessions) as session:
            library = session.get(KnowledgeBaseRecord, base_id)
            return library.title if library else base_id

    @staticmethod
    def _search_tokens(query: str) -> list[str]:
        raw_tokens = list(dict.fromkeys(re.findall(r"[\w-]+", query.casefold(), re.UNICODE)))
        if len(raw_tokens) <= 1:
            return raw_tokens[:8]
        intent_words = {
            "about",
            "are",
            "compare",
            "find",
            "for",
            "have",
            "i",
            "in",
            "latest",
            "learned",
            "me",
            "my",
            "notes",
            "our",
            "search",
            "show",
            "tell",
            "the",
            "what",
            "we",
            "where",
            "with",
        }
        topical = [token for token in raw_tokens if token not in intent_words]
        return (topical or raw_tokens)[:8]

    @staticmethod
    def _search_snippet(content: str, query: str, limit: int = 220) -> str:
        from gunther.service import KnowledgeService

        compact = re.sub(r"\s+", " ", content).replace("**", "").replace("`", "").strip()
        index = compact.casefold().find(query.casefold())
        if index < 0:
            index = next(
                (
                    candidate
                    for token in KnowledgeService._search_tokens(query)
                    if (candidate := compact.casefold().find(token.casefold())) >= 0
                ),
                -1,
            )
        start = max(0, index - 70) if index >= 0 else 0
        snippet = compact[start : start + limit]
        if start:
            snippet = f"…{snippet}"
        if start + limit < len(compact):
            snippet = f"{snippet}…"
        return snippet

    def search_knowledge(
        self,
        query: str,
        limit: int = 20,
        knowledge_base_ids: list[str] | None = None,
    ) -> list[KnowledgeSearchResultOut]:
        """Search accepted knowledge, sources, notes and sessions.

        ``knowledge_base_ids`` narrows every result type to those knowledge bases;
        unfiled material (Inbox sources, unfiled notes) is then excluded.
        """
        needle = query.strip()
        tokens = self._search_tokens(needle)
        if not tokens:
            return []
        scoped = knowledge_base_ids is not None
        patterns = [
            f"%{token.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"
            for token in tokens
        ]

        def match_all(*columns):
            return and_(
                *(
                    or_(*(column.ilike(pattern, escape="\\") for column in columns))
                    for pattern in patterns
                )
            )

        with session_scope(self.sessions) as session:
            results: list[KnowledgeSearchResultOut] = []
            units = (
                session.scalars(
                    select(KnowledgeUnit)
                    .options(selectinload(KnowledgeUnit.revisions))
                    .join(KnowledgeUnitRevision)
                    .where(match_all(KnowledgeUnit.title, KnowledgeUnitRevision.content))
                    .where(KnowledgeUnit.status != "deprecated")
                    .where(KnowledgeUnit.knowledge_base_id.not_in(trashed_library_ids()))
                    .where(
                        KnowledgeUnit.knowledge_base_id.in_(knowledge_base_ids)
                        if scoped
                        else true()
                    )
                    .order_by(KnowledgeUnit.updated_at.desc())
                    .limit(limit)
                )
                .unique()
                .all()
            )
            for unit in units:
                detail = self._knowledge_unit_out(session, unit)
                results.append(
                    KnowledgeSearchResultOut(
                        id=unit.id,
                        knowledge_base_id=unit.knowledge_base_id,
                        kind="knowledge_unit",
                        title=unit.title,
                        snippet=self._search_snippet(detail.content, needle),
                        meta=f"{unit.status} · {_count_label(detail.revision_count, 'revision')}",
                        updated_at=_timestamp(unit.updated_at),
                        source_session_id=detail.source_session_id,
                        source_message_id=detail.source_message_id,
                    )
                )

            # Unfiled captures still wait in Inbox, but they are the user's material and
            # must be findable; they come back without a library (knowledge_base_id=None).
            source_query = select(Source, KnowledgeBaseSource.knowledge_base_id)
            source_query = (
                source_query.join(
                    KnowledgeBaseSource,
                    and_(
                        KnowledgeBaseSource.source_id == Source.id,
                        KnowledgeBaseSource.knowledge_base_id.in_(knowledge_base_ids),
                        KnowledgeBaseSource.knowledge_base_id.not_in(trashed_library_ids()),
                    ),
                )
                if scoped
                else source_query.outerjoin(
                    KnowledgeBaseSource,
                    and_(
                        KnowledgeBaseSource.source_id == Source.id,
                        KnowledgeBaseSource.knowledge_base_id.not_in(trashed_library_ids()),
                    ),
                )
            )
            source_rows = session.execute(
                source_query.where(match_all(Source.title, Source.content))
                .where(Source.trashed_at.is_(None))
                .order_by(Source.created_at.desc())
                .limit(limit)
            ).all()
            for source, knowledge_base_id in source_rows:
                summary = self._source_summary(session, source)
                results.append(
                    KnowledgeSearchResultOut(
                        id=source.id,
                        knowledge_base_id=knowledge_base_id,
                        kind="source",
                        title=source.title,
                        snippet=self._search_snippet(source.content, needle),
                        meta=(
                            f"{_count_label(summary.assertion_count, 'claim')} · "
                            f"{_count_label(summary.entity_count, 'entity', 'entities')}"
                        ),
                        updated_at=_timestamp(source.created_at),
                    )
                )

            # Passages found by meaning (and by several words at once): a sentence such as
            # "which marker identifies T cells?" rarely has every word in one note.
            meaning = self._sources_by_meaning(
                session, needle, knowledge_base_ids, {item.id for item in results}
            )

            notebook_notes = session.scalars(
                select(NotebookNote)
                .where(
                    match_all(NotebookNote.title, NotebookNote.content),
                    NotebookNote.status != "archived",
                    NotebookNote.trashed_at.is_(None),
                    or_(
                        NotebookNote.knowledge_base_id.is_(None),
                        NotebookNote.knowledge_base_id.not_in(trashed_library_ids()),
                    ),
                    NotebookNote.knowledge_base_id.in_(knowledge_base_ids) if scoped else true(),
                )
                .order_by(NotebookNote.updated_at.desc())
                .limit(limit)
            ).all()
            for note in notebook_notes:
                results.append(
                    KnowledgeSearchResultOut(
                        id=note.id,
                        knowledge_base_id=note.knowledge_base_id,
                        kind="note",
                        title=note.title,
                        snippet=self._search_snippet(note.content or note.title, needle),
                        meta="Notebook · " + ("filed" if note.status == "filed" else "unfiled"),
                        updated_at=_timestamp(note.updated_at),
                    )
                )

            knowledge_sessions = session.scalars(
                select(KnowledgeSession)
                .where(
                    match_all(KnowledgeSession.title, KnowledgeSession.summary),
                    KnowledgeSession.archived.is_(False),
                    KnowledgeSession.knowledge_base_id != HOME_SCOPE,
                    KnowledgeSession.knowledge_base_id.not_in(trashed_library_ids()),
                    KnowledgeSession.knowledge_base_id.in_(knowledge_base_ids)
                    if scoped
                    else true(),
                )
                .order_by(KnowledgeSession.updated_at.desc())
                .limit(limit)
            ).all()
            for knowledge_session in knowledge_sessions:
                results.append(
                    KnowledgeSearchResultOut(
                        id=knowledge_session.id,
                        knowledge_base_id=knowledge_session.knowledge_base_id,
                        kind="session",
                        title=knowledge_session.title,
                        snippet=self._search_snippet(
                            knowledge_session.summary or knowledge_session.title,
                            needle,
                        ),
                        meta="Conversation session",
                        updated_at=_timestamp(knowledge_session.updated_at),
                    )
                )

            ranked = sorted(
                results,
                key=lambda item: (
                    needle.casefold() in item.title.casefold(),
                    sum(
                        token.casefold() in f"{item.title} {item.snippet}".casefold()
                        for token in tokens
                    ),
                    item.updated_at,
                ),
                reverse=True,
            )[:limit]
            # Exact matches first, then up to a few more the words alone did not find.
            return [*ranked, *meaning[:MEANING_RESULTS]]

    def _sources_by_meaning(
        self,
        session: Session,
        query: str,
        knowledge_base_ids: list[str] | None,
        already: set[str],
    ) -> list[KnowledgeSearchResultOut]:
        """Sources whose passages match the query by meaning or by most of its words.

        Uses the retrieval Ask uses: keyword and, when Search by meaning is on,
        vector matches together. The best passage of each source is its snippet.
        """

        source_ids = self._home_source_ids(session, [], knowledge_base_ids)
        found: list[KnowledgeSearchResultOut] = []
        for hit in self.index.retrieve(session, source_ids, query, limit=MEANING_RESULTS * 3):
            source = hit.source
            if source.id in already or source.trashed_at is not None:
                continue
            already.add(source.id)
            library_id = session.scalar(
                select(KnowledgeBaseSource.knowledge_base_id)
                .join(
                    KnowledgeBaseRecord,
                    KnowledgeBaseRecord.id == KnowledgeBaseSource.knowledge_base_id,
                )
                .where(
                    KnowledgeBaseSource.source_id == source.id,
                    KnowledgeBaseRecord.trashed_at.is_(None),
                )
                .limit(1)
            )
            summary = self._source_summary(session, source)
            found.append(
                KnowledgeSearchResultOut(
                    id=source.id,
                    knowledge_base_id=library_id,
                    kind="source",
                    title=source.title,
                    snippet=self._search_snippet(hit.block.content, query),
                    meta=(
                        f"{_count_label(summary.assertion_count, 'claim')} · related by "
                        + ("meaning" if hit.method != "keyword" else "its words")
                    ),
                    updated_at=_timestamp(source.created_at),
                )
            )
        return found

    def get_graph(self) -> KnowledgeGraphOut:
        with session_scope(self.sessions) as session:
            assertions = session.scalars(self._live_assertion_query()).all()
            counts: Counter[str] = Counter()
            entities: dict[str, Entity] = {}
            for assertion in assertions:
                entities[assertion.subject.id] = assertion.subject
                entities[assertion.object.id] = assertion.object
                counts[assertion.subject.id] += 1
                counts[assertion.object.id] += 1

            return KnowledgeGraphOut(
                nodes=[
                    GraphNodeOut(
                        id=entity.id,
                        label=entity.label,
                        type=entity.type,
                        assertion_count=counts[entity.id],
                    )
                    for entity in entities.values()
                ],
                edges=[
                    GraphEdgeOut(
                        id=assertion.id,
                        source=assertion.subject.id,
                        target=assertion.object.id,
                        label=assertion.predicate,
                        status=assertion.status,
                        confidence=assertion.confidence,
                    )
                    for assertion in assertions
                ],
            )

    def overview(self) -> OverviewOut:
        live_claim = Assertion.source_id.not_in(
            select(Source.id).where(Source.trashed_at.is_not(None))
        )
        with session_scope(self.sessions) as session:
            counts = OverviewCountsOut(
                sources=session.scalar(
                    select(func.count()).select_from(Source).where(Source.trashed_at.is_(None))
                )
                or 0,
                entities=session.scalar(select(func.count()).select_from(Entity)) or 0,
                assertions=session.scalar(
                    select(func.count()).select_from(Assertion).where(live_claim)
                )
                or 0,
                provisional=session.scalar(
                    select(func.count())
                    .select_from(Assertion)
                    .where(live_claim, Assertion.status == "provisional")
                )
                or 0,
            )
            sources = session.scalars(
                select(Source)
                .where(Source.trashed_at.is_(None))
                .order_by(Source.created_at.desc())
                .limit(4)
            ).all()
            assertions = session.scalars(
                self._live_assertion_query().order_by(Assertion.created_at.desc()).limit(5)
            ).all()
            return OverviewOut(
                counts=counts,
                recent_sources=[self._source_summary(session, source) for source in sources],
                recent_assertions=[self._assertion_out(assertion) for assertion in assertions],
            )
