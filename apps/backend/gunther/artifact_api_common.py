"""Artifact workspace headers, error mapping and output stream orchestration."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Annotated

from fastapi import Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from gunther.answer_runs import AnswerRun, Stopped, check_stop, output_key, stopping_on
from gunther.api_common import _answer_runs, _service, _sse, _sse_events
from gunther.artifact_audio import render_audio
from gunther.artifact_plans import PlanConflictError, PlanValidationError
from gunther.artifact_read import ArtifactIntegrityError
from gunther.artifact_versions import ArtifactConflictError, WorkPlanConflict
from gunther.llm import ModelError
from gunther.output_types import output_type_for
from gunther.outputs import Events
from gunther.schemas import ArtifactOut, ArtifactTurnOut

WorkspaceHeader = Annotated[
    str, Header(alias="X-Gunther-Workspace-Id", min_length=1, max_length=40)
]


# Check the conflict subclass before ValueError.
_ARTIFACT_ERROR_STATUS = {
    ArtifactConflictError: 409,
    PlanConflictError: 409,
    ArtifactIntegrityError: 409,
    LookupError: 404,
    ModelError: 502,
    PlanValidationError: 422,
    ValueError: 400,
}


def _artifact_error_status(error: Exception) -> int:
    return next(code for kind, code in _ARTIFACT_ERROR_STATUS.items() if isinstance(error, kind))


def _error_detail(error: Exception) -> str | dict | list:
    if isinstance(error, PlanConflictError):
        current = error.current if isinstance(error, WorkPlanConflict) else {}
        return {"code": error.code, "detail": str(error), **current}
    if isinstance(error, PlanValidationError):
        return error.detail
    return str(error)


@contextmanager
def _artifact_http_errors(*additional: type[Exception]) -> Iterator[None]:
    try:
        yield
    except (ArtifactIntegrityError, LookupError, *additional) as error:
        raise HTTPException(
            status_code=_artifact_error_status(error), detail=_error_detail(error),
        ) from error


def _conversation_run(
    request: Request, workspace_id: str, knowledge_base_id: str, conversation_id: str,
) -> AnswerRun | None:
    run = _answer_runs(request).get(output_key(conversation_id))
    if run is not None and not run.finished and run.scope == (workspace_id, knowledge_base_id):
        return run
    return None


def _output_event(name: str, data: dict) -> StreamingResponse:
    """Reply to a replay or a rejected request without starting a run."""

    async def event():
        yield name, data

    return _sse_events(event())


def _output_error(error: Exception) -> StreamingResponse:
    return _output_event("error", _error_event(error))


def _error_event(error: Exception) -> dict:
    status = _artifact_error_status(error)
    if isinstance(error, PlanConflictError):
        return {"status": status, **_error_detail(error)}
    if isinstance(error, PlanValidationError):
        return {"status": status, "detail": str(error), "issues": error.detail}
    return {"status": status, "detail": str(error)}


def _output_run(
    request: Request,
    workspace_id: str,
    knowledge_base_id: str,
    label: str,
    call: Callable[[Events, AnswerRun], ArtifactOut | ArtifactTurnOut],
    words: tuple[str, str, str],
    about: str,
    *,
    conversation_id: str | None,
    type_id: str | None = None,
    lineage_id: str | None = None,
    artifact_id: str | None = None,
    plan_id: str | None = None,
    request_id: str | None = None,
) -> StreamingResponse:
    """Stream one output run while Activity offers to open or stop it."""

    running, done_word, failed_word = words
    activity = request.app.state.activity
    runs = _answer_runs(request)
    key = output_key(conversation_id, lineage_id)
    target = {
        "view": "library", "libraryId": knowledge_base_id, "tab": "outputs",
        "conversationId": conversation_id, "typeId": type_id,
        "lineageId": lineage_id, "artifactId": artifact_id,
        **({"planId": plan_id} if plan_id is not None else {}),
    }

    def work(run: AnswerRun) -> None:
        tracked = run.track_activity(
            activity.track(key, "output", running, about, open=target, stop=run.stop),
            done=done_word,
            failed=failed_word,
        )

        def hear(event: dict) -> None:
            if event["type"] != "plan":
                check_stop()
            if event["type"] == "action" and event.get("state") == "running":
                tracked.update(detail=event.get("label") or "Working")
            elif event["type"] == "stage":
                tracked.update(
                    detail=" · ".join(filter(None, (event.get("label"), event.get("detail"))))
                )
            if event["type"] != "saving":
                run.publish(event["type"], event)

        try:
            with stopping_on(run.stop_requested):
                result = call(hear, run)
                saved = result.artifact if isinstance(result, ArtifactTurnOut) else result
                if saved is not None and saved.audio and saved.audio.status == "pending":
                    # The script was sealed before Stop. Leave recording retryable.
                    with suppress(Stopped):
                        saved = render_audio(request, saved, hear)
                    result = (result.model_copy(update={"artifact": saved})
                              if isinstance(result, ArtifactTurnOut) else saved)
            saved = result.artifact if isinstance(result, ArtifactTurnOut) else result
            plan = result.plan if isinstance(result, ArtifactTurnOut) else None
            opened = {
                **target,
                "conversationId": saved.conversation_id or conversation_id,
                "typeId": output_type_for(saved.kind, saved.style).id,
                "lineageId": saved.lineage_id,
                "artifactId": saved.id,
            } if saved is not None else {**target, "typeId": plan.options.type} if plan else None
            run.publish("done", result.model_dump(mode="json", by_alias=True), open=opened)
        except Stopped:
            run.publish("stopped", {"type": "stopped"})
        except LookupError as error:
            # The request may have named a target moved to Trash before the run registered.
            tracked.stopped()
            run.publish("error", {"status": 404, "detail": str(error)})
        except tuple(_ARTIFACT_ERROR_STATUS) as error:
            run.publish("error", _error_event(error))
        except Exception:
            from gunther.api import logger

            logger.exception("Building an output failed")
            run.publish("error", {"status": 500, "detail": "The output could not be built."})

    on_stop = None
    if conversation_id is not None and plan_id is not None and request_id is not None:
        service = _service(request)

        def stop(run: AnswerRun) -> None:
            service.stop_artifact_plan(
                knowledge_base_id, workspace_id, conversation_id, plan_id, request_id, run,
            )

        on_stop = stop
    run = runs.start(
        key, label, work, scope=(workspace_id, knowledge_base_id), lineage_id=lineage_id,
        on_stop=on_stop,
    )
    if run is None:
        active = runs.get(key)
        follow = (
            f"/api/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/stream"
            if conversation_id is not None and active is not None
            and active.scope == (workspace_id, knowledge_base_id) else None
        )
        raise HTTPException(status_code=409, detail={
            "code": "run_active", "detail": "Gunther is still working on this output.",
            **({"follow": follow} if follow else {}),
        })
    return _sse(run)
