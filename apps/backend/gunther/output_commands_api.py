"""Direct output builds, revisions, reviews, restyles, edits and checks."""

from fastapi import APIRouter, Request, status
from fastapi.responses import Response, StreamingResponse

from gunther.api_common import _answer_runs, _service, _sse
from gunther.artifact_api_common import (
    WorkspaceHeader,
    _artifact_http_errors,
    _output_run,
    _outputs_key,
)
from gunther.llm import ModelError
from gunther.schemas import (
    ArtifactOut,
    BuildOutputInput,
    EditOutputInput,
    RestyleSlidesInput,
    ReviewSlidesInput,
    ReviseOutputInput,
)

stream_router = APIRouter()
router = APIRouter()


# The words for an output's entry in the top bar: running, done, failed.
_OUTPUT_WORDS = {
    ("build", "slides"): ("Building slides", "Slides ready", "Couldn't build slides"),
    ("build", "report"): ("Writing a report", "Report ready", "Couldn't write the report"),
    ("revise", "slides"): ("Revising slides", "Slides revised", "Couldn't revise the slides"),
    ("revise", "report"): (
        "Revising the report",
        "Report revised",
        "Couldn't revise the report",
    ),
}


@stream_router.post("/knowledge-bases/{knowledge_base_id}/outputs/build/stream")
async def build_output_stream(
    knowledge_base_id: str,
    payload: BuildOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Build a report or slides by agents from the library or what was picked."""

    service = _service(request)
    return _output_run(
        request,
        knowledge_base_id,
        payload.brief,
        lambda hear: service.build_output(knowledge_base_id, workspace_id, payload, hear),
        _OUTPUT_WORDS["build", payload.kind],
        service.library_title(knowledge_base_id),
    )


@stream_router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/visual-review/stream")
async def review_slides_stream(knowledge_base_id: str, artifact_id: str,
                               payload: ReviewSlidesInput, request: Request,
                               workspace_id: WorkspaceHeader) -> StreamingResponse:
    service = _service(request)
    return _output_run(request, knowledge_base_id, "Review rendered slides",
        lambda hear: service.review_slides(knowledge_base_id, workspace_id, artifact_id,
                                           payload, hear),
        ("Reviewing rendered slides", "Slide review saved", "Couldn't review slides"),
        service.library_title(knowledge_base_id))


@stream_router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/revise/stream")
async def revise_output_stream(
    knowledge_base_id: str,
    artifact_id: str,
    payload: ReviseOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Change a section (or all) of the latest version as instructed: the next version."""

    service = _service(request)
    try:
        kind = service.artifact_kind(knowledge_base_id, artifact_id)
    except LookupError:
        kind = "report"  # the run itself answers 404
    return _output_run(
        request,
        knowledge_base_id,
        payload.instruction,
        lambda hear: service.revise_output(
            knowledge_base_id, workspace_id, artifact_id, payload, hear
        ),
        _OUTPUT_WORDS["revise", "slides" if kind == "slides" else "report"],
        service.library_title(knowledge_base_id),
    )


@stream_router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/restyle/stream")
async def restyle_slides_stream(
    knowledge_base_id: str,
    artifact_id: str,
    payload: RestyleSlidesInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Draw every slide of the latest version again in another style: the next version."""

    service = _service(request)
    return _output_run(
        request,
        knowledge_base_id,
        f"Restyle the slides: {payload.style}",
        lambda hear: service.restyle_output(
            knowledge_base_id, workspace_id, artifact_id, payload, hear
        ),
        ("Designing the slides", "Slides restyled", "Couldn't restyle the slides"),
        service.library_title(knowledge_base_id),
    )


@router.get("/knowledge-bases/{knowledge_base_id}/outputs/build/stream", response_model=None)
async def follow_output_build(
    knowledge_base_id: str, request: Request, workspace_id: WorkspaceHeader
) -> Response:
    """Follow an output being built: what it did so far, then live. 204 when none is."""

    run = _answer_runs(request).get(_outputs_key(knowledge_base_id))
    if run is None or run.finished:
        return Response(status_code=204)
    return _sse(run, resumed=True)


@router.post("/knowledge-bases/{knowledge_base_id}/outputs/build/stop", status_code=204)
def stop_output_build(
    knowledge_base_id: str, request: Request, workspace_id: WorkspaceHeader
) -> Response:
    """Stop the output being built. Nothing is saved."""

    _answer_runs(request).stop(_outputs_key(knowledge_base_id))
    return Response(status_code=204)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/edits",
    response_model=ArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
def edit_output(
    knowledge_base_id: str,
    artifact_id: str,
    payload: EditOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactOut:
    """Save typed text as the next version, exactly as typed."""

    with _artifact_http_errors(ValueError):
        return _service(request).edit_output(knowledge_base_id, workspace_id, artifact_id, payload)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/check",
    response_model=ArtifactOut,
)
def check_output(
    knowledge_base_id: str,
    artifact_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactOut:
    """Have the Checker look at the sections it has not checked as they read now."""

    with _artifact_http_errors(ValueError, ModelError):
        return _service(request).check_output(knowledge_base_id, workspace_id, artifact_id)
