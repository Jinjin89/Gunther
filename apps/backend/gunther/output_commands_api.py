"""The compatibility build route and operations on saved outputs."""

import json

from fastapi import APIRouter, Request, status
from fastapi.responses import Response, StreamingResponse

from gunther.api_common import _answer_runs, _service, _sse
from gunther.artifact_api_common import (
    WorkspaceHeader,
    _artifact_http_errors,
    _output_run,
    _outputs_key,
)
from gunther.artifact_versions import earlier_output, output_library, output_version
from gunther.database import session_scope
from gunther.llm import ModelError
from gunther.output_types import OUTPUT_TYPES, output_type_for
from gunther.outputs import _marked, split_document
from gunther.schemas import (
    ArtifactOut,
    ArtifactTurnInput,
    BuildOutputInput,
    BuildTurnInput,
    EditOutputInput,
    OutputTypeOut,
    RestyleSlidesInput,
    ReviewSlidesInput,
)

stream_router = APIRouter()
router = APIRouter()


@router.get("/output-types", response_model=list[OutputTypeOut])
def output_types() -> list[OutputTypeOut]:
    return [OutputTypeOut.model_validate(item) for item in OUTPUT_TYPES.values()]


# The words for an output's entry in the top bar: running, done, failed.
_BUILD_WORDS = {
    "slides": ("Building slides", "Slides ready", "Couldn't build slides"),
    "report": ("Writing a report", "Report ready", "Couldn't write the report"),
}


def _legacy_build(service, base_id: str, workspace_id: str, payload: BuildOutputInput, hear):
    fields = service._output_request_fields(payload)
    previous = [service._output_fingerprint(workspace_id, base_id, "build", fields)]
    inherits_style = payload.kind == "report" and "style" not in payload.model_fields_set
    if payload.kind == "report" and payload.supersedes_artifact_id:
        fields["inherit_style"] = inherits_style
        previous.append(service._output_fingerprint(workspace_id, base_id, "build", fields))
    fields.pop("library_supplements")
    fingerprint = service._output_fingerprint(workspace_id, base_id, "build", fields)
    earlier = earlier_output(
        service.sessions, workspace_id, payload.client_request_id, fingerprint, previous=previous,
    )
    if earlier is not None:
        return earlier

    with session_scope(service.sessions) as session:
        output_library(session, workspace_id, base_id)
        parent = (
            output_version(
                session, workspace_id, base_id, payload.supersedes_artifact_id, latest=True,
            )
            if payload.supersedes_artifact_id else None
        )
        provenance = json.loads(parent.provenance_json) if parent else {}
        owner = provenance.get("conversation_id")
        if owner and payload.conversation_id and owner != payload.conversation_id:
            raise ValueError("This artifact belongs to a different conversation.")
        same_shape = parent is not None and parent.kind == payload.kind
        prior = provenance if same_shape else {}
        style = parent.style if inherits_style and same_shape else payload.style
        item = output_type_for(payload.kind, style)
        theme, design = payload.theme, payload.design_version
        if same_shape and payload.kind == "slides":
            slides = split_document(parent.content, "slides")[1]
            if slides:
                from gunther.studio import format as studio_format

                cover = _marked(slides[0])
                studio_theme = studio_format.parse_slide(slides[0]).attrs.get("theme")
                inherited_theme = (
                    studio_theme if studio_theme in studio_format.THEMES else cover.theme
                )
                theme = theme or inherited_theme
                design = cover.design_version if design is None else design
        length = payload.length if "length" in payload.model_fields_set else prior.get("length")
        language = (
            payload.language if "language" in payload.model_fields_set else prior.get("language")
        )
        use = prior.get("use") if payload.use == "auto" else payload.use
        build = BuildTurnInput(
            type=item.id, focus=payload.brief, audience=payload.audience,
            length=length, language=language, use=use if item.shape == "slides" else None,
            theme=theme, design_version=design if item.shape == "slides" else None,
            web=payload.web, title=payload.title, scope=payload.scope.model_dump(),
            outline=payload.outline,
        )
        lineage = parent.lineage_id if parent else None

    conversation_id = payload.conversation_id or owner or "ac_" + service._output_fingerprint(
        workspace_id, base_id, "build-conversation", {"requestId": payload.client_request_id},
    )
    turn = service.artifact_turn(
        base_id, workspace_id, conversation_id,
        ArtifactTurnInput(
            client_request_id=payload.client_request_id, kind="build", build=build,
            target_lineage_id=lineage, model=payload.model, effort=payload.effort,
        ),
        # Legacy followers expect an Artifact as the terminal payload.
        lambda event: hear(event) if event["type"] != "turn" else None,
        legacy_fingerprint=fingerprint, legacy_title=payload.conversation_title,
        expected_parent=payload.supersedes_artifact_id,
    )
    assert turn.artifact is not None
    return turn.artifact


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
        lambda hear: _legacy_build(service, knowledge_base_id, workspace_id, payload, hear),
        _BUILD_WORDS[payload.kind],
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
