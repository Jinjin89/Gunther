"""Transactional structured evidence and scoped hybrid retrieval.

FTS is the always-available baseline. Semantic retrieval adds a local model
(see embedding) and vectors in sqlite-vec (see vector_index); when either is
unavailable, retrieval falls back to keywords and never blocks originals.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from gunther import vector_index
from gunther.content import MIN_EMBED_CHARS, ParsedBlock, parse_content, search_tokens
from gunther.database import session_scope
from gunther.models import (
    ContentBlock,
    ProcessingJob,
    Source,
    SourceIndexHead,
    SourcePaper,
    SourceRevision,
)

# Parser versions are part of a revision's fingerprint. v2 reflows PDF lines into
# paragraphs and makes passage-sized blocks; v1 revisions are parsed again.
TEXT_PARSER = "gunther-text-v2"
ASSET_PARSER = "gunther-asset-v2"
LEGACY_ASSET_PARSER = "gunther-asset-v1"


def embeddable() -> list[Any]:
    """SQL conditions for passages that get a vector (see content.passage_flags)."""

    return [
        ContentBlock.kind != "heading",
        func.length(ContentBlock.content) >= MIN_EMBED_CHARS,
        func.coalesce(func.json_extract(ContentBlock.payload_json, "$.noEmbed"), 0) == 0,
    ]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Embedder(Protocol):
    model_id: str

    def encode(self, texts: list[str], *, query: bool = False) -> list[list[float]]: ...


@dataclass
class RetrievalHit:
    block: ContentBlock
    source: Source
    score: float
    method: str


def enqueue(
    session: Session,
    source_id: str,
    kind: str,
    key: str,
    *,
    revision_id: str | None = None,
    model_id: str | None = None,
) -> ProcessingJob:
    existing = session.scalar(select(ProcessingJob).where(ProcessingJob.dedupe_key == key))
    if existing:
        return existing
    job = ProcessingJob(
        id=new_id("job"),
        source_id=source_id,
        kind=kind,
        dedupe_key=key,
        revision_id=revision_id,
        model_id=model_id,
    )
    session.add(job)
    return job


class KnowledgeIndex:
    def __init__(self, sessions: sessionmaker[Session], embedder: Embedder | None = None):
        self.sessions = sessions
        self.embedder = embedder
        self.semantic_error: str | None = None
        # Why semantic search is off, when it is; shown in Settings.
        self.semantic_off_reason: str | None = None

    def publish(
        self,
        session: Session,
        source: Source,
        blocks: list[ParsedBlock],
        *,
        parser: str = TEXT_PARSER,
        warning: str | None = None,
    ) -> SourceRevision:
        fingerprint = hashlib.sha256(
            json.dumps(
                {"blocks": [asdict(b) for b in blocks], "parser": parser, "warning": warning},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        revision = session.scalar(
            select(SourceRevision).where(
                SourceRevision.source_id == source.id, SourceRevision.fingerprint == fingerprint
            )
        )
        if revision is None:
            revision = SourceRevision(
                id=new_id("srev"),
                source_id=source.id,
                fingerprint=fingerprint,
                parser=parser,
                state="partial" if warning or not blocks else "ready",
                warning=warning or ("No readable content was extracted." if not blocks else None),
            )
            session.add(revision)
            session.flush()
            ids = [new_id("blk") for _ in blocks]
            for ordinal, parsed in enumerate(blocks):
                if parsed.parent is not None and not 0 <= parsed.parent < ordinal:
                    raise ValueError("A content block parent must precede its child")
                block = ContentBlock(
                    id=ids[ordinal],
                    revision_id=revision.id,
                    parent_id=ids[parsed.parent] if parsed.parent is not None else None,
                    ordinal=ordinal,
                    kind=parsed.kind,
                    content=parsed.text,
                    locator=parsed.locator[:240],
                    heading_path_json=json.dumps(parsed.headings),
                    anchor_json=json.dumps(parsed.anchor),
                    payload_json=json.dumps(parsed.payload, ensure_ascii=False),
                )
                session.add(block)
                # Flush parents before children for the self-referential foreign key.
                session.flush()
                if parsed.kind != "heading" and not parsed.payload.get("reference"):
                    session.execute(
                        text(
                            "INSERT INTO knowledge_fts(block_id,source_id,revision_id,tokens) "
                            "VALUES (:block,:source,:revision,:tokens)"
                        ),
                        {
                            "block": block.id,
                            "source": source.id,
                            "revision": revision.id,
                            "tokens": " ".join(search_tokens(parsed.text)),
                        },
                    )
        head = session.get(SourceIndexHead, source.id)
        if head:
            head.revision_id = revision.id
        else:
            session.add(SourceIndexHead(source_id=source.id, revision_id=revision.id))
        if self.embedder and blocks:
            enqueue(
                session,
                source.id,
                "embed",
                f"embed:{revision.id}:{self.embedder.model_id}",
                revision_id=revision.id,
                model_id=self.embedder.model_id,
            )
        if blocks:
            self.enqueue_summary(session, source.id, revision.id)
        session.flush()
        return revision

    def summary_key(self, revision_id: str) -> str:
        return f"summarize:{revision_id}:{self.embedder.model_id if self.embedder else 'text'}"

    def enqueue_summary(self, session: Session, source_id: str, revision_id: str) -> None:
        """Read the paper's facts (and, with a model, its vector) in the background."""

        enqueue(
            session,
            source_id,
            "summarize",
            self.summary_key(revision_id),
            revision_id=revision_id,
            model_id=self.embedder.model_id if self.embedder else None,
        )

    def add_source(self, session: Session, source: Source) -> None:
        self.publish(session, source, parse_content(source.content))

    def backfill(self, limit: int = 50) -> int:
        """Bounded startup batches; every commit is independently restartable."""
        with session_scope(self.sessions) as session:
            sources = session.scalars(
                select(Source)
                .where(
                    Source.trashed_at.is_(None),
                    ~Source.id.in_(select(SourceIndexHead.source_id)),
                    ~Source.id.in_(
                        select(ProcessingJob.source_id).where(ProcessingJob.kind == "parse_asset")
                    ),
                )
                .limit(limit)
            ).all()
            for source in sources:
                self.add_source(session, source)
            # Files parsed before passages existed are parsed again, once.
            for source_id in session.scalars(
                select(SourceIndexHead.source_id)
                .join(SourceRevision, SourceRevision.id == SourceIndexHead.revision_id)
                .join(Source, Source.id == SourceIndexHead.source_id)
                .where(
                    SourceRevision.parser == LEGACY_ASSET_PARSER,
                    Source.asset_id.is_not(None),
                    Source.trashed_at.is_(None),
                    ~SourceIndexHead.source_id.in_(
                        select(ProcessingJob.source_id).where(
                            ProcessingJob.dedupe_key.like("parse_asset:%:v2")
                        )
                    ),
                )
                .limit(limit)
            ).all():
                enqueue(session, source_id, "parse_asset", f"parse_asset:{source_id}:v2")
            if self.embedder:
                heads = session.scalars(
                    select(SourceIndexHead)
                    .where(
                        ~SourceIndexHead.revision_id.in_(
                            select(ProcessingJob.revision_id).where(
                                ProcessingJob.kind == "embed",
                                ProcessingJob.model_id == self.embedder.model_id,
                                ProcessingJob.revision_id.is_not(None),
                            )
                        )
                    )
                    .limit(limit)
                )
                for head in heads:
                    enqueue(
                        session,
                        head.source_id,
                        "embed",
                        f"embed:{head.revision_id}:{self.embedder.model_id}",
                        revision_id=head.revision_id,
                        model_id=self.embedder.model_id,
                    )
            # Sources indexed before papers were read, or before the model was on.
            summarized = select(ProcessingJob.dedupe_key).where(
                ProcessingJob.kind == "summarize"
            )
            heads = session.scalars(
                select(SourceIndexHead)
                .join(Source, Source.id == SourceIndexHead.source_id)
                .where(
                    Source.trashed_at.is_(None),
                    (
                        "summarize:"
                        + SourceIndexHead.revision_id
                        + (":" + (self.embedder.model_id if self.embedder else "text"))
                    ).not_in(summarized),
                )
                .limit(limit)
            ).all()
            for head in heads:
                self.enqueue_summary(session, head.source_id, head.revision_id)
            return len(sources)

    def retrieve(
        self,
        session: Session,
        source_ids: list[str],
        question: str,
        *,
        limit: int = 8,
        block_ids: list[str] | None = None,
    ) -> list[RetrievalHit]:
        if not source_ids or block_ids == []:
            return []
        terms = list(dict.fromkeys(search_tokens(question)))[:32]
        if not terms:
            return []
        clause = " AND f.block_id IN :blocks" if block_ids is not None else ""
        head_join = (
            "JOIN source_index_heads h ON h.source_id=f.source_id AND h.revision_id=f.revision_id "
            if block_ids is None
            else ""
        )
        statement = text(
            "SELECT f.block_id, bm25(knowledge_fts) AS rank FROM knowledge_fts AS f "
            + head_join
            + "WHERE knowledge_fts MATCH :query "
            "AND f.source_id IN :sources" + clause + " ORDER BY rank LIMIT 64"
        ).bindparams(bindparam("sources", expanding=True))
        params: dict[str, object] = {
            "query": " OR ".join('"' + term.replace('"', '""') + '"' for term in terms),
            "sources": source_ids,
        }
        if block_ids is not None:
            statement = statement.bindparams(bindparam("blocks", expanding=True))
            params["blocks"] = block_ids
        rows = session.execute(statement, params).all()
        ranked: dict[str, float] = {}
        methods: dict[str, str] = {}
        for rank, (block_id, _) in enumerate(rows):
            block = session.get(ContentBlock, block_id)
            matched = set(terms).intersection(search_tokens(block.content))
            if len(matched) < min(2, len(terms)):
                continue
            ranked[block_id] = 1 / (60 + rank)
            methods[block_id] = "keyword"
        if self.embedder:
            try:
                query = self.embedder.encode([question], query=True)[0]
                if not query or not all(math.isfinite(value) for value in query):
                    raise ValueError("Invalid query embedding")
                if block_ids is not None:
                    nearest = vector_index.nearest_among(
                        session,
                        model=self.embedder.model_id,
                        query=query,
                        block_ids=block_ids,
                        source_ids=source_ids,
                        k=32,
                    )
                else:
                    heads = session.scalars(
                        select(SourceIndexHead.revision_id).where(
                            SourceIndexHead.source_id.in_(source_ids)
                        )
                    ).all()
                    nearest = vector_index.nearest(
                        session,
                        model=self.embedder.model_id,
                        query=query,
                        revision_ids=heads,
                        k=32,
                    )
                # Each model scores unrelated text differently; E5 rarely goes below 0.7.
                floor = getattr(self.embedder, "min_similarity", 0.75)
                for rank, (block_id, similarity) in enumerate(nearest):
                    if similarity < floor:
                        continue
                    ranked[block_id] = ranked.get(block_id, 0) + 1 / (60 + rank)
                    methods[block_id] = "hybrid" if block_id in methods else "semantic"
                self.semantic_error = None
            except Exception as error:
                self.semantic_error = f"Semantic retrieval unavailable ({type(error).__name__})."
        hits: list[RetrievalHit] = []
        counts: Counter[str] = Counter()
        for block_id, score in sorted(ranked.items(), key=lambda item: item[1], reverse=True):
            block = session.get(ContentBlock, block_id)
            revision = session.get(SourceRevision, block.revision_id)
            source = session.get(Source, revision.source_id)
            if counts[source.id] >= 3:
                continue
            hits.append(RetrievalHit(block, source, score, methods[block_id]))
            counts[source.id] += 1
            if len(hits) >= limit:
                break
        return hits

    def nearest_papers(
        self,
        session: Session,
        source_ids: list[str],
        question: str,
        *,
        limit: int,
        exclude: set[str] | None = None,
    ) -> list[tuple[SourcePaper, ContentBlock | None]]:
        """Papers whose title and abstract are closest to the question (semantic only).

        ``exclude``: sources already cited, which are skipped rather than counted.
        """

        if not self.embedder or not source_ids:
            return []
        try:
            query = self.embedder.encode([question], query=True)[0]
            found = vector_index.nearest_papers(
                session,
                model=self.embedder.model_id,
                query=query,
                source_ids=[item for item in source_ids if item not in (exclude or set())],
                k=limit * 3,
            )
        except Exception as error:
            self.semantic_error = f"Semantic retrieval unavailable ({type(error).__name__})."
            return []
        floor = getattr(self.embedder, "min_similarity", 0.75)
        results: list[tuple[SourcePaper, ContentBlock | None]] = []
        works: set[str] = set()
        for source_id, similarity in found:
            paper = session.get(SourcePaper, source_id)
            if similarity < floor or paper is None or not paper.abstract:
                continue
            if paper.work_id and paper.work_id in works:
                continue  # one copy of a paper is enough
            works.add(paper.work_id or source_id)
            block_ids = json.loads(paper.abstract_block_ids_json or "[]")
            results.append((paper, session.get(ContentBlock, block_ids[0]) if block_ids else None))
            if len(results) >= limit:
                break
        return results

    def processing(self, session: Session, source_id: str) -> dict[str, object]:
        job = session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.source_id == source_id, ProcessingJob.kind == "parse_asset")
            .order_by(ProcessingJob.created_at.desc())
        )
        head = session.get(SourceIndexHead, source_id)
        revision = session.get(SourceRevision, head.revision_id) if head else None
        return {
            "state": job.state
            if job and job.state != "completed"
            else (revision.state if revision else "pending"),
            "jobId": job.id if job else None,
            "revisionId": revision.id if revision else None,
            "warning": (job.error if job else None) or (revision.warning if revision else None),
        }
