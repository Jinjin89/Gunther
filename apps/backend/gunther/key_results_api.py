"""List library key results and manage their origin jobs."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther import key_results
from gunther.api_common import _service
from gunther.database import session_scope
from gunther.library_overview import live_library
from gunther.models import KeyResult, KeyResultJob, utc_now
from gunther.schemas import KeyResultJobOut, KeyResultOriginKind, KeyResultOut

router = APIRouter(prefix="/knowledge-bases/{base_id}/key-results")


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


@router.post("/{result_id}/remove", status_code=204)
def remove_key_result(base_id: str, result_id: str, request: Request) -> Response:
    with _library_session(request, base_id, write=True) as session:
        result = session.get(KeyResult, result_id)
        if result is None or result.knowledge_base_id != base_id:
            raise HTTPException(404, "Key result was not found")
        if result.status == "kept":
            result.status, result.updated_at = "removed", utc_now()
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
