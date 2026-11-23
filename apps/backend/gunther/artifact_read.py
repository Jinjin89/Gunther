"""Verify immutable artifact data and project saved versions for readers."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from gunther import key_results
from gunther.artifact_manifest import artifact_manifest_hash
from gunther.checks import complete, coverage_of, issue_view, read_verdicts
from gunther.database import session_scope
from gunther.models import (
    Artifact,
    ArtifactAudio,
    ArtifactCheck,
    ArtifactMessage,
    ArtifactUnitBinding,
    KnowledgeUnitRevision,
)
from gunther.output_shapes import SHAPES
from gunther.output_types import output_type_label
from gunther.outputs import _marked, document_units, heading_of, section_hash
from gunther.schemas import (
    ArtifactAudioOut,
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


def artifact_owner():
    """Read the owning conversation from the sealed provenance."""

    provenance = case(
        (func.json_valid(Artifact.provenance_json), Artifact.provenance_json), else_="{}",
    )
    return func.json_extract(provenance, "$.conversation_id")


def live_artifact_message():
    live = select(Artifact.id).where(Artifact.trashed_at.is_(None))
    return or_(ArtifactMessage.artifact_id.is_(None), ArtifactMessage.artifact_id.in_(live))


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
            section_hash(unit): _marked(unit.text).body
            for unit in document_units(artifact.content, artifact.kind)
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
    for index, unit in enumerate(document_units(artifact.content, artifact.kind)):
        digest = section_hash(unit)
        state = records.get(digest, {}).get("verdicts")
        sections.append(
            OutputSectionOut(
                index=index,
                heading=heading_of(unit),
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
        output_type=output_type_label(artifact.kind, artifact.style),
        origin=artifact.origin,
        conversation_id=saved.get("conversation_id"),
        conversation_title=saved.get("conversation_title"),
        client_request_id=artifact.client_request_id or None,
        request=saved.get("request"),
    )


def artifact_summary(session: Session, artifact: Artifact) -> ArtifactSummaryOut:
    _, provenance, saved = _verified_payload(session, artifact)
    return _summary(artifact, provenance.accepted_unit_ids, saved)


def _audio(session: Session, artifact: Artifact) -> ArtifactAudioOut | None:
    if not SHAPES[artifact.kind].speech:
        return None
    row = session.get(ArtifactAudio, artifact.id)
    if row is None or row.content_hash != artifact.content_hash:
        return ArtifactAudioOut(status="pending", content_hash=artifact.content_hash)
    return ArtifactAudioOut(
        status=row.status, content_hash=row.content_hash, audio_asset_id=row.asset_id,
        voices=json.loads(row.voices_json), timings=json.loads(row.timings_json), error=row.error,
    )


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
        key_results=key_results.origin_states(session, "output", [artifact.id]).get(artifact.id),
        audio=_audio(session, artifact),
    )


class ArtifactReader:
    def list_artifacts(self, knowledge_base_id: str, workspace_id: str) -> list[ArtifactSummaryOut]:
        with session_scope(self.sessions) as session:
            if self._live_knowledge_base(session, knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            artifacts = session.scalars(
                select(Artifact)
                .where(
                    Artifact.knowledge_base_id == knowledge_base_id,
                    Artifact.workspace_id == workspace_id,
                    Artifact.trashed_at.is_(None),
                )
                .order_by(Artifact.created_at.desc(), Artifact.version_number.desc())
            ).all()
            return [artifact_summary(session, artifact) for artifact in artifacts]

    def get_artifact(
        self, knowledge_base_id: str, artifact_id: str, workspace_id: str
    ) -> ArtifactOut:
        with session_scope(self.sessions) as session:
            if self._live_knowledge_base(session, knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.workspace_id == workspace_id,
                    Artifact.knowledge_base_id == knowledge_base_id,
                    Artifact.trashed_at.is_(None),
                )
            )
            if artifact is None:
                raise LookupError(f"Artifact {artifact_id} was not found")
            shown = artifact_detail(session, artifact)
            skill = getattr(shown.provenance, "skill", None)
            if (skill and skill.render_check and skill.render_check.get("status") == "captured"
                    and self.slide_renderer
                    and not self.slide_renderer.current(skill.render_check)):
                reason = "The renderer or source figures changed after this review."
                skill.render_check = {**skill.render_check, "status": "stale", "reason": reason}
                if skill.visual_review:
                    skill.visual_review = {
                        **skill.visual_review, "status": "stale", "reason": reason}
            return shown

    @staticmethod
    def _require_agent_output(artifact: Artifact) -> None:
        if artifact.origin == "legacy":
            raise ValueError(
                "This version came from the old builder. Build a new output to change it."
            )

    @staticmethod
    def _reusable_figure_readings(session: Session, base_id: str) -> list[dict]:
        """Earlier Studio figure records in this library, checked by fingerprint before reuse."""

        found: list[dict] = []
        rows = session.scalars(
            select(Artifact.provenance_json)
            .where(
                Artifact.knowledge_base_id == base_id,
                Artifact.kind == "slides",
                Artifact.trashed_at.is_(None),
            )
            .order_by(Artifact.created_at.desc())
            .limit(30)
        )
        for row in rows:
            try:
                approach = (json.loads(row).get("approach") or {})
            except ValueError:
                continue
            found += approach.get("figure_readings") or approach.get("figureReadings") or []
        return found

    @staticmethod
    def _made_by(artifact: Artifact) -> dict[str, object] | None:
        """How a version was made by a skill, for the versions that follow it."""

        provenance = json.loads(artifact.provenance_json)
        skill = provenance.get("skill")
        if not isinstance(skill, dict):
            return None
        from gunther.schemas import SkillRunOut

        skill = SkillRunOut.model_validate(skill).model_dump(mode="json", by_alias=True)
        return {
            "generator": provenance.get("generator"),
            "skill": {"name": skill.get("name"), "version": skill.get("version"), "skipped": [],
                      **{key: skill[key] for key in ("renderCheck", "visualReview")
                         if key in skill}},
            "approach": provenance.get("approach"),
            "use": provenance.get("use"),
            "length": provenance.get("length"),
            "language": provenance.get("language"),
        }
