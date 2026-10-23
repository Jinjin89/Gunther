"""Token history for Settings → Usage. Days follow the supplied local UTC offset."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select

from gunther.database import session_scope
from gunther.device_auth import AuthPrincipal
from gunther.models import ModelUsage

router = APIRouter()
COUNTS = ("input", "cached", "output", "reasoning")


def _sessions(request: Request):
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, "Usage history is only on the desktop")
    return request.app.state.knowledge_service.sessions


@router.get("/usage/summary")
def summary(
    request: Request,
    range: Literal["today", "7d", "30d", "all"] = "7d",
    tz_offset: int = Query(0, alias="tzOffset", ge=-840, le=840),
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    offset = timedelta(minutes=tz_offset)
    start = (now + offset).replace(hour=0, minute=0, second=0, microsecond=0) - offset
    if range in ("7d", "30d"):
        start -= timedelta(days=int(range[:-1]) - 1)
    totals = [func.sum(getattr(ModelUsage, name)).label(name) for name in COUNTS] + [
        func.count().label("requests")
    ]
    conditions = [ModelUsage.at <= now]
    if range != "all":
        conditions.append(ModelUsage.at >= start)
    # SQLite rounds fractional seconds with modifiers; do not round into the next day.
    local_day = func.date(func.substr(ModelUsage.at, 1, 19), f"{tz_offset:+d} minutes")
    with session_scope(_sessions(request)) as session:

        def grouped(*columns):
            query = select(*columns, *totals).where(*conditions)
            if columns:
                query = query.group_by(*columns).order_by(*columns)
            return [dict(row) for row in session.execute(query).mappings()]

        return {
            "totals": grouped()[0],
            "byDay": grouped(local_day.label("day")),
            "byModel": grouped(
                ModelUsage.provider_id.label("providerId"), ModelUsage.model_id.label("modelId")
            ),
            "byJob": grouped(ModelUsage.job),
        }


@router.get("/usage/recent")
def recent(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    with session_scope(_sessions(request)) as session:
        rows = session.scalars(
            select(ModelUsage).order_by(ModelUsage.at.desc(), ModelUsage.id.desc()).limit(limit)
        ).all()
        return {
            "requests": [
                {
                    "id": row.id,
                    "at": row.at.replace(tzinfo=UTC).isoformat(),
                    "providerId": row.provider_id,
                    "modelId": row.model_id,
                    "job": row.job,
                    "detail": row.detail,
                    "ref": row.ref,
                    **{name: getattr(row, name) for name in COUNTS},
                    "status": row.status,
                    "ms": row.ms,
                }
                for row in rows
            ]
        }


@router.delete("/usage", status_code=204)
def clear(request: Request) -> Response:
    with session_scope(_sessions(request)) as session:
        session.execute(delete(ModelUsage))
    return Response(status_code=204)
