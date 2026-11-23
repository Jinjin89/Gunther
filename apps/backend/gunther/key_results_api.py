"""List library key results and manage their origin jobs."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gunther import key_results
from gunther.api_common import _service
from gunther.database import session_scope
from gunther.library_overview import live_library
from gunther.models import (
    KeyResult,
    KeyResultEvidence,
    KeyResultJob,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    utc_now,
)
from gunther.schemas import ApiModel, KeyResultJobOut, KeyResultOriginKind, KeyResultOut

router = APIRouter(prefix="/knowledge-bases/{base_id}/key-results")


class PendingSavedAnswerOut(ApiModel):
    id: str
    title: str
    content: str


@contextmanager
def _library_session(request: Request, base_id: str, *, write: bool = False) -> Iterator[Session]:
    with session_scope(_service(request).sessions) as session:
        if write:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if live_library(session, base_id) is None:
            raise HTTPException(404, "Library was not found")
        yield session


@router.get("")
def list_key_results(base_id: str, request: Request) -> list[KeyResultOut]:
    with _library_session(request, base_id) as session:
        return key_results.list_kept(session, base_id)


@router.get("/pending-saved-answers")
def pending_saved_answers(base_id: str, request: Request) -> list[PendingSavedAnswerOut]:
    migrated = select(KeyResultJob.id).where(
        KeyResultJob.knowledge_base_id == base_id, KeyResultJob.origin_kind == "saved_answer",
        KeyResultJob.origin_id == KnowledgeUnit.id, KeyResultJob.state == "done",
    ).exists()
    revisions = select(KnowledgeUnitRevision.content).where(
        KnowledgeUnitRevision.unit_id == KnowledgeUnit.id,
    )
    head = revisions.where(
        KnowledgeUnitRevision.id == KnowledgeUnit.head_revision_id,
    ).scalar_subquery()
    latest = revisions.order_by(KnowledgeUnitRevision.revision_number.desc()).limit(1)
    content = func.coalesce(
        head, latest.scalar_subquery(),
    )
    with _library_session(request, base_id) as session:
        return [
            PendingSavedAnswerOut(id=unit_id, title=title, content=saved or "")
            for unit_id, title, saved in session.execute(
                select(KnowledgeUnit.id, KnowledgeUnit.title, content)
                .where(
                    KnowledgeUnit.knowledge_base_id == base_id,
                    KnowledgeUnit.status == "trusted",
                    ~migrated,
                )
                .order_by(KnowledgeUnit.created_at.desc(), KnowledgeUnit.id)
            )
        ]


def _result(session: Session, base_id: str, result_id: str) -> KeyResult:
    result = session.get(KeyResult, result_id)
    if result is None or result.knowledge_base_id != base_id:
        raise HTTPException(404, "Key result was not found")
    return result


@router.post(
    "/{result_id}/remove", status_code=204, responses={200: {"description": "Removal stamp"}},
)
def remove_key_result(
    base_id: str, result_id: str, request: Request,
    with_undo: Annotated[bool, Query(alias="withUndo")] = False,
) -> Response:
    with _library_session(request, base_id, write=True) as session:
        result = _result(session, base_id, result_id)
        if result.status == "kept":
            result.status, result.updated_at = "removed", utc_now()
            if with_undo:
                return JSONResponse({"removedAt": result.updated_at.isoformat()})
        elif with_undo:
            raise HTTPException(409, "This key result was already removed.")
    return Response(status_code=204)


@router.post("/{result_id}/restore", status_code=204)
def restore_key_result(
    base_id: str, result_id: str, request: Request,
    removed_at: Annotated[str, Query(alias="removedAt")],
) -> Response:
    with _library_session(request, base_id, write=True) as session:
        result = _result(session, base_id, result_id)
        if result.status == "kept":
            return Response(status_code=204)
        # An origin Undo deletes the creating origin's evidence even if the result
        # was already removed. An older toast must not bring that result back.
        evidence = select(KeyResultEvidence.id).where(
            KeyResultEvidence.key_result_id == result.id,
            KeyResultEvidence.origin_kind == result.created_origin_kind,
            KeyResultEvidence.origin_id == result.created_origin_id,
        ).exists()
        if result.updated_at.isoformat() != removed_at or not session.scalar(select(evidence)):
            raise HTTPException(409, "This removal can no longer be undone.")
        result.status, result.updated_at = "kept", utc_now()
    return Response(status_code=204)


def _origin_job(
    session: Session, base_id: str, kind: KeyResultOriginKind, origin_id: str,
) -> KeyResultJob:
    job = session.scalar(select(KeyResultJob).where(
        KeyResultJob.knowledge_base_id == base_id,
        KeyResultJob.origin_kind == kind, KeyResultJob.origin_id == origin_id,
    ))
    if job is None:
        raise HTTPException(404, "Key results job was not found")
    return job


@router.get("/origins/{origin_kind}/{origin_id}")
def key_result_origin(
    base_id: str, origin_kind: KeyResultOriginKind, origin_id: str, request: Request,
) -> KeyResultJobOut:
    with _library_session(request, base_id) as session:
        _origin_job(session, base_id, origin_kind, origin_id)
        return key_results.origin_states(session, origin_kind, [origin_id])[origin_id]


@router.post("/origins/{origin_kind}/{origin_id}/undo")
def undo_key_results(
    base_id: str, origin_kind: KeyResultOriginKind, origin_id: str, request: Request,
) -> KeyResultJobOut:
    with _library_session(request, base_id, write=True) as session:
        job = _origin_job(session, base_id, origin_kind, origin_id)
        if job.state != "done":
            raise HTTPException(409, "Wait for the key results job to finish before undoing it.")
        key_results.undo(session, job)
        return key_results.origin_states(session, origin_kind, [origin_id])[origin_id]


@router.post("/jobs/{job_id}/retry")
def retry_key_results(base_id: str, job_id: str, request: Request) -> KeyResultJobOut:
    with _library_session(request, base_id, write=True) as session:
        job = session.get(KeyResultJob, job_id)
        if job is None or job.knowledge_base_id != base_id:
            raise HTTPException(404, "Key results job was not found")
        if job.state != "failed":
            raise HTTPException(409, "Only failed key results jobs can be retried.")
        job.state, job.error, job.updated_at = "queued", None, utc_now()
        return key_results.origin_states(session, job.origin_kind, [job.origin_id])[job.origin_id]
