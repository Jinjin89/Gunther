"""Verify immutable artifact data and project saved versions for readers."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther.artifact_manifest import artifact_manifest_hash
from gunther.checks import complete, coverage_of, issue_view, read_verdicts
from gunther.models import Artifact, ArtifactCheck, ArtifactUnitBinding, KnowledgeUnitRevision
from gunther.outputs import _marked, heading_of, section_hash, split_document
from gunther.schemas import (
    ArtifactOut,
    ArtifactProvenanceOut,
    ArtifactSummaryOut,
    ArtifactUnitSnapshotOut,
    ConversationCitationOut,
    OutlineItem,
    OutputInputsOut,
    OutputIssueOut,
    OutputProvenanceOut,
    OutputScope,
    OutputSectionOut,
)


class ArtifactIntegrityError(RuntimeError):
    """Stored immutable Artifact bytes or provenance no longer match their hashes."""


def _verified_payload(
    session: Session, artifact: Artifact
) -> tuple[list[ArtifactUnitSnapshotOut], ArtifactProvenanceOut, dict[str, Any]]:
    try:
        accepted_unit_ids = json.loads(artifact.accepted_unit_ids_json)
        snapshots = [
            ArtifactUnitSnapshotOut.model_validate(item)
            for item in json.loads(artifact.revision_snapshot_json)
        ]
        saved = json.loads(artifact.provenance_json)
        # Version 2 is an output built by agents; version 1 is the old builder's.
        provenance: ArtifactProvenanceOut = (
            OutputProvenanceOut if saved.get("schema_version") == 2 else ArtifactProvenanceOut
        ).model_validate(saved)
    except Exception as error:
        raise ArtifactIntegrityError(
            f"Artifact {artifact.id} contains invalid immutable provenance"
        ) from error
    agents = isinstance(provenance, OutputProvenanceOut)

    content_hash = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(content_hash, artifact.content_hash):
        raise ArtifactIntegrityError(f"Artifact {artifact.id} content failed its integrity check")
    if (
        not isinstance(accepted_unit_ids, list)
        or accepted_unit_ids != [snapshot.unit_id for snapshot in snapshots]
        or provenance.workspace_id != artifact.workspace_id
        or provenance.knowledge_base_id != artifact.knowledge_base_id
        or provenance.accepted_unit_ids != accepted_unit_ids
        or provenance.revision_ids != [snapshot.revision_id for snapshot in snapshots]
        # The old builder used accepted knowledge only; agents read sources as well.
        or provenance.accepted_only == agents
    ):
        raise ArtifactIntegrityError(
            f"Artifact {artifact.id} provenance failed its integrity check"
        )
    if isinstance(provenance, OutputProvenanceOut) and (
        provenance.kind != artifact.kind
        or artifact.format != artifact.kind
        or provenance.style != artifact.style
        or provenance.audience != artifact.audience
        or provenance.brief != artifact.brief
        or provenance.origin != artifact.origin
    ):
        raise ArtifactIntegrityError(
            f"Artifact {artifact.id} provenance failed its integrity check"
        )

    bindings = session.scalars(
        select(ArtifactUnitBinding)
        .where(ArtifactUnitBinding.artifact_id == artifact.id)
        .order_by(ArtifactUnitBinding.position)
    ).all()
    if len(bindings) != len(snapshots):
        raise ArtifactIntegrityError(
            f"Artifact {artifact.id} binding count failed its integrity check"
        )
    for position, (snapshot, binding) in enumerate(zip(snapshots, bindings, strict=True)):
        snapshot_hash = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
        revision = session.get(KnowledgeUnitRevision, snapshot.revision_id)
        if (
            not secrets.compare_digest(snapshot_hash, snapshot.content_hash)
            or binding.position != position
            or binding.unit_id != snapshot.unit_id
            or binding.revision_id != snapshot.revision_id
            or not secrets.compare_digest(binding.content_hash, snapshot.content_hash)
            or revision is None
            or revision.unit_id != snapshot.unit_id
            or revision.revision_number != snapshot.revision_number
            or not secrets.compare_digest(
                hashlib.sha256(revision.content.encode("utf-8")).hexdigest(),
                snapshot.content_hash,
            )
        ):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} pinned revision failed its integrity check"
            )

    try:
        manifest_hash = artifact_manifest_hash(artifact.__getattribute__)
    except (TypeError, ValueError) as error:
        raise ArtifactIntegrityError(
            f"Artifact {artifact.id} manifest contains invalid JSON"
        ) from error
    if not secrets.compare_digest(manifest_hash, artifact.manifest_hash):
        raise ArtifactIntegrityError(f"Artifact {artifact.id} manifest failed its integrity check")
    return snapshots, provenance, saved


def latest_check_records(session: Session, artifact: Artifact) -> dict[str, dict]:
    """Read the latest check once and decode saved verdicts at the storage boundary."""

    row = session.scalar(
        select(ArtifactCheck)
        .where(ArtifactCheck.artifact_id == artifact.id)
        .order_by(ArtifactCheck.created_at.desc(), ArtifactCheck.id.desc())
        .limit(1)
    )
    if row is None:
        return {}
    records = json.loads(row.checks_json).get("sections", [])
    texts = {}
    if any("verdicts" not in record for record in records):
        texts = {
            section_hash(text): _marked(text).body
            for text in split_document(artifact.content, artifact.kind)[1]
        }
    return {
        record["hash"]: {
            "hash": record["hash"],
            "verdicts": read_verdicts(record, texts.get(record["hash"], "")),
        }
        for record in records
    }


def _output_sections(artifact: Artifact, records: dict[str, dict]) -> list[OutputSectionOut]:
    sections = []
    for index, text in enumerate(split_document(artifact.content, artifact.kind)[1]):
        digest = section_hash(text)
        state = records.get(digest, {}).get("verdicts")
        sections.append(
            OutputSectionOut(
                index=index,
                heading=heading_of(text),
                checked=state is not None and complete(state),
                issues=[OutputIssueOut(**issue) for issue in issue_view(state or {})],
                coverage=coverage_of(state, digest) if state is not None else None,
            )
        )
    return sections


def _summary(
    artifact: Artifact, accepted_unit_ids: list[str], saved: dict[str, Any]
) -> ArtifactSummaryOut:
    return ArtifactSummaryOut(
        id=artifact.id,
        workspace_id=artifact.workspace_id,
        knowledge_base_id=artifact.knowledge_base_id,
        lineage_id=artifact.lineage_id,
        version_number=artifact.version_number,
        supersedes_artifact_id=artifact.supersedes_artifact_id,
        format=artifact.format,
        audience=artifact.audience,
        title=artifact.title,
        content_hash=artifact.content_hash,
        manifest_hash=artifact.manifest_hash,
        accepted_unit_ids=accepted_unit_ids,
        unit_count=len(accepted_unit_ids),
        created_at=f"{artifact.created_at.isoformat(timespec='milliseconds')}Z",
        kind=artifact.kind,
        style=artifact.style,
        origin=artifact.origin,
        conversation_id=saved.get("conversation_id"),
        conversation_title=saved.get("conversation_title"),
        client_request_id=artifact.client_request_id or None,
        request=saved.get("request"),
    )


def artifact_summary(session: Session, artifact: Artifact) -> ArtifactSummaryOut:
    _, provenance, saved = _verified_payload(session, artifact)
    return _summary(artifact, provenance.accepted_unit_ids, saved)


def artifact_detail(
    session: Session, artifact: Artifact, *, check_records: dict[str, dict] | None = None
) -> ArtifactOut:
    snapshots, provenance, saved = _verified_payload(session, artifact)
    summary = _summary(artifact, provenance.accepted_unit_ids, saved)
    scope = json.loads(artifact.scope_json)
    inputs = json.loads(artifact.inputs_json)
    sections = []
    if artifact.origin != "legacy":
        if check_records is None:
            check_records = latest_check_records(session, artifact)
        sections = _output_sections(artifact, check_records)
    return ArtifactOut(
        **summary.model_dump(),
        content=artifact.content,
        unit_snapshots=snapshots,
        provenance=provenance,
        brief=artifact.brief,
        outline=[
            OutlineItem.model_validate(item) for item in json.loads(artifact.outline_json)
        ],
        citations=[
            ConversationCitationOut.model_validate(item)
            for item in json.loads(artifact.citations_json)
        ],
        scope=OutputScope.model_validate(scope) if scope else None,
        inputs=OutputInputsOut(
            sources=len(inputs.get("sources", [])),
            units=len(inputs.get("units", [])),
            sessions=len(inputs.get("sessions", [])),
        ),
        model_label=(
            provenance.model.label
            if isinstance(provenance, OutputProvenanceOut) and provenance.model
            else None
        ),
        sections=sections,
    )
