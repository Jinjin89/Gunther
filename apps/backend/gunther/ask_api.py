"""Ask sessions, briefs, skills and streaming answer routes."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

from gunther.answer_runs import AnswerRun, Stopped
from gunther.api_common import _answer_runs, _service, _sse
from gunther.ask_turns import AskFailure
from gunther.brief import BriefEditInput
from gunther.schemas import (
    AskSkillOut,
    BriefOut,
    CreateKnowledgeSessionInput,
    CreateSessionMessageInput,
    FileSessionInput,
    KnowledgeSessionOut,
    KnowledgeSessionSummaryOut,
    UpdateKnowledgeSessionInput,
)
from gunther.skillbook import ask_skills

router = APIRouter()


@router.get(
    "/knowledge-bases/{knowledge_base_id}/sessions",
    response_model=list[KnowledgeSessionSummaryOut],
)
def knowledge_sessions(
    knowledge_base_id: str,
    request: Request,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
) -> list[KnowledgeSessionSummaryOut]:
    return _service(request).list_knowledge_sessions(
        knowledge_base_id, include_archived=include_archived
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/sessions",
    response_model=KnowledgeSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_session(
    knowledge_base_id: str,
    payload: CreateKnowledgeSessionInput,
    request: Request,
) -> KnowledgeSessionOut:
    try:
        return _service(request).create_knowledge_session(knowledge_base_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sessions/{session_id}", response_model=KnowledgeSessionOut)
def knowledge_session(session_id: str, request: Request) -> KnowledgeSessionOut:
    try:
        return _service(request).get_knowledge_session(session_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/messages/{message_id}/branch",
    response_model=KnowledgeSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def branch_knowledge_session(
    session_id: str, message_id: str, request: Request
) -> KnowledgeSessionOut:
    try:
        return _service(request).branch_knowledge_session(session_id, message_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/sessions/{session_id}", response_model=KnowledgeSessionSummaryOut)
def update_knowledge_session(
    session_id: str,
    payload: UpdateKnowledgeSessionInput,
    request: Request,
) -> KnowledgeSessionSummaryOut:
    try:
        return _service(request).update_knowledge_session(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/ask/skills", response_model=list[AskSkillOut])
def list_ask_skills() -> list[AskSkillOut]:
    """The skills the composer's / menu offers."""

    return [
        AskSkillOut(
            command=skill.command,
            title=skill.title,
            description=skill.description,
            budgets=list(skill.budgets),
        )
        for skill in ask_skills().values()
    ]


@router.post("/sessions/{session_id}/brief/refresh", response_model=BriefOut)
def refresh_session_brief(session_id: str, request: Request) -> BriefOut:
    """Bring the conversation's brief up to date with its last answer."""

    try:
        return _service(request).refresh_brief(session_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/sessions/{session_id}/brief", response_model=BriefOut)
def edit_session_brief(session_id: str, payload: BriefEditInput, request: Request) -> BriefOut:
    try:
        return _service(request).edit_brief(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/file", response_model=KnowledgeSessionSummaryOut)
def file_home_session(
    session_id: str, payload: FileSessionInput, request: Request
) -> KnowledgeSessionSummaryOut:
    try:
        return _service(request).file_home_session(session_id, payload.knowledge_base_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/messages/stream")
async def stream_session_message(
    session_id: str,
    payload: CreateSessionMessageInput,
    request: Request,
) -> StreamingResponse:
    """Answer a posted message and send events as the work progresses.

    ``step`` and ``text`` events report the agent at work; ``done`` carries
    the saved turn, ``error`` says why there is none, and ``stopped`` follows
    a stop. Closing the connection only stops listening: the answer is still
    written and saved, and ``GET .../answer/stream`` follows it again. Only
    ``POST .../answer/stop`` ends it early, keeping the question marked stopped.
    """

    service = _service(request)
    researching = (payload.skill or "").strip().lstrip("/") == "research"
    activity = request.app.state.activity
    runs = _answer_runs(request)
    library_id = service.session_library(session_id)
    question = payload.content.strip()
    if len(question) > 80:
        question = question[:80] + "…"
    where = (
        {"view": "home", "sessionId": session_id}
        if library_id == "@home"
        else {"view": "library", "libraryId": library_id, "tab": "ask", "sessionId": session_id}
    )

    def work(run: AnswerRun) -> None:
        writing = False
        # A conversation that isn't there is answered 404 below, with nothing to show.
        tracked = None
        if library_id:
            tracked = run.track_activity(
                activity.track(
                    f"answer:{session_id}",
                    "answer",
                    "Writing an answer",
                    question,
                    open=where,
                    stop=lambda: runs.stop(session_id),
                    detail="Thinking",
                ),
                done="Answer ready",
                failed="Couldn't write the answer",
            )

        def fail(status: int, detail: str, question_id: str | None = None) -> None:
            data: dict[str, int | str] = {"status": status, "detail": detail}
            if question_id is not None:
                data["questionId"] = question_id
            run.publish("error", data)

        def hear(event: dict) -> None:
            nonlocal writing
            if tracked is not None:
                label = event.get("label")
                if event["type"] == "step" and event.get("state") == "running" and label:
                    tracked.update(detail=label)
                elif event["type"] == "text" and not writing:
                    tracked.update(detail="Writing")
            # Research keeps what it found when stopped before it writes (see ``stopping``);
            # every other answer is dropped.
            if run.stop_requested.is_set():
                if writing or not researching:
                    raise Stopped
                if event["type"] == "text":
                    # Too late to end gathering: the answer is already being written.
                    run.stop_requested.clear()
            if event["type"] == "text":
                writing = True
            if event["type"] != "saving":
                run.publish(event["type"], event)

        def stopping() -> bool:
            """Research: the first Stop ends gathering. It is used up, so a second Stop,
            while the answer is being written, drops the answer as for any other."""
            if writing or not run.stop_requested.is_set():
                return False
            run.stop_requested.clear()
            return True

        try:
            turn = (
                service.create_session_turn(session_id, payload, hear, stopping)
                if researching
                else service.create_session_turn(session_id, payload, hear)
            )
            run.publish("done", turn.model_dump(mode="json", by_alias=True))
        except Stopped:
            service.record_interrupted_question(session_id, payload, "stopped")
            run.publish("stopped", {"type": "stopped"})
        except AskFailure as error:
            question_id = service.record_interrupted_question(session_id, payload, "failed", error)
            fail(error.status, str(error), question_id)
        except LookupError as error:
            fail(404, str(error))
        except ValueError as error:
            fail(400, str(error))
        except Exception:
            from gunther.api import logger

            logger.exception("Answering a question failed")
            error = AskFailure("The answer could not be written.", 500)
            question_id = service.record_interrupted_question(session_id, payload, "failed", error)
            fail(error.status, str(error), question_id)

    run = _answer_runs(request).start(session_id, payload.content, work)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Gunther is still answering the last question here. Wait, or stop it first.",
        )
    return _sse(run)


@router.get("/sessions/{session_id}/answer/stream", response_model=None)
async def follow_session_answer(session_id: str, request: Request) -> Response:
    """Follow an answer still being written: what it did so far, then live. 204 when none is."""

    run = _answer_runs(request).get(session_id)
    if run is None or run.finished:
        return Response(status_code=204)
    return _sse(run, resumed=True)


@router.post("/sessions/{session_id}/answer/stop", status_code=204)
def stop_session_answer(session_id: str, request: Request) -> Response:
    """Stop the answer being written; the question stays in the conversation, marked."""

    _answer_runs(request).stop(session_id)
    return Response(status_code=204)
