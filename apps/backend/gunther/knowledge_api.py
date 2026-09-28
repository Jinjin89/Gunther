from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import func, select, update

from gunther.database import session_scope
from gunther.knowledge_index import enqueue
from gunther.models import (
    BlockEmbedding,
    ContentBlock,
    ProcessingJob,
    Source,
    SourceIndexHead,
    SourceRevision,
    utc_now,
)
from gunther.schemas import ApiModel
from gunther.topics import TopicConflict, TopicService

router = APIRouter()


class TopicInput(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    parent_id: str | None = Field(default=None, max_length=160)
    position: int = Field(default=0, ge=0, le=100_000)
    version: int = Field(default=1, ge=1)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Title cannot be blank")
        return value.strip()


class EvidenceInput(ApiModel):
    block_id: str = Field(min_length=1, max_length=160)


def structure_error(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=409
        if isinstance(error, TopicConflict)
        else (404 if isinstance(error, LookupError) else 422),
        detail=str(error),
    )


@router.get("/knowledge-bases/{base_id}/topics")
def list_topics(base_id: str, request: Request):
    try:
        return TopicService(request.app.state.knowledge_service.sessions).list(base_id)
    except LookupError as error:
        raise structure_error(error) from error


@router.post("/knowledge-bases/{base_id}/topics", status_code=201)
def create_topic(base_id: str, payload: TopicInput, request: Request):
    try:
        return TopicService(request.app.state.knowledge_service.sessions).save(
            base_id, payload.model_dump()
        )
    except (LookupError, ValueError) as error:
        raise structure_error(error) from error


@router.patch("/knowledge-bases/{base_id}/topics/{topic_id}")
def update_topic(base_id: str, topic_id: str, payload: TopicInput, request: Request):
    try:
        return TopicService(request.app.state.knowledge_service.sessions).save(
            base_id, payload.model_dump(exclude_unset=True), topic_id
        )
    except (LookupError, ValueError) as error:
        raise structure_error(error) from error


@router.post("/knowledge-bases/{base_id}/topics/{topic_id}/evidence")
def link_evidence(base_id: str, topic_id: str, payload: EvidenceInput, request: Request):
    try:
        return TopicService(request.app.state.knowledge_service.sessions).link(
            base_id, topic_id, payload.block_id
        )
    except (LookupError, ValueError) as error:
        raise structure_error(error) from error


@router.get("/sources/{source_id}/structure")
def source_structure(
    source_id: str,
    request: Request,
    revision_id: str | None = None,
    block_id: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    import json

    index = request.app.state.knowledge_service.index
    with session_scope(index.sessions) as session:
        if session.get(Source, source_id) is None:
            raise HTTPException(404, "Source was not found")
        head = session.get(SourceIndexHead, source_id)
        revision_id = revision_id or (head.revision_id if head else None)
        revision = session.get(SourceRevision, revision_id) if revision_id else None
        if revision_id and (not revision or revision.source_id != source_id):
            raise HTTPException(404, "Source revision was not found")
        statement = select(ContentBlock).where(ContentBlock.revision_id == revision_id)
        if block_id:
            statement = statement.where(ContentBlock.id == block_id)
        blocks = (
            list(
                session.scalars(
                    statement.order_by(ContentBlock.ordinal).offset(offset).limit(limit + 1)
                )
            )
            if revision
            else []
        )
        if block_id and not blocks:
            raise HTTPException(404, "Evidence block was not found in this revision")
        return {
            "sourceId": source_id,
            "revisionId": revision_id,
            "processing": index.processing(session, source_id),
            "parser": revision.parser if revision else None,
            "nextOffset": offset + limit if len(blocks) > limit else None,
            "blocks": [
                {
                    "id": b.id,
                    "parentId": b.parent_id,
                    "kind": b.kind,
                    "content": b.content,
                    "locator": b.locator,
                    "headings": json.loads(b.heading_path_json),
                    "anchor": json.loads(b.anchor_json),
                    "payload": json.loads(b.payload_json),
                }
                for b in blocks[:limit]
            ],
        }


@router.get("/sources/{source_id}/processing")
def processing_status(source_id: str, request: Request):
    service = request.app.state.knowledge_service
    with session_scope(service.sessions) as session:
        if session.get(Source, source_id) is None:
            raise HTTPException(404, "Source was not found")
        return service.index.processing(session, source_id)


@router.post("/sources/{source_id}/reprocess", status_code=202)
def reprocess(source_id: str, request: Request):
    service = request.app.state.knowledge_service
    with session_scope(service.sessions) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        source = session.get(Source, source_id)
        if not source or source.trashed_at is not None:
            raise HTTPException(404, "Source was not found")
        if not source.asset_id:
            service.index.add_source(session, source)
        else:
            job = enqueue(session, source.id, "parse_asset", f"parse_asset:{source.id}:v1")
            session.flush()
            if job.state not in {"queued", "running"}:
                job.state, job.attempts, job.error = "queued", 0, None
                job.available_at = utc_now()
        if service.index.embedder:
            session.execute(
                update(ProcessingJob)
                .where(
                    ProcessingJob.source_id == source_id,
                    ProcessingJob.kind == "embed",
                    ProcessingJob.model_id == service.index.embedder.model_id,
                    ProcessingJob.state.in_(["failed", "cancelled"]),
                )
                .values(state="queued", attempts=0, error=None, available_at=utc_now())
            )
        return service.index.processing(session, source_id)


@router.post("/sources/{source_id}/processing/cancel")
def cancel_processing(source_id: str, request: Request):
    service = request.app.state.knowledge_service
    with session_scope(service.sessions) as session:
        if session.get(Source, source_id) is None:
            raise HTTPException(404, "Source was not found")
        session.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.source_id == source_id,
                ProcessingJob.state.in_(["queued", "running"]),
            )
            .values(state="cancelled", lease_token=None, lease_until=None, updated_at=utc_now())
        )
        return service.index.processing(session, source_id)


@router.get("/retrieval/status")
def retrieval_status(request: Request):
    index = request.app.state.knowledge_service.index
    with session_scope(index.sessions) as session:
        jobs = dict(
            session.execute(
                select(ProcessingJob.state, func.count(ProcessingJob.id)).group_by(
                    ProcessingJob.state
                )
            ).all()
        )
        indexed = session.scalar(select(func.count()).select_from(SourceIndexHead))
        embedded = session.scalar(
            select(func.count())
            .select_from(BlockEmbedding)
            .where(BlockEmbedding.model == (index.embedder.model_id if index.embedder else ""))
        )
    return {
        "keyword": "fts5-bm25-cjk-bigrams-v1",
        "semanticConfigured": index.embedder is not None,
        "model": index.embedder.model_id if index.embedder else None,
        "warning": index.semantic_error,
        "vectorSearch": "scoped-exact" if index.embedder else "disabled",
        "indexedSources": indexed,
        "embeddedBlocks": embedded,
        "jobs": jobs,
        "semanticCandidateLimit": 20_000,
    }
