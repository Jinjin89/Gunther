"""Commit a sealed artifact version, including its pinned inputs and check snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from gunther import key_result_origins
from gunther.artifact_manifest import artifact_manifest_hash
from gunther.artifact_read import artifact_detail, latest_check_records
from gunther.database import session_scope
from gunther.knowledge_index import new_id
from gunther.models import (
    Artifact,
    ArtifactAsset,
    ArtifactCheck,
    ArtifactConversation,
    ArtifactUnitBinding,
    DocumentElement,
    ElementAsset,
    KnowledgeBaseRecord,
    WorkspaceIdentity,
)
from gunther.output_types import output_type_for
from gunther.schemas import (
    ArtifactOut,
    ArtifactUnitSnapshotOut,
    OutputModelOut,
    OutputProvenanceOut,
)

# The largest an output's text may be, in bytes.
MAX_OUTPUT_BYTES = 2_500_000


class ArtifactConflictError(ValueError):
    """An idempotency key or immutable lineage head conflicts with current state."""


def earlier_output(
    sessions: sessionmaker[Session],
    workspace_id: str,
    client_request_id: str,
    fingerprint: str,
    *,
    previous: Sequence[str] = (),
) -> ArtifactOut | None:
    """The version an earlier try of this same request already saved, if there is one."""

    with session_scope(sessions) as session:
        existing = session.scalar(
            select(Artifact).where(
                Artifact.workspace_id == workspace_id,
                Artifact.client_request_id == client_request_id,
            )
        )
        if existing is None:
            return None
        if existing.request_fingerprint not in (fingerprint, *previous):
            raise ArtifactConflictError(
                "clientRequestId was already used for a different Artifact request"
            )
        if existing.trashed_at is not None:
            raise LookupError(f"Artifact {existing.id} was not found")
        output_library(session, workspace_id, existing.knowledge_base_id)
        return artifact_detail(session, existing)


def output_library(session: Session, workspace_id: str, base_id: str) -> KnowledgeBaseRecord:
    workspace = session.get(WorkspaceIdentity, "primary")
    if workspace is None or workspace.workspace_id != workspace_id:
        raise LookupError("The verified workspace identity was not found")
    library = session.get(KnowledgeBaseRecord, base_id)
    if library is None or library.trashed_at is not None:
        raise LookupError(f"Knowledge Base {base_id} was not found")
    return library


def lineage_head(
    session: Session, workspace_id: str, base_id: str, lineage_id: str,
) -> Artifact | None:
    return session.scalar(
        select(Artifact)
        .where(
            Artifact.lineage_id == lineage_id,
            Artifact.workspace_id == workspace_id,
            Artifact.knowledge_base_id == base_id,
            Artifact.trashed_at.is_(None),
        )
        .order_by(Artifact.version_number.desc())
        .limit(1)
    )


def output_version(
    session: Session, workspace_id: str, base_id: str, artifact_id: str, *, latest: bool
) -> Artifact:
    """A version of an output. With ``latest``, only the newest of its lineage will do:
    that is the one that can be built on."""

    found = session.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.workspace_id == workspace_id,
            Artifact.knowledge_base_id == base_id,
            Artifact.trashed_at.is_(None),
        )
    )
    if found is None:
        raise LookupError(f"Artifact {artifact_id} was not found in this Knowledge Base")
    if latest:
        head = lineage_head(session, workspace_id, base_id, found.lineage_id)
        if head is None or head.id != found.id:
            raise ArtifactConflictError(
                "That is not the latest version of this output. Open the latest one."
            )
    return found


def commit_version(
    sessions: sessionmaker[Session],
    *,
    workspace_id: str,
    base_id: str,
    client_request_id: str,
    fingerprint: str,
    parent_id: str | None,
    kind: str,
    style: str | None,
    audience: str,
    title: str,
    brief: str,
    origin: str,
    content: str,
    scope: dict[str, object],
    inputs: dict[str, object],
    outline: list[dict[str, object]],
    citations: list[dict[str, object]],
    snapshots: list[ArtifactUnitSnapshotOut],
    model: OutputModelOut | None,
    checks: list[dict[str, object]],
    made_by: dict[str, object] | None = None,
    publish: Callable[[Session, ArtifactOut], None] | None = None,
) -> ArtifactOut:
    """Write a new version of an output, sealed with its hashes, and what the Checker found.

    A conversation publishes its work and turn in the same transaction as the version.
    """

    try:
        with session_scope(sessions) as session:
            # Serialize the lineage eligibility check with a concurrent move to Trash.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            library = output_library(session, workspace_id, base_id)
            if parent_id:
                parent = output_version(session, workspace_id, base_id, parent_id, latest=True)
                lineage_id, version_number = parent.lineage_id, parent.version_number + 1
            else:
                parent, lineage_id, version_number = None, new_id("arl"), 1
            if len(content.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise ValueError(f"The output is larger than the {MAX_OUTPUT_BYTES:,}-byte limit")
            skilled = made_by or {}
            if kind == "slides" and isinstance(skilled.get("skill"), dict):
                from gunther.slide_review import invalidate_reviews

                skilled = {
                    **skilled,
                    "skill": invalidate_reviews(
                        skilled["skill"], content, outline, citations, skilled.get("use")
                    ),
                }
            parent_provenance = json.loads(parent.provenance_json) if parent else {}
            style = output_type_for(kind, style).id
            conversation_id = skilled.get("conversation_id") or parent_provenance.get(
                "conversation_id"
            )
            if (
                parent_provenance.get("conversation_id")
                and conversation_id != parent_provenance["conversation_id"]
            ):
                raise ValueError("This artifact belongs to a different conversation.")
            provenance = OutputProvenanceOut(
                schema_version=2,
                generator=str(skilled.get("generator") or "gunther.output-agents.v1"),
                workspace_id=workspace_id,
                knowledge_base_id=base_id,
                knowledge_base_question=library.question,
                accepted_only=False,
                accepted_unit_ids=[item.unit_id for item in snapshots],
                revision_ids=[item.revision_id for item in snapshots],
                kind=kind,
                style=style,
                audience=audience,
                brief=brief,
                origin=origin,
                model=model,
                skill=skilled.get("skill"),
                approach=skilled.get("approach"),
                use=skilled.get("use"),
                length=skilled.get("length"),
                language=skilled.get("language"),
                conversation_id=conversation_id,
                conversation_title=skilled.get("conversation_title")
                or parent_provenance.get("conversation_title"),
                request=skilled.get("request") if conversation_id else None,
                reply=skilled.get("reply") if conversation_id else None,
                changes=skilled.get("changes"),
            )
            if provenance.conversation_id:
                conversation = session.get(ArtifactConversation, provenance.conversation_id)
                if conversation is not None and conversation.trashed_at is not None:
                    raise LookupError("The output's conversation was not found")
            sealed = provenance.model_dump()
            if skilled.get("work") is not None:
                # The artifact's work as it was made: sealed with the version, not sent out.
                sealed["work"] = skilled["work"]
            for key in (
                "skill",
                "approach",
                "use",
                "length",
                "language",
                "conversation_id",
                "conversation_title",
                "request",
                "reply",
                "changes",
            ):
                # A version made without a skill keeps the provenance it always had.
                if sealed.get(key) is None:
                    sealed.pop(key, None)
            artifact = Artifact(
                id=new_id("art"),
                workspace_id=workspace_id,
                knowledge_base_id=base_id,
                client_request_id=client_request_id,
                request_fingerprint=fingerprint,
                lineage_id=lineage_id,
                version_number=version_number,
                supersedes_artifact_id=parent.id if parent else None,
                format=kind,
                audience=audience,
                title=(title or library.title)[:160],
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                manifest_hash="",
                accepted_unit_ids_json=json.dumps(
                    [item.unit_id for item in snapshots],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                revision_snapshot_json=json.dumps(
                    [item.model_dump() for item in snapshots],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                provenance_json=json.dumps(
                    sealed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                kind=kind,
                style=style,
                brief=brief,
                origin=origin,
                scope_json=json.dumps(scope, ensure_ascii=False, separators=(",", ":")),
                inputs_json=json.dumps(inputs, ensure_ascii=False, separators=(",", ":")),
                outline_json=json.dumps(outline, ensure_ascii=False, separators=(",", ":")),
                citations_json=json.dumps(citations, ensure_ascii=False, separators=(",", ":")),
            )
            artifact.manifest_hash = artifact_manifest_hash(artifact.__getattribute__)
            session.add(artifact)
            session.flush()
            cited_revisions = [
                c.get("source_revision_id") or c.get("sourceRevisionId") for c in citations
            ]
            retained_images = set(
                session.scalars(
                    select(ElementAsset.asset_id)
                    .join(DocumentElement)
                    .where(DocumentElement.revision_id.in_([r for r in cited_revisions if r]))
                )
            )
            requested_images = set(re.findall(r"gunther-asset:([A-Za-z0-9_-]+)", content))
            if requested_images - retained_images:
                raise ValueError("An output figure must belong to its cited materials.")
            for asset_id in retained_images:
                session.add(ArtifactAsset(artifact_id=artifact.id, asset_id=asset_id))
            for position, item in enumerate(snapshots):
                session.add(
                    ArtifactUnitBinding(
                        id=new_id("aub"),
                        artifact_id=artifact.id,
                        position=position,
                        unit_id=item.unit_id,
                        revision_id=item.revision_id,
                        content_hash=item.content_hash,
                    )
                )
            if checks:
                session.add(
                    ArtifactCheck(
                        id=new_id("chk"),
                        artifact_id=artifact.id,
                        content_hash=artifact.content_hash,
                        checks_json=json.dumps({"sections": checks}, ensure_ascii=False),
                    )
                )
            session.flush()
            records = latest_check_records(session, artifact)
            key_result_origins.enqueue_output(session, artifact, records)
            shown = artifact_detail(session, artifact, check_records=records)
            if publish is not None:
                publish(session, shown)
            return shown
    except (IntegrityError, ArtifactConflictError) as error:
        earlier = earlier_output(sessions, workspace_id, client_request_id, fingerprint)
        if earlier is not None:
            return earlier
        if isinstance(error, ArtifactConflictError):
            raise
        raise ArtifactConflictError(
            "Artifact version creation conflicted with another writer; refresh and retry"
        ) from error
