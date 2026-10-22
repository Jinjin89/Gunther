"""Editable topic trees, separate from immutable document structure.

The topic tree is also a library's arranged contents: topics are the user's sections,
and each source of a library is placed in at most one section (or in none, "not placed
yet"). Sections and sources share one order inside their parent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlparse

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from gunther.database import session_scope
from gunther.knowledge_index import new_id
from gunther.models import (
    Asset,
    ContentBlock,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeSession,
    ProcessingJob,
    Source,
    SourceIndexHead,
    SourcePaper,
    SourceRevision,
    TopicEvidenceLink,
    TopicNode,
    TopicSourceLink,
    WebSnapshot,
)
from gunther.recording_metadata import recording_timing


class TopicConflict(ValueError):
    pass


def subtree_ids(session: Session, base_id: str, topic_id: str) -> set[str]:
    topic = session.get(TopicNode, topic_id)
    if not topic or topic.knowledge_base_id != base_id:
        raise LookupError("Topic was not found in this knowledge base")
    nodes = list(session.scalars(select(TopicNode).where(TopicNode.knowledge_base_id == base_id)))
    selected = {topic_id}
    for _ in range(len(nodes)):
        added = {node.id for node in nodes if node.parent_id in selected} - selected
        if not added:
            break
        selected.update(added)
    return selected


def topic_source_ids(session: Session, base_id: str, topic_id: str) -> list[str]:
    """Live sources filed under a topic or any topic inside it."""

    selected = subtree_ids(session, base_id, topic_id)
    return list(
        session.scalars(
            select(TopicSourceLink.source_id)
            .join(Source, Source.id == TopicSourceLink.source_id)
            .where(TopicSourceLink.topic_id.in_(selected), Source.trashed_at.is_(None))
            .distinct()
        )
    )


def topic_block_ids(session: Session, base_id: str, topic_id: str) -> list[str]:
    selected = subtree_ids(session, base_id, topic_id)
    return list(
        session.scalars(
            select(TopicEvidenceLink.block_id).where(TopicEvidenceLink.topic_id.in_(selected))
        )
    )


class TopicService:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @staticmethod
    def out(session: Session, node: TopicNode) -> dict[str, object]:
        return {
            "id": node.id,
            "knowledgeBaseId": node.knowledge_base_id,
            "parentId": node.parent_id,
            "title": node.title,
            "description": node.description,
            "position": node.position,
            "version": node.version,
            "blockIds": list(
                session.scalars(
                    select(TopicEvidenceLink.block_id).where(TopicEvidenceLink.topic_id == node.id)
                )
            ),
            "sourceIds": list(
                session.scalars(
                    select(TopicSourceLink.source_id)
                    .join(Source, Source.id == TopicSourceLink.source_id)
                    .where(TopicSourceLink.topic_id == node.id, Source.trashed_at.is_(None))
                    .order_by(
                        TopicSourceLink.position, TopicSourceLink.created_at, TopicSourceLink.id
                    )
                )
            ),
        }

    def list(self, base_id: str) -> list[dict[str, object]]:
        with session_scope(self.sessions) as session:
            library = session.get(KnowledgeBaseRecord, base_id)
            if library is None or library.trashed_at is not None:
                raise LookupError("Knowledge base was not found")
            return [
                self.out(session, node)
                for node in session.scalars(
                    select(TopicNode)
                    .where(TopicNode.knowledge_base_id == base_id)
                    .order_by(TopicNode.position, TopicNode.created_at, TopicNode.id)
                )
            ]

    def save(
        self,
        base_id: str,
        values: dict[str, object],
        node_id: str | None = None,
        source_ids: list[str] | None = None,
    ) -> dict[str, object]:
        with session_scope(self.sessions) as session:
            # Serialize tree edits before checking ancestry to prevent concurrent
            # A->B / B->A changes from passing independent cycle checks.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            library = session.get(KnowledgeBaseRecord, base_id)
            if library is None or library.trashed_at is not None:
                raise LookupError("Knowledge base was not found")
            node = session.get(TopicNode, node_id) if node_id else None
            if node_id and (not node or node.knowledge_base_id != base_id):
                raise LookupError("Topic was not found")
            if node and values.get("version") != node.version:
                raise TopicConflict("This topic changed. Reload before saving.")
            parent_id = values.get("parent_id", node.parent_id if node else None)
            cursor, seen = parent_id, set()
            while cursor:
                if cursor == node_id or cursor in seen:
                    raise ValueError("A topic cannot contain itself or its ancestors")
                seen.add(cursor)
                if len(seen) > 32:
                    raise ValueError("Topic hierarchy exceeds 32 levels")
                parent = session.get(TopicNode, cursor)
                if not parent or parent.knowledge_base_id != base_id:
                    raise ValueError("Parent topic must belong to this knowledge base")
                cursor = parent.parent_id
            old_parent_id = node.parent_id if node else None
            if node is None:
                node = TopicNode(
                    id=new_id("topic"),
                    knowledge_base_id=base_id,
                    title=str(values["title"]).strip(),
                )
                session.add(node)
                arrange = True
            else:
                node.version += 1
                arrange = parent_id != old_parent_id or "position" in values
            for field in ("title", "description", "position"):
                if field in values:
                    value = values[field]
                    setattr(node, field, value.strip() if isinstance(value, str) else value)
            node.parent_id = parent_id
            session.flush()
            if arrange:
                # Without an explicit position the section goes to the end of its parent.
                if parent_id != old_parent_id:
                    self._renumber(session, base_id, old_parent_id)
                self._renumber(
                    session,
                    base_id,
                    parent_id,
                    put=[node],
                    index=int(values["position"]) if "position" in values else None,
                )
            if source_ids:
                self._file(session, base_id, node, source_ids)
            return self.out(session, node)

    @classmethod
    def _file(cls, session: Session, base_id: str, node: TopicNode, source_ids: list[str]) -> None:
        """Place whole sources in a section, at its end. A source already placed elsewhere
        in this library is moved: it has one place only."""

        wanted = list(dict.fromkeys(source_ids))
        members = set(
            session.scalars(
                select(KnowledgeBaseSource.source_id)
                .join(Source, Source.id == KnowledgeBaseSource.source_id)
                .where(
                    KnowledgeBaseSource.knowledge_base_id == base_id,
                    KnowledgeBaseSource.source_id.in_(wanted),
                    Source.trashed_at.is_(None),
                )
            )
        )
        if set(wanted) - members:
            raise ValueError("Every source must belong to this knowledge base")
        placed = {
            link.source_id: link
            for link in session.scalars(
                select(TopicSourceLink).where(
                    TopicSourceLink.knowledge_base_id == base_id,
                    TopicSourceLink.source_id.in_(wanted),
                )
            )
        }
        left: set[str] = set()
        for link in placed.values():
            if link.topic_id != node.id:
                left.add(link.topic_id)
                session.delete(link)
        session.flush()
        added = [
            TopicSourceLink(
                id=new_id("tsl"),
                topic_id=node.id,
                source_id=source_id,
                knowledge_base_id=base_id,
            )
            for source_id in wanted
            if source_id not in placed or placed[source_id].topic_id != node.id
        ]
        session.add_all(added)
        session.flush()
        for topic_id in sorted(left):
            cls._renumber(session, base_id, topic_id)
        if added:
            cls._renumber(session, base_id, node.id, put=added)

    def file_sources(
        self, base_id: str, topic_id: str, source_ids: list[str]
    ) -> dict[str, object]:
        """File whole sources under a topic."""

        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            topic = session.get(TopicNode, topic_id)
            if not topic or topic.knowledge_base_id != base_id:
                raise LookupError("Topic was not found")
            self._file(session, base_id, topic, source_ids)
            topic.version += 1
            session.flush()
            return self.out(session, topic)

    def unfile_source(self, base_id: str, topic_id: str, source_id: str) -> dict[str, object]:
        with session_scope(self.sessions) as session:
            topic = session.get(TopicNode, topic_id)
            if not topic or topic.knowledge_base_id != base_id:
                raise LookupError("Topic was not found")
            link = session.scalar(
                select(TopicSourceLink).where(
                    TopicSourceLink.topic_id == topic_id, TopicSourceLink.source_id == source_id
                )
            )
            if link is not None:
                session.delete(link)
                topic.version += 1
                session.flush()
                self._renumber(session, topic.knowledge_base_id, topic_id)
            session.flush()
            return self.out(session, topic)

    def link(self, base_id: str, topic_id: str, block_id: str) -> dict[str, object]:
        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            topic = session.get(TopicNode, topic_id)
            if not topic or topic.knowledge_base_id != base_id:
                raise LookupError("Topic was not found")
            block = session.get(ContentBlock, block_id)
            revision = session.get(SourceRevision, block.revision_id) if block else None
            allowed = revision and session.scalar(
                select(KnowledgeBaseSource.id).where(
                    KnowledgeBaseSource.source_id == revision.source_id,
                    KnowledgeBaseSource.knowledge_base_id == base_id,
                )
            )
            if not allowed:
                raise ValueError("Evidence must belong to this knowledge base")
            existing = session.scalar(
                select(TopicEvidenceLink).where(
                    TopicEvidenceLink.topic_id == topic_id,
                    TopicEvidenceLink.block_id == block_id,
                )
            )
            if not existing:
                session.add(
                    TopicEvidenceLink(id=new_id("tle"), topic_id=topic_id, block_id=block_id)
                )
                session.execute(
                    update(TopicNode)
                    .where(TopicNode.id == topic_id)
                    .values(version=TopicNode.version + 1)
                )
            session.flush()
            session.refresh(topic)
            return self.out(session, topic)

    # Arranged contents -------------------------------------------------------

    @staticmethod
    def _children(
        session: Session, base_id: str, parent_id: str | None
    ) -> list[TopicNode | TopicSourceLink]:
        """What sits directly under a section (or at the top): its sub-sections and, for a
        section, its sources, in the user's order."""

        sections = select(TopicNode).where(TopicNode.knowledge_base_id == base_id)
        sections = sections.where(
            TopicNode.parent_id.is_(None) if parent_id is None else TopicNode.parent_id == parent_id
        )
        items: list[TopicNode | TopicSourceLink] = list(session.scalars(sections))
        if parent_id is not None:
            items += session.scalars(
                select(TopicSourceLink).where(TopicSourceLink.topic_id == parent_id)
            )
        items.sort(
            key=lambda item: (
                item.position,
                isinstance(item, TopicSourceLink),
                item.created_at,
                item.id,
            )
        )
        return items

    @classmethod
    def _renumber(
        cls,
        session: Session,
        base_id: str,
        parent_id: str | None,
        put: Sequence[TopicNode | TopicSourceLink] = (),
        index: int | None = None,
    ) -> None:
        """Number a parent's children 0…n−1. ``put`` are placed first among them, starting
        at ``index`` counted over the children the user can see (None: at the end);
        sources in Trash keep their place but are not counted."""

        moving = {item.id for item in put}
        children = cls._children(session, base_id, parent_id)
        rest = [item for item in children if item.id not in moving]
        if put:
            hidden: set[str] = set()
            if parent_id is not None:
                hidden = set(
                    session.scalars(
                        select(TopicSourceLink.id)
                        .join(Source, Source.id == TopicSourceLink.source_id)
                        .where(
                            TopicSourceLink.topic_id == parent_id,
                            Source.trashed_at.is_not(None),
                        )
                    )
                )
            visible = [item for item in rest if item.id not in hidden]
            at = (
                len(rest)
                if index is None or index >= len(visible)
                else rest.index(visible[max(index, 0)])
            )
            rest[at:at] = list(put)
        for number, item in enumerate(rest):
            if item.position != number:
                item.position = number
        session.flush()

    def contents(self, base_id: str) -> dict[str, object]:
        """Every section, every placement and every live source of a library."""

        with session_scope(self.sessions) as session:
            return self._contents(session, base_id)

    @classmethod
    def _contents(cls, session: Session, base_id: str) -> dict[str, object]:
        library = session.get(KnowledgeBaseRecord, base_id)
        if library is None or library.trashed_at is not None:
            raise LookupError("Knowledge base was not found")
        passages = dict(
            session.execute(
                select(TopicEvidenceLink.topic_id, func.count())
                .join(TopicNode, TopicNode.id == TopicEvidenceLink.topic_id)
                .where(TopicNode.knowledge_base_id == base_id)
                .group_by(TopicEvidenceLink.topic_id)
            ).all()
        )
        sections = [
            {
                "id": node.id,
                "parentId": node.parent_id,
                "title": node.title,
                "description": node.description,
                "position": node.position,
                "version": node.version,
                "passageCount": passages.get(node.id, 0),
            }
            for node in session.scalars(
                select(TopicNode)
                .where(TopicNode.knowledge_base_id == base_id)
                .order_by(TopicNode.position, TopicNode.created_at, TopicNode.id)
            )
        ]
        placements = [
            {"sourceId": source_id, "sectionId": topic_id, "position": position}
            for source_id, topic_id, position in session.execute(
                select(
                    TopicSourceLink.source_id, TopicSourceLink.topic_id, TopicSourceLink.position
                )
                .join(Source, Source.id == TopicSourceLink.source_id)
                .where(TopicSourceLink.knowledge_base_id == base_id, Source.trashed_at.is_(None))
                .order_by(TopicSourceLink.topic_id, TopicSourceLink.position, TopicSourceLink.id)
            )
        ]
        return {
            "sections": sections,
            "placements": placements,
            "sources": cls._source_rows(session, base_id),
        }

    @staticmethod
    def _source_rows(session: Session, base_id: str) -> list[dict[str, object]]:
        """One row of details per live source, from a handful of queries for the whole
        library (never one per source, and never a source's whole text)."""

        member = KnowledgeBaseSource.knowledge_base_id == base_id
        rows = session.execute(
            select(
                Source.id,
                Source.title,
                Source.kind,
                Source.created_at,
                Asset.media_type,
            )
            .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
            .outerjoin(Asset, Asset.id == Source.asset_id)
            .where(member, Source.trashed_at.is_(None))
            .order_by(Source.created_at.desc(), Source.id)
        ).all()

        # Only the head of a recording's text holds its "Duration:" line.
        durations: dict[str, int] = {}
        duration_pending: dict[str, bool] = {}
        for source_id, head in session.execute(
            select(Source.id, func.substr(Source.content, 1, 2000))
            .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
            .where(member, Source.trashed_at.is_(None), Source.kind.in_(("recording", "course")))
        ):
            seconds, pending = recording_timing(session, head or "")
            duration_pending[source_id] = pending
            if seconds is None:
                seconds = _duration_seconds(head or "")
            if seconds is not None:
                durations[source_id] = seconds

        papers = {
            source_id: (authors, year)
            for source_id, authors, year in session.execute(
                select(SourcePaper.source_id, SourcePaper.authors, SourcePaper.year)
                .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == SourcePaper.source_id)
                .where(member)
            )
        }

        # The current version's state and page count; the manifest is read inside SQLite so
        # a big manifest is never loaded.
        pages_of = case(
            (
                func.json_valid(SourceRevision.manifest_json) == 1,
                func.json_extract(SourceRevision.manifest_json, "$.pageCount"),
            ),
            else_=None,
        )
        revisions = {
            source_id: (state, pages)
            for source_id, state, pages in session.execute(
                select(SourceIndexHead.source_id, SourceRevision.state, pages_of)
                .join(SourceRevision, SourceRevision.id == SourceIndexHead.revision_id)
                .join(
                    KnowledgeBaseSource, KnowledgeBaseSource.source_id == SourceIndexHead.source_id
                )
                .where(member)
            )
        }
        latest_job: dict[str, str] = {}
        for source_id, state in session.execute(
            select(ProcessingJob.source_id, ProcessingJob.state)
            .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == ProcessingJob.source_id)
            .where(member, ProcessingJob.kind == "parse_asset")
            .order_by(ProcessingJob.created_at, ProcessingJob.id)
        ):
            latest_job[source_id] = state

        hosts: dict[str, str] = {}
        for source_id, url in session.execute(
            select(WebSnapshot.source_id, WebSnapshot.final_url)
            .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == WebSnapshot.source_id)
            .where(member)
        ):
            host = (urlparse(url or "").hostname or "").removeprefix("www.")
            if host:
                hosts[source_id] = host

        out: list[dict[str, object]] = []
        for source_id, title, kind, created_at, media_type in rows:
            authors, year = papers.get(source_id, ("", None))
            state, pages = revisions.get(source_id, (None, None))
            job = latest_job.get(source_id)
            # The same reading of "is it ready" as KnowledgeIndex.processing().
            status = job if job and job != "completed" else (state or "pending")
            out.append(
                {
                    "id": source_id,
                    "title": title,
                    "kind": kind,
                    "createdAt": created_at.isoformat(),
                    "durationSeconds": durations.get(source_id),
                    "durationPending": duration_pending.get(source_id, False),
                    "authors": _short_authors(authors),
                    "year": year,
                    "pages": pages if isinstance(pages, int) and pages > 0 else None,
                    "mediaType": media_type,
                    "host": hosts.get(source_id),
                    "processing": _processing(status),
                }
            )
        return out

    def move(
        self,
        base_id: str,
        items: Sequence[tuple[Literal["source", "section"], str]],
        parent_id: str | None,
        index: int | None,
    ) -> dict[str, object]:
        """Move sections and sources, in the given order, to be children of a section
        (or, with no section, sections to the top and sources to "not placed yet")."""

        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            library = session.get(KnowledgeBaseRecord, base_id)
            if library is None or library.trashed_at is not None:
                raise LookupError("Knowledge base was not found")
            parent = None
            if parent_id is not None:
                parent = session.get(TopicNode, parent_id)
                if parent is None:
                    raise LookupError("Section was not found")
                if parent.knowledge_base_id != base_id:
                    raise ValueError("The section must belong to this knowledge base")

            wanted = list(dict.fromkeys(items))
            moved: list[TopicNode | Source] = []
            for kind, item_id in wanted:
                if kind == "section":
                    node = session.get(TopicNode, item_id)
                    if node is None:
                        raise LookupError("Section was not found")
                    if node.knowledge_base_id != base_id:
                        raise ValueError("Every section must belong to this knowledge base")
                    moved.append(node)
                else:
                    source = session.get(Source, item_id)
                    if source is None or source.trashed_at is not None:
                        raise LookupError("Source was not found")
                    if not session.scalar(
                        select(KnowledgeBaseSource.id).where(
                            KnowledgeBaseSource.knowledge_base_id == base_id,
                            KnowledgeBaseSource.source_id == item_id,
                        )
                    ):
                        raise ValueError("Every source must belong to this knowledge base")
                    moved.append(source)

            # A section cannot go into itself or anything inside it.
            moving_sections = {item.id for item in moved if isinstance(item, TopicNode)}
            if moving_sections and parent_id is not None:
                parents = dict(
                    session.execute(
                        select(TopicNode.id, TopicNode.parent_id).where(
                            TopicNode.knowledge_base_id == base_id
                        )
                    ).all()
                )
                cursor, steps = parent_id, 0
                while cursor is not None and steps <= len(parents):
                    if cursor in moving_sections:
                        raise ValueError("A section cannot go into itself or its sub-sections")
                    cursor = parents.get(cursor)
                    steps += 1

            touched: set[str | None] = set()
            put: list[TopicNode | TopicSourceLink] = []
            for item in moved:
                if isinstance(item, TopicNode):
                    touched.add(item.parent_id)
                    item.parent_id = parent_id
                    item.version += 1
                    put.append(item)
                    continue
                link = session.scalar(
                    select(TopicSourceLink).where(
                        TopicSourceLink.knowledge_base_id == base_id,
                        TopicSourceLink.source_id == item.id,
                    )
                )
                if link is not None:
                    touched.add(link.topic_id)
                if parent_id is None:
                    if link is not None:
                        session.delete(link)
                elif link is not None:
                    link.topic_id = parent_id
                    put.append(link)
                else:
                    link = TopicSourceLink(
                        id=new_id("tsl"),
                        topic_id=parent_id,
                        source_id=item.id,
                        knowledge_base_id=base_id,
                    )
                    session.add(link)
                    put.append(link)
            session.flush()
            for old in sorted(touched - {parent_id}, key=lambda value: value or ""):
                self._renumber(session, base_id, old)
            if put or parent_id in touched:
                self._renumber(session, base_id, parent_id, put=put, index=index)
            return self._contents(session, base_id)

    def order(
        self,
        base_id: str,
        parent_id: str | None,
        children: Sequence[tuple[Literal["source", "section"], str]],
    ) -> dict[str, object]:
        """Set the order of exactly the children a section (or the top) has now."""

        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            library = session.get(KnowledgeBaseRecord, base_id)
            if library is None or library.trashed_at is not None:
                raise LookupError("Knowledge base was not found")
            if parent_id is not None:
                parent = session.get(TopicNode, parent_id)
                if parent is None or parent.knowledge_base_id != base_id:
                    raise LookupError("Section was not found")
            current = self._children(session, base_id, parent_id)
            trashed = set(
                session.scalars(
                    select(TopicSourceLink.id)
                    .join(Source, Source.id == TopicSourceLink.source_id)
                    .where(
                        TopicSourceLink.topic_id == parent_id,
                        Source.trashed_at.is_not(None),
                    )
                )
            ) if parent_id is not None else set()
            by_key: dict[tuple[str, str], TopicNode | TopicSourceLink] = {}
            for item in current:
                if item.id in trashed:
                    continue
                if isinstance(item, TopicNode):
                    by_key[("section", item.id)] = item
                else:
                    by_key[("source", item.source_id)] = item
            if len(children) != len(set(children)) or set(children) != set(by_key):
                raise TopicConflict("The contents changed. Reload and try again.")
            ordered = [by_key[key] for key in children]
            ordered += [item for item in current if item.id in trashed]
            for number, item in enumerate(ordered):
                item.position = number
            session.flush()
            return self._contents(session, base_id)

    def delete(self, base_id: str, topic_id: str) -> dict[str, object]:
        """Delete a section and everything inside it. No source is deleted: they go back
        to "not placed yet". Written overviews and passage links of those sections go with
        them, and conversations focused on one fall back to the whole library."""

        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            library = session.get(KnowledgeBaseRecord, base_id)
            if library is None or library.trashed_at is not None:
                raise LookupError("Knowledge base was not found")
            doomed = subtree_ids(session, base_id, topic_id)
            nodes = {
                node.id: node
                for node in session.scalars(
                    select(TopicNode).where(TopicNode.knowledge_base_id == base_id)
                )
            }
            parent_id = nodes[topic_id].parent_id
            session.execute(
                update(KnowledgeSession)
                .where(
                    KnowledgeSession.knowledge_base_id == base_id,
                    KnowledgeSession.focus_chapter_id.in_(doomed),
                )
                .values(focus_chapter_id=None)
            )

            def depth(node_id: str) -> int:
                steps, cursor = 0, nodes[node_id].parent_id
                while cursor is not None and steps < len(nodes):
                    steps, cursor = steps + 1, nodes[cursor].parent_id
                return steps

            # parent_id restricts deletion, so the deepest sections go first. Their
            # placements, overviews and passage links go with them (cascade).
            for node_id in sorted(doomed, key=depth, reverse=True):
                session.execute(delete(TopicNode).where(TopicNode.id == node_id))
            session.expire_all()
            self._renumber(session, base_id, parent_id)
            return self._contents(session, base_id)


_DURATION = re.compile(r"^Duration:[ \t]*(?:(\d+(?::\d+){1,2})|(\d+)[ \t]*seconds?)", re.MULTILINE)


def _duration_seconds(head: str) -> int | None:
    """Seconds from a recording's "Duration: 1:02:03" (or "Duration: 90 seconds") line."""

    found = _DURATION.search(head)
    if not found:
        return None
    if found.group(2):
        return int(found.group(2))
    total = 0
    for part in found.group(1).split(":"):
        total = total * 60 + int(part)
    return total


def _short_authors(authors: str | None) -> str | None:
    """The first author, with "et al." when there are several."""

    names = [name.strip() for name in (authors or "").split(",") if name.strip()]
    if not names:
        return None
    return names[0] if len(names) == 1 else f"{names[0]} et al."


def _processing(state: str) -> str:
    if state in {"queued", "running", "pending", "retry"}:
        return "reading"
    if state in {"failed", "dead", "error"}:
        return "failed"
    return "ready"
