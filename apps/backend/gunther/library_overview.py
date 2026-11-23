"""Write an empty Chat's overview from the library's current source material."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from gunther import usage
from gunther.document_structure import evidence_eligible
from gunther.knowledge_index import new_id
from gunther.llm import ModelError, ModelGateway
from gunther.models import (
    ContentBlock,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    LibraryOverview,
    ProcessingJob,
    Source,
    SourceDigest,
    SourceIndexHead,
    SourcePaper,
    utc_now,
)
from gunther.schemas import ApiModel

logger = logging.getLogger(__name__)
KIND = "library_overview"
# Fallback sources contribute a short passage, in roughly a paragraph's space.
SNIPPET_CHARS = 900
INSTRUCTIONS = """Write a short library overview and up to three questions the sources can answer.
Say only what the supplied source material supports.
Example: given a note that the garden is watered on Tuesdays, write
{"summary":"The garden notes record a Tuesday watering schedule.",
 "suggested_questions":["When is the garden watered?"]}."""


class _Overview(BaseModel):
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=700)]
    suggested_questions: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=220)]
    ] = Field(max_length=3)


class LibraryOverviewOut(ApiModel):
    state: Literal["empty", "writing", "ready", "needs_model", "failed"]
    summary: str = ""
    suggested_questions: list[str] = Field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class Material:
    fingerprint: str
    text: str


def live_library(session: Session, library_id: str) -> KnowledgeBaseRecord | None:
    library = session.get(KnowledgeBaseRecord, library_id)
    return library if library and library.trashed_at is None else None


def material(session: Session, library_id: str) -> Material | None:
    leading = (
        select(func.substr(ContentBlock.content, 1, SNIPPET_CHARS))
        .where(
            ContentBlock.revision_id == SourceIndexHead.revision_id,
            evidence_eligible(ContentBlock.kind, ContentBlock.payload_json),
            func.length(func.trim(ContentBlock.content)) > 0,
        )
        .order_by(ContentBlock.ordinal)
        .limit(1)
        .correlate(SourceIndexHead)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            Source.id, Source.title, Source.kind, SourceDigest.overview, SourcePaper.abstract,
            func.coalesce(leading, func.substr(Source.content, 1, SNIPPET_CHARS)),
        )
        .select_from(Source)
        .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
        .outerjoin(SourceIndexHead, SourceIndexHead.source_id == Source.id)
        .outerjoin(SourceDigest, and_(
            SourceDigest.source_id == Source.id,
            SourceDigest.revision_id == SourceIndexHead.revision_id,
        ))
        .outerjoin(SourcePaper, and_(
            SourcePaper.source_id == Source.id,
            SourcePaper.revision_id == SourceIndexHead.revision_id,
        ))
        .where(
            KnowledgeBaseSource.knowledge_base_id == library_id,
            Source.trashed_at.is_(None),
        )
        .order_by(Source.id)
    )
    inputs = [
        {
            "id": source_id,
            "title": title,
            "kind": kind,
            "text": next(
                (value.strip() for value in (digest, abstract, snippet) if value and value.strip()),
                "",
            ),
        }
        for source_id, title, kind, digest, abstract, snippet in rows
    ]
    if not any(item["text"] for item in inputs):
        return None
    text = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    return Material(hashlib.sha256(text.encode()).hexdigest(), text)


def job_key(library_id: str, fingerprint: str) -> str:
    return f"{KIND}:{library_id}:{fingerprint}"


def request_overview(
    session: Session, library_id: str, *, has_model: bool, retry: bool
) -> LibraryOverviewOut:
    """Read the current result or queue one job under the caller's write transaction."""

    job = session.scalar(select(ProcessingJob).where(
        ProcessingJob.knowledge_base_id == library_id, ProcessingJob.kind == KIND,
    ))
    if job and job.state in {"queued", "running"}:
        return LibraryOverviewOut(state="writing")
    current = material(session, library_id)
    if current is None:
        return LibraryOverviewOut(state="empty")
    previous = session.get(LibraryOverview, library_id)
    matches = previous is not None and previous.source_fingerprint == current.fingerprint
    if previous is not None and matches and previous.error is None:
        return LibraryOverviewOut(
            state="ready", summary=previous.summary,
            suggested_questions=json.loads(previous.suggested_questions_json),
        )
    if not has_model:
        return LibraryOverviewOut(state="needs_model")
    key = job_key(library_id, current.fingerprint)
    if not retry:
        if job and job.state == "failed" and job.dedupe_key == key:
            return LibraryOverviewOut(state="failed", error=job.error)
        if previous is not None and matches:
            return LibraryOverviewOut(state="failed", error=previous.error)

    if job is None:
        job = ProcessingJob(
            id=new_id("job"), knowledge_base_id=library_id, kind=KIND,
        )
        session.add(job)
    job.dedupe_key = key
    job.state, job.attempts, job.error = "queued", 0, None
    job.lease_token, job.lease_until = None, None
    job.available_at = job.updated_at = utc_now()
    return LibraryOverviewOut(state="writing")


def write(
    gateway: ModelGateway | None, library_id: str, inputs: Material,
) -> tuple[_Overview | None, str, str | None] | None:
    chosen = gateway.for_role("analysis") if gateway else None
    if chosen is None:
        return None
    model, effort = chosen
    try:
        with usage.job("analysis", KIND, ref=library_id):
            result, _ = gateway.complete_json(
                model, _Overview, system=INSTRUCTIONS, prompt=inputs.text, effort=effort,
            )
        return result, model.method, None
    except Exception as error:
        if not isinstance(error, ModelError):
            logger.warning("Library overview model call failed (%s)", type(error).__name__)
        return None, model.method, "The Analysis model could not write the library overview."


def publish(
    session: Session, library_id: str, inputs: Material,
    result: _Overview | None, method: str, error: str | None,
) -> None:
    row = session.get(LibraryOverview, library_id)
    if row is None:
        row = LibraryOverview(knowledge_base_id=library_id)
        session.add(row)
    row.source_fingerprint = inputs.fingerprint
    row.summary = result.summary if result else ""
    row.suggested_questions_json = json.dumps(
        list(dict.fromkeys(result.suggested_questions)) if result else [], ensure_ascii=False,
    )
    row.method, row.error, row.updated_at = method, error, utc_now()
