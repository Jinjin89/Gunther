"""The compatibility build route and operations on saved outputs."""

from collections.abc import Callable

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from gunther.answer_runs import AnswerRun
from gunther.api_common import _service
from gunther.artifact_api_common import (
    WorkspaceHeader,
    _artifact_http_errors,
    _output_error,
    _output_event,
    _output_run,
)
from gunther.artifact_audio import render_audio, voice_options
from gunther.artifact_plans import PlanConflictError
from gunther.artifact_read import ArtifactIntegrityError, artifact_detail, artifact_summary
from gunther.artifact_versions import earlier_output, output_library, output_version
from gunther.database import session_scope
from gunther.llm import ModelError
from gunther.models import ArtifactConversation
from gunther.output_commands import slide_cover_settings
from gunther.output_types import OUTPUT_TYPES, SHAPES, output_type_for
from gunther.outputs import Events, split_document
from gunther.schemas import (
    ArtifactOut,
    ArtifactTurnInput,
    BuildOutputInput,
    BuildTurnInput,
    EditOutputInput,
    OutputTypeOut,
    RestyleSlidesInput,
    RetryAudioInput,
    ReviewSlidesInput,
)

stream_router = APIRouter()
router = APIRouter()


@router.get("/output-types", response_model=list[OutputTypeOut])
def output_types(request: Request) -> list[OutputTypeOut]:
    types = [OutputTypeOut.model_validate(item) for item in OUTPUT_TYPES.values()]
    for item in types:
        if "voices" in item.options:
            item.voices = voice_options(request)
    return types


@stream_router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/audio/stream")
def retry_audio(
    request: Request, knowledge_base_id: str, artifact_id: str,
    payload: RetryAudioInput, workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    service = _service(request)
    with _artifact_http_errors(ValueError):
        artifact = service.get_artifact(knowledge_base_id, artifact_id, workspace_id)
        if not SHAPES[artifact.kind].speech:
            raise ValueError("This output does not support audio.")
    return _output_run(
        request, workspace_id, knowledge_base_id, "Recording audio",
        lambda hear, _run: render_audio(request, artifact, hear, retry=True, voices=payload.voices),
        ("Recording audio", "Done", "Couldn't record audio"), artifact.title,
        conversation_id=artifact.conversation_id, lineage_id=artifact.lineage_id,
        artifact_id=artifact.id, type_id=artifact.style,
    )


# The words for an output's entry in the top bar: running, done, failed.
_BUILD_WORDS = {
    "slides": ("Building slides", "Slides ready", "Couldn't build slides"),
    "report": ("Writing a report", "Report ready", "Couldn't write the report"),
}


def _visible_conversation(
    session: Session, workspace_id: str, base_id: str, conversation_id: str | None,
) -> None:
    row = session.get(ArtifactConversation, conversation_id) if conversation_id else None
    if row is not None and (
        row.trashed_at is not None or (row.workspace_id, row.knowledge_base_id)
        != (workspace_id, base_id)
    ):
        raise LookupError("The output's conversation was not found in this library.")


def _legacy_build(
    request: Request, base_id: str, workspace_id: str, payload: BuildOutputInput,
) -> StreamingResponse:
    service = _service(request)
    with session_scope(service.sessions) as session:
        library_title = output_library(session, workspace_id, base_id).title
        _visible_conversation(session, workspace_id, base_id, payload.conversation_id)

    fields = service._output_request_fields(payload)
    previous = [service._output_fingerprint(workspace_id, base_id, "build", fields)]
    inherits_style = payload.kind == "report" and "style" not in payload.model_fields_set
    if payload.kind == "report" and payload.supersedes_artifact_id is not None:
        fields["inherit_style"] = inherits_style
        previous.append(service._output_fingerprint(workspace_id, base_id, "build", fields))
    previous.append(service._output_fingerprint(
        workspace_id, base_id, "build",
        {name: value for name, value in fields.items() if name != "library_supplements"},
    ))
    inherits_scope = "scope" not in payload.model_fields_set
    fields["inherit_scope"] = inherits_scope
    fingerprint = service._output_fingerprint(workspace_id, base_id, "build", fields)
    earlier = earlier_output(
        service.sessions, workspace_id, payload.client_request_id, fingerprint, previous=previous,
    )
    if earlier is not None:
        if earlier.knowledge_base_id != base_id:
            raise LookupError("The output was not found in this library.")
        with session_scope(service.sessions) as session:
            _visible_conversation(session, workspace_id, base_id, earlier.conversation_id)
        return _output_event("done", earlier.model_dump(mode="json", by_alias=True))
    if payload.supersedes_artifact_id is None:
        return _output_error(PlanConflictError(
            "plan_required", "Plan this output in Studio and choose Approve & Generate.",
        ))

    with session_scope(service.sessions) as session:
        row = output_version(
            session, workspace_id, base_id, payload.supersedes_artifact_id, latest=True,
        )
        parent = artifact_detail(session, row)
        owner = parent.conversation_id
        if owner and payload.conversation_id and owner != payload.conversation_id:
            raise LookupError("This artifact was not found in this conversation.")
        _visible_conversation(session, workspace_id, base_id, owner)
        same_shape = parent.kind == payload.kind
        prior = parent.provenance if same_shape else None
        style = parent.style if inherits_style and same_shape else payload.style
        item = output_type_for(payload.kind, style)
        theme, design = payload.theme, payload.design_version
        if same_shape and payload.kind == "slides":
            slides = split_document(parent.content, "slides")[1]
            if slides:
                inherited_theme, inherited_design = slide_cover_settings(slides[0])
                theme = theme or inherited_theme
                design = inherited_design if design is None else design
        length = (
            payload.length if "length" in payload.model_fields_set
            else getattr(prior, "length", None)
        )
        language = (
            payload.language if "language" in payload.model_fields_set
            else getattr(prior, "language", None)
        )
        use = getattr(prior, "use", None) if payload.use == "auto" else payload.use
        scope = (parent.scope if inherits_scope else None) or payload.scope
        service._resolve_output_scope(session, base_id, scope, strict=True)
        build = BuildTurnInput(
            type=item.id, focus=payload.brief, audience=payload.audience,
            length=length, language=language, use=use if item.shape == "slides" else None,
            theme=theme, design_version=design if item.shape == "slides" else None,
            web=payload.web, title=payload.title, scope=scope.model_dump(),
            outline=payload.outline,
        )
        lineage = parent.lineage_id

    conversation_id = payload.conversation_id or owner or "ac_" + service._output_fingerprint(
        workspace_id, base_id, "build-conversation", {"requestId": payload.client_request_id},
    )
    turn = ArtifactTurnInput(
        client_request_id=payload.client_request_id, kind="build", build=build,
        target_lineage_id=lineage, model=payload.model, effort=payload.effort,
    )

    def rebuild(hear: Events, run: AnswerRun) -> ArtifactOut:
        result = service.artifact_turn(
            base_id, workspace_id, conversation_id, turn,
            lambda event: hear(event) if event["type"] != "turn" else None,
            run=run,
            legacy_fingerprint=fingerprint, legacy_title=payload.conversation_title,
            expected_parent=parent.id,
        )
        if result.artifact is None:
            raise ValueError(result.reply or "The rebuild did not produce a version.")
        return result.artifact

    words = _BUILD_WORDS.get(
        payload.kind, (f"Building {item.label.lower()}", "Ready", "Couldn't build"),
    )
    return _output_run(
        request, workspace_id, base_id, payload.brief, rebuild, words, library_title,
        conversation_id=conversation_id, type_id=build.type, lineage_id=lineage,
        artifact_id=parent.id,
    )


@stream_router.post("/knowledge-bases/{knowledge_base_id}/outputs/build/stream")
def build_output_stream(
    knowledge_base_id: str,
    payload: BuildOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Replay an earlier build or rebuild the verified current version."""

    try:
        return _legacy_build(request, knowledge_base_id, workspace_id, payload)
    except (ArtifactIntegrityError, LookupError, ValueError) as error:
        return _output_error(error)


def _version_run(
    request: Request, base_id: str, workspace_id: str, artifact_id: str,
    label: str, call: Callable[[Events, AnswerRun], ArtifactOut],
    words: tuple[str, str, str], *, conversation_id: str | None = None,
) -> StreamingResponse:
    service = _service(request)
    try:
        with session_scope(service.sessions) as session:
            output_library(session, workspace_id, base_id)
            # A replay can still name a version that is no longer the head.
            parent = output_version(session, workspace_id, base_id, artifact_id, latest=False)
            owner = artifact_summary(session, parent).conversation_id
            if owner and conversation_id and owner != conversation_id:
                raise LookupError("This artifact was not found in this conversation.")
    except (ArtifactIntegrityError, LookupError, ValueError) as error:
        return _output_error(error)
    return _output_run(
        request, workspace_id, base_id, label, call, words, service.library_title(base_id),
        conversation_id=owner or conversation_id,
        type_id=output_type_for(parent.kind, parent.style).id,
        lineage_id=parent.lineage_id, artifact_id=artifact_id,
    )


@stream_router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/visual-review/stream")
def review_slides_stream(
    knowledge_base_id: str, artifact_id: str, payload: ReviewSlidesInput,
    request: Request, workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    service = _service(request)
    return _version_run(
        request, knowledge_base_id, workspace_id, artifact_id, "Review rendered slides",
        lambda hear, _run: service.review_slides(
            knowledge_base_id, workspace_id, artifact_id, payload, hear,
        ),
        ("Reviewing rendered slides", "Slide review saved", "Couldn't review slides"),
    )


@stream_router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/restyle/stream")
def restyle_slides_stream(
    knowledge_base_id: str,
    artifact_id: str,
    payload: RestyleSlidesInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Draw every slide of the latest version again in another style: the next version."""

    service = _service(request)
    return _version_run(
        request,
        knowledge_base_id,
        workspace_id,
        artifact_id,
        f"Restyle the slides: {payload.style}",
        lambda hear, _run: service.restyle_output(
            knowledge_base_id, workspace_id, artifact_id, payload, hear
        ),
        ("Designing the slides", "Slides restyled", "Couldn't restyle the slides"),
        conversation_id=payload.conversation_id,
    )


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
