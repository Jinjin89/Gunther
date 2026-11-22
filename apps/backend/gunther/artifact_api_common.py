"""Artifact workspace headers, error mapping and output stream orchestration."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from gunther.answer_runs import AnswerRun, Stopped, check_stop, stopping_on
from gunther.api_common import _answer_runs, _sse
from gunther.artifact_read import ArtifactIntegrityError
from gunther.artifact_versions import ArtifactConflictError
from gunther.llm import ModelError

WorkspaceHeader = Annotated[
    str, Header(alias="X-Gunther-Workspace-Id", min_length=1, max_length=40)
]


# Check the conflict subclass before ValueError.
_ARTIFACT_ERROR_STATUS = {
    ArtifactConflictError: 409,
    ArtifactIntegrityError: 409,
    LookupError: 404,
    ModelError: 502,
    ValueError: 400,
}


def _artifact_error_status(error: Exception) -> int:
    return next(code for kind, code in _ARTIFACT_ERROR_STATUS.items() if isinstance(error, kind))


@contextmanager
def _artifact_http_errors(*additional: type[Exception]) -> Iterator[None]:
    try:
        yield
    except (ArtifactIntegrityError, LookupError, *additional) as error:
        raise HTTPException(status_code=_artifact_error_status(error), detail=str(error)) from error


_SECTION_VERBS = {"researching": "Researching", "writing": "Writing", "checking": "Checking"}


def _outputs_key(knowledge_base_id: str) -> str:
    """One output at a time is built in a library."""

    return f"outputs:{knowledge_base_id}"


def _output_run(
    request: Request,
    knowledge_base_id: str,
    label: str,
    call,
    words: tuple[str, str, str],
    about: str,
    result=None,
) -> StreamingResponse:
    """Start building (or revising) an output and stream how it goes.

    ``call`` does the work, given the function that hears each step. The events are the
    agents' progress (``outline``, ``section``, ``step``, ``text``), then ``done`` with
    the saved version, ``error`` saying why there is none, or ``stopped``. Closing the
    connection only stops listening; ``POST .../outputs/build/stop`` ends the work, and
    then nothing is saved.

    ``result`` shapes the ``done`` payload from what ``call`` returns (a saved version unless
    told otherwise; the Artifacts agent's turn has its own).
    """

    running, done_word, failed_word = words
    shaped = result or (lambda saved: saved.model_dump(mode="json", by_alias=True))
    activity = request.app.state.activity
    runs = _answer_runs(request)

    def work(run: AnswerRun) -> None:
        sections = 0
        # A section can be checked again after it is done: each counts once.
        finished: set[int] = set()
        tracked = run.track_activity(
            activity.track(
                f"output:{knowledge_base_id}",
                "output",
                running,
                about,
                open={"view": "library", "libraryId": knowledge_base_id, "tab": "outputs"},
                stop=lambda: runs.stop(_outputs_key(knowledge_base_id)),
                detail="Planning",
            ),
            done=done_word,
            failed=failed_word,
        )

        def hear(event: dict) -> None:
            nonlocal sections
            check_stop()
            if event["type"] == "action" and event.get("state") == "running":
                tracked.update(detail=event.get("label") or "Working")
            elif event["type"] == "stage" and event.get("state") == "running":
                # A studio build's step, with what it is doing now ("4 of 11 slides checked").
                tracked.update(
                    detail=" · ".join(filter(None, (event.get("label"), event.get("detail"))))
                    or "Working"
                )
            elif event["type"] == "outline":
                sections = len(event.get("sections") or [])
            elif event["type"] == "section":
                number = event["index"] + 1
                of = f" of {sections}" if sections else ""
                if event["state"] == "done":
                    finished.add(event["index"])
                    if sections:
                        tracked.update(progress=len(finished) / sections)
                elif event["state"] in _SECTION_VERBS:
                    verb = _SECTION_VERBS[event["state"]]
                    tracked.update(detail=f"{verb} section {number}{of}")
            if event["type"] != "saving":
                run.publish(event["type"], event)

        try:
            with stopping_on(run.stop_requested):
                artifact = call(hear)
            run.publish("done", shaped(artifact))
        except Stopped:
            run.publish("stopped", {"type": "stopped"})
        except tuple(_ARTIFACT_ERROR_STATUS) as error:
            run.publish("error", {"status": _artifact_error_status(error), "detail": str(error)})
        except Exception:
            from gunther.api import logger

            logger.exception("Building an output failed")
            run.publish("error", {"status": 500, "detail": "The output could not be built."})

    run = _answer_runs(request).start(_outputs_key(knowledge_base_id), label, work)
    if run is None:
        raise HTTPException(status_code=409, detail="Gunther is still building an output here.")
    return _sse(run)
