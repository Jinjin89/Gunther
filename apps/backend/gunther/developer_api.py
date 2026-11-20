"""Settings → Developer: how answers are made, recent log lines, and background jobs.

Only the desktop owner sees these; a paired phone does not.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from gunther.database import session_scope
from gunther.device_auth import AuthPrincipal
from gunther.log_buffer import RecentLog
from gunther.models import ProcessingJob, Source
from gunther.schemas import ApiModel
from gunther.service import KnowledgeService
from gunther.service_settings import ServiceSettingsStore

router = APIRouter()


class DeveloperIn(ApiModel):
    traces: bool


def _owner_only(request: Request) -> None:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, "Developer settings are only on the desktop")


def _store(request: Request) -> ServiceSettingsStore:
    return request.app.state.service_settings


def _service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


@router.get("/settings/developer")
def get_developer(request: Request) -> dict[str, Any]:
    _owner_only(request)
    return _store(request).developer()


@router.put("/settings/developer")
def save_developer(payload: DeveloperIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    try:
        _store(request).save_developer(traces=payload.traces)
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error
    return _store(request).developer()


@router.get("/sessions/{session_id}/messages/{message_id}/trace")
def message_trace(session_id: str, message_id: str, request: Request) -> dict[str, Any]:
    """How an answer was made, step by step; 404 when it was not kept."""

    _owner_only(request)
    found = _service(request).message_trace(session_id, message_id)
    if found is None:
        raise HTTPException(
            404,
            "No trace was kept for this answer. Turn on “Keep how answers are made” in "
            "Settings → Developer, then ask again.",
        )
    return found


@router.get("/developer/logs")
def recent_logs(
    request: Request,
    level: str = Query("info", pattern="^(info|warning|error)$"),
    limit: int = Query(200, ge=1, le=400),
) -> dict[str, Any]:
    _owner_only(request)
    recent: RecentLog = request.app.state.recent_log
    return {"lines": recent.lines(limit=limit, level=level)}


@router.get("/developer/jobs")
def recent_jobs(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Background work (reading, embedding, summaries), newest first."""

    _owner_only(request)
    with session_scope(_service(request).sessions) as session:
        rows = session.execute(
            select(ProcessingJob, Source.title)
            .join(Source, Source.id == ProcessingJob.source_id, isouter=True)
            .order_by(ProcessingJob.updated_at.desc())
            .limit(limit)
        ).all()
        return {
            "jobs": [
                {
                    "id": job.id,
                    "kind": job.kind,
                    "state": job.state,
                    "attempts": job.attempts,
                    "error": job.error,
                    "sourceId": job.source_id,
                    "sourceTitle": title,
                    "createdAt": job.created_at.isoformat(),
                    "updatedAt": job.updated_at.isoformat(),
                }
                for job, title in rows
            ]
        }
