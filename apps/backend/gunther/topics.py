"""Editable topic trees, separate from immutable document structure."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from gunther.database import session_scope
from gunther.knowledge_index import new_id
from gunther.models import (
    ContentBlock,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    SourceRevision,
    TopicEvidenceLink,
    TopicNode,
)


class TopicConflict(ValueError):
    pass


def topic_block_ids(session: Session, base_id: str, topic_id: str) -> list[str]:
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
            if node is None:
                node = TopicNode(
                    id=new_id("topic"),
                    knowledge_base_id=base_id,
                    title=str(values["title"]).strip(),
                )
                session.add(node)
            else:
                node.version += 1
            for field in ("title", "description", "position"):
                if field in values:
                    value = values[field]
                    setattr(node, field, value.strip() if isinstance(value, str) else value)
            node.parent_id = parent_id
            session.flush()
            return self.out(session, node)

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
