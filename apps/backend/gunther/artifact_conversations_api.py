"""Artifact conversation lifecycle, briefs and turn streams."""

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from gunther.api_common import _service
from gunther.artifact_api_common import WorkspaceHeader, _artifact_http_errors, _output_run
from gunther.artifact_versions import ArtifactConflictError
from gunther.schemas import (
    ArtifactBriefStateOut,
    ArtifactConversationOut,
    ArtifactConversationSummaryOut,
    ArtifactTurnInput,
    CreateArtifactConversationInput,
    EditArtifactBriefInput,
    ImportArtifactConversationsInput,
    UpdateArtifactConversationInput,
)

turn_router = APIRouter()
router = APIRouter()


@turn_router.post(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/messages/stream"
)
async def artifact_turn_stream(
    knowledge_base_id: str,
    conversation_id: str,
    payload: ArtifactTurnInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Send a message to the Artifacts agent and hear it work: ``decision`` and ``action``
    events (and the skill's own) while it does, then ``done`` with how the turn ended, ``error``
    or ``stopped``. The run is the library's one output run: Stop and Follow work as for a
    build."""

    service = _service(request)
    try:
        title = service.artifact_conversation_title(
            knowledge_base_id, workspace_id, conversation_id, payload.text
        )
    except LookupError:
        title = "your artifacts"  # the run itself answers 404
    return _output_run(
        request,
        knowledge_base_id,
        payload.text,
        lambda hear: service.artifact_turn(
            knowledge_base_id, workspace_id, conversation_id, payload, hear
        ),
        (f"Working on {title}", "Done", "Couldn't finish"),
        service.library_title(knowledge_base_id),
        result=lambda turn: turn.model_dump(mode="json", by_alias=True),
    )


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations",
    response_model=list[ArtifactConversationSummaryOut],
)
def artifact_conversations(
    knowledge_base_id: str, request: Request, workspace_id: WorkspaceHeader
) -> list[ArtifactConversationSummaryOut]:
    """The Artifacts conversations of a library, the latest first."""

    with _artifact_http_errors(ArtifactConflictError):
        return _service(request).artifact_conversations(knowledge_base_id, workspace_id)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations",
    response_model=ArtifactConversationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_artifact_conversation(
    knowledge_base_id: str,
    payload: CreateArtifactConversationInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactConversationOut:
    """Start an Artifacts conversation, under the id given (a device's keeps its own)."""

    with _artifact_http_errors(ValueError):
        return _service(request).create_artifact_conversation(
            knowledge_base_id, workspace_id, payload
        )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/import",
    response_model=list[ArtifactConversationSummaryOut],
)
def import_artifact_conversations(
    knowledge_base_id: str,
    payload: ImportArtifactConversationsInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> list[ArtifactConversationSummaryOut]:
    """Take in the conversations a device kept. Ids that exist are skipped."""

    with _artifact_http_errors(ValueError):
        return _service(request).import_artifact_conversations(
            knowledge_base_id, workspace_id, payload
        )


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}",
    response_model=ArtifactConversationOut,
)
def artifact_conversation(
    knowledge_base_id: str,
    conversation_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactConversationOut:
    """One Artifacts conversation: its messages, brief and works."""

    with _artifact_http_errors(ArtifactConflictError):
        return _service(request).artifact_conversation(
            knowledge_base_id, workspace_id, conversation_id
        )


@router.patch(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}",
    response_model=ArtifactConversationOut,
)
def update_artifact_conversation(
    knowledge_base_id: str,
    conversation_id: str,
    payload: UpdateArtifactConversationInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactConversationOut:
    """Rename an Artifacts conversation, change what it reads from, or archive it."""

    with _artifact_http_errors(ValueError):
        return _service(request).update_artifact_conversation(
            knowledge_base_id, workspace_id, conversation_id, payload
        )


@router.patch(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/brief",
    response_model=ArtifactBriefStateOut,
)
def edit_artifact_brief(
    knowledge_base_id: str,
    conversation_id: str,
    payload: EditArtifactBriefInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactBriefStateOut:
    """Set the lines the user typed in the Brief panel; they are marked as theirs."""

    with _artifact_http_errors(ValueError):
        return _service(request).edit_artifact_brief(
            knowledge_base_id, workspace_id, conversation_id, payload
        )
