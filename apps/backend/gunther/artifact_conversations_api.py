"""Artifact conversation lifecycle, briefs and turn streams."""

from fastapi import APIRouter, Request, status
from fastapi.responses import Response, StreamingResponse

from gunther.answer_runs import Stopped
from gunther.api_common import _service, _sse
from gunther.artifact_api_common import (
    WorkspaceHeader,
    _artifact_http_errors,
    _conversation_run,
    _output_error,
    _output_event,
    _output_run,
)
from gunther.artifact_plans import PlanConflictError
from gunther.artifact_read import ArtifactIntegrityError
from gunther.artifact_versions import ArtifactConflictError
from gunther.schemas import (
    ApprovePlanTurnInput,
    ArtifactBriefStateOut,
    ArtifactConversationOut,
    ArtifactConversationSummaryOut,
    ArtifactTurnInput,
    CreateArtifactConversationInput,
    EditArtifactBriefInput,
    ImportArtifactConversationsInput,
    InitialPlanTurnInput,
    SavedArtifactTurnInput,
    UpdateArtifactConversationInput,
    WorkPlanPatchInput,
)

turn_router = APIRouter()
router = APIRouter()


@turn_router.post(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/messages/stream"
)
def artifact_turn_stream(
    knowledge_base_id: str,
    conversation_id: str,
    payload: ArtifactTurnInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Send a turn and hear its progress, followed by ``done``, ``error`` or ``stopped``."""

    service = _service(request)
    command = payload.root
    initial = command if isinstance(command, InitialPlanTurnInput) else None
    saved = command if isinstance(command, SavedArtifactTurnInput) else None
    plan_id = command.plan_id if saved is None else None
    text = getattr(command, "text", "") or (initial.setup.focus if initial else "")
    try:
        earlier = service.artifact_turn_replay(
            knowledge_base_id, workspace_id, conversation_id, payload,
        )
        if earlier is not None:
            return _output_event("done", earlier.model_dump(mode="json", by_alias=True))
        if saved and saved.build and saved.target_lineage_id is None:
            raise PlanConflictError(
                "plan_required", "Plan this output in Studio and choose Approve & Generate.",
            )
        title = service.artifact_conversation_title(
            knowledge_base_id, workspace_id, conversation_id, text, initial=initial,
        )
        about = service.library_title(knowledge_base_id)
    except Stopped:
        return _output_event("stopped", {"type": "stopped"})
    except (ArtifactIntegrityError, LookupError, ValueError) as error:
        return _output_error(error)
    if isinstance(command, ApprovePlanTurnInput):
        words = (f"Generating {title}", "Output ready", "Couldn't generate")
    elif plan_id is not None:
        words = (f"Planning {title}", "Plan updated", "Couldn't plan")
    else:
        words = (f"Working on {title}", "Done", "Couldn't finish")
    return _output_run(
        request,
        workspace_id,
        knowledge_base_id,
        text or words[0],
        lambda hear, run: service.artifact_turn(
            knowledge_base_id, workspace_id, conversation_id, payload, hear, run=run,
        ),
        words,
        about,
        conversation_id=conversation_id,
        type_id=(
            initial.setup.type if initial else saved.build.type if saved and saved.build else None
        ),
        lineage_id=saved.target_lineage_id if saved else None,
        plan_id=plan_id,
        request_id=command.client_request_id,
    )


@router.get("/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/stream")
async def follow_artifact_turn(
    knowledge_base_id: str, conversation_id: str, request: Request, workspace_id: WorkspaceHeader,
) -> Response:
    """Replay and follow the live run of this conversation, if one is running."""

    run = _conversation_run(request, workspace_id, knowledge_base_id, conversation_id)
    return _sse(run, resumed=True) if run is not None else Response(status_code=204)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/stop",
    status_code=204,
)
def stop_artifact_turn(
    knowledge_base_id: str, conversation_id: str, request: Request, workspace_id: WorkspaceHeader,
) -> Response:
    """Ask this conversation's current run to stop."""

    run = _conversation_run(request, workspace_id, knowledge_base_id, conversation_id)
    if run is not None:
        run.stop()
    return Response(status_code=204)


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


@router.patch(
    "/knowledge-bases/{knowledge_base_id}/artifact-conversations/{conversation_id}/works/{work_key}",
    response_model=ArtifactConversationOut,
)
def patch_artifact_work(
    knowledge_base_id: str,
    conversation_id: str,
    work_key: str,
    payload: WorkPlanPatchInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactConversationOut:
    with _artifact_http_errors(ValueError):
        return _service(request).patch_artifact_work(
            knowledge_base_id, workspace_id, conversation_id, work_key, payload,
        )
