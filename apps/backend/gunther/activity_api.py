from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response

from gunther.activity import Activity
from gunther.schemas import ApiModel

router = APIRouter()

# How long a request that already has the latest version waits for something new.
WAIT_SECONDS = 25.0


class ActivityItemOut(ApiModel):
    id: str
    kind: Literal["transcribe", "output", "answer"]
    title: str
    about: str
    state: Literal["waiting", "running", "done", "failed"]
    progress: float | None
    detail: str
    error: str | None
    started_at: str
    ended_at: str | None
    open: dict[str, Any] | None
    can_stop: bool


class ActivityListOut(ApiModel):
    version: int
    items: list[ActivityItemOut]


def _activity(request: Request) -> Activity:
    return request.app.state.activity


@router.get("/activity", response_model=ActivityListOut)
async def list_activity(request: Request, after: int | None = None) -> dict[str, Any]:
    """What is running and what finished lately; with ``after`` = the version you have, waits
    up to 25 seconds for something to change."""

    activity = _activity(request)
    if after is not None and after == activity.snapshot()["version"]:
        return await activity.wait_for_change(after, WAIT_SECONDS)
    return activity.snapshot()


@router.post("/activity/clear", status_code=204)
def clear_activity(request: Request) -> Response:
    _activity(request).clear()
    return Response(status_code=204)


# Async, so a stop runs on the event loop, where a Transcribe again task can be cancelled.
@router.post("/activity/{activity_id}/stop", status_code=204)
async def stop_activity(activity_id: str, request: Request) -> Response:
    stopped = _activity(request).stop(activity_id)
    if stopped is None:
        raise HTTPException(status_code=404, detail="Nothing is running with this id.")
    if stopped is False:
        raise HTTPException(status_code=409, detail="This can't be stopped.")
    return Response(status_code=204)


@router.post("/activity/{activity_id}/dismiss", status_code=204)
def dismiss_activity(activity_id: str, request: Request) -> Response:
    dismissed = _activity(request).dismiss(activity_id)
    if dismissed is None:
        raise HTTPException(status_code=404, detail="Nothing finished has this id.")
    if dismissed is False:
        raise HTTPException(status_code=409, detail="This is still running.")
    return Response(status_code=204)
