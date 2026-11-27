"""Commit a sealed artifact version, including its pinned inputs and check snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from gunther import artifact_workspace as workspaces
from gunther import key_result_origins
from gunther.answer_runs import AnswerRun, AnswerRuns, Stopped, output_key
from gunther.artifact_manifest import artifact_manifest_hash
from gunther.artifact_plans import PlanConflictError, plan_revision, project_work_plan
from gunther.artifact_read import artifact_detail, creation_plan_reference, latest_check_records
from gunther.database import session_scope
from gunther.knowledge_index import new_id
from gunther.models import (
    Artifact,
    ArtifactAsset,
    ArtifactAudio,
    ArtifactCheck,
    ArtifactConversation,
    ArtifactMessage,
    ArtifactUnitBinding,
    DocumentElement,
    ElementAsset,
    KnowledgeBaseRecord,
    WorkspaceIdentity,
)
from gunther.output_material import AssetReader, reviewed_studio_material, verify_studio_assets
from gunther.output_shapes import SHAPES
from gunther.output_types import output_type_for
from gunther.outputs import FirstWrite
from gunther.schemas import (
    ApprovedCreationPlanOut,
    ArtifactOut,
    ArtifactUnitSnapshotOut,
    ConversationCitationOut,
    OutputModelOut,
    OutputProvenanceOut,
    OutputScope,
    PlanPreferencesOut,
    VoicePair,
)

# The largest an output's text may be, in bytes.
MAX_OUTPUT_BYTES = 2_500_000


class ArtifactConflictError(ValueError):
    """An idempotency key or immutable lineage head conflicts with current state."""


class WorkPlanConflict(PlanConflictError):
    """A conflict with a scoped plan whose current identity can be returned."""

    def __init__(
        self, code: str, detail: str, work: workspaces.Work, *,
        revision: str, edit_version: int,
    ) -> None:
        super().__init__(code, detail)
        self.current = {
            "workKey": work.key, "planId": work.plan.id if work.plan else None,
            "planRevision": revision, "editVersion": edit_version,
        }


def matches_attempt(
    work: workspaces.Work, request_id: str, server_boot_id: str, attempt_id: str,
    phase: Literal["plan", "generate"],
) -> bool:
    attempt = work.plan.attempt if work.plan is not None else None
    return bool(
        attempt is not None and attempt.state == "running"
        and (attempt.request_id, attempt.server_boot_id, attempt.attempt_id, attempt.phase)
        == (request_id, server_boot_id, attempt_id, phase)
    )


@contextmanager
def locked_plan_run(
    runs: AnswerRuns, conversation_id: str, scope: tuple[str, str], *,
    server_boot_id: str, attempt_id: str,
) -> Iterator[AnswerRun | None]:
    """Keep the matching scoped registry entry stable through a SQLite write."""

    key = output_key(conversation_id)
    with runs.locked_active() as active:
        yield next((
            run for run in active
            if not run.finished and (run.session_id, run.scope, run.server_boot_id, run.attempt_id)
            == (key, scope, server_boot_id, attempt_id)
        ), None)


def _approval_work(
    session: Session, permit: FirstWrite,
) -> tuple[ArtifactConversation, workspaces.Workspace, workspaces.Work]:
    row = session.get(ArtifactConversation, permit.conversation_id)
    if (
        row is None or row.trashed_at is not None
        or (row.workspace_id, row.knowledge_base_id) != (permit.workspace_id, permit.library_id)
    ):
        raise LookupError("The output's conversation was not found.")
    workspace = workspaces.load_workspace(row.state_json)
    work = next((item for item in workspace.works if item.key == permit.work_key), None)
    if work is None:
        raise PlanConflictError("plan_required", "Approve the current plan before saving.")
    return row, workspace, work


def _approved_creation(
    session: Session, permit: FirstWrite, scope: dict,
    *, kind: str, style: str, snapshots: Sequence[ArtifactUnitSnapshotOut],
    inputs: Mapping[str, object], citations: Sequence[dict[str, object]],
    verified_assets: dict[str, str],
) -> tuple[ArtifactConversation, ApprovedCreationPlanOut]:
    row, workspace, work = _approval_work(session, permit)
    brief = workspaces.load_brief(row.brief_json)
    plan = work.plan
    approval = plan.approval if plan is not None else None
    if (
        work.version_id is not None or plan is None or approval is None
        or approval.first_version_id is not None
        or (approval.request_id, approval.attempt_id, approval.revision)
        != (permit.request_id, permit.attempt_id, permit.revision)
        or not matches_attempt(
            work, permit.request_id, permit.server_boot_id, permit.attempt_id, "generate",
        )
    ):
        raise PlanConflictError("plan_required", "Approve the current plan before saving.")
    pending = workspace.pending_by_target.get(workspaces.pending_target(work))
    if pending is not None or plan_revision(work, brief, pending) != permit.revision:
        raise PlanConflictError("plan_changed", "Review the current plan before generating.")
    user = session.get(ArtifactMessage, approval.user_message_id)
    user_fields = json.loads(user.payload_json or "{}") if user is not None else {}
    if (
        user is None or user.conversation_id != row.id or user.kind != "approve"
        or user.role != "user" or user.client_request_id != permit.request_id
        or user_fields.get("planRevision") != permit.revision
        or user_fields.get("planId") != plan.id
    ):
        raise PlanConflictError("plan_required", "The approval message could not be verified.")
    material = reviewed_studio_material(
        session, row.workspace_id, row.knowledge_base_id, work.spec.scope or OutputScope(),
        workspace, work, frozen=True, verified_assets=verified_assets,
    )
    if material is None or material.key != approval.material_key or material.blocker:
        raise PlanConflictError(
            "plan_changed", (material.blocker if material else None)
            or "The reviewed material changed. Plan again.",
        )
    shown = project_work_plan(work, brief, material=material, attempt_is_live=False)
    assert shown is not None
    if shown.blocker or shown.proposal is None:
        raise PlanConflictError("plan_changed", shown.blocker or "Review the current plan.")
    assert material.progress is not None
    members = material.progress["manifest"]["members"]
    units = {
        (pin["id"], pin["revision"], pin["content_hash"])
        for pin in members if pin["kind"] == "unit"
    }
    declared_inputs = {
        (kind, value["id"], value.get("revisionId"))
        for kind in ("source", "unit", "session")
        for value in inputs.get(f"{kind}s", ())
    }
    if (
        kind != work.spec.kind or style != shown.options.type
        or OutputScope.model_validate(scope) != shown.options.scope
        or units != {(item.unit_id, item.revision_id, item.content_hash) for item in snapshots}
        or declared_inputs != {(pin["kind"], pin["id"], pin["revision"]) for pin in members}
    ):
        raise PlanConflictError("plan_changed", "The output no longer matches the approved plan.")

    def citation_key(citation: dict) -> str:
        return ConversationCitationOut.model_validate(citation).model_dump_json(
            exclude={"ref", "library_source_id"},
        )

    eligible = {citation_key(item) for item in workspace.pool if item.get("ref") in material.refs}
    if any(citation_key(citation) not in eligible for citation in citations):
        raise PlanConflictError("plan_changed", "The output cites evidence outside the approval.")
    return row, ApprovedCreationPlanOut(
        plan_id=plan.id, revision=approval.revision, approval_message_id=approval.user_message_id,
        approved_at=approval.approved_at, material_key=approval.material_key, options=shown.options,
        preferences=shown.preferences, proposal=shown.proposal, outline=shown.outline,
    )


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
    voices: VoicePair | None = None,
    first_write: FirstWrite | None = None,
    answer_runs: AnswerRuns | None = None,
    asset_reader: AssetReader | None = None,
) -> ArtifactOut:
    """Write a new version of an output, sealed with its hashes, and what the Checker found.

    A conversation publishes its work and turn in the same transaction as the version.
    """

    if parent_id is None:
        if (
            not isinstance(first_write, FirstWrite) or publish is None or answer_runs is None
            or (first_write.workspace_id, first_write.library_id, first_write.request_id)
            != (workspace_id, base_id, client_request_id)
        ):
            raise PlanConflictError("plan_required", "Approve the plan before its first save.")
    elif first_write is not None:
        raise PlanConflictError("plan_changed", "First-version approval cannot write a revision.")

    verified_assets = {}
    guard = nullcontext()
    if first_write is not None:
        assert answer_runs is not None
        scope_key = (workspace_id, base_id)
        if not answer_runs.matches(
            output_key(first_write.conversation_id), scope=scope_key,
            server_boot_id=first_write.server_boot_id, attempt_id=first_write.attempt_id,
        ):
            raise PlanConflictError("plan_required", "The approved attempt is no longer live.")
        with sessions() as session:
            _, _, work = _approval_work(session, first_write)
        verified_assets = verify_studio_assets(work, asset_reader)
        guard = locked_plan_run(
            answer_runs, first_write.conversation_id, scope_key,
            server_boot_id=first_write.server_boot_id, attempt_id=first_write.attempt_id,
        )

    try:
        with guard as run, session_scope(sessions) as session:
            if first_write is not None:
                if run is None:
                    raise PlanConflictError(
                        "plan_required", "The approved attempt is no longer live.",
                    )
                if run.stop_requested.is_set():
                    raise Stopped
            # Serialize the lineage eligibility check with a concurrent move to Trash.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            library = output_library(session, workspace_id, base_id)
            if parent_id is not None:
                parent = output_version(session, workspace_id, base_id, parent_id, latest=True)
                lineage_id, version_number = parent.lineage_id, parent.version_number + 1
            else:
                parent, lineage_id, version_number = None, new_id("arl"), 1
            if len(content.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise ValueError(f"The output is larger than the {MAX_OUTPUT_BYTES:,}-byte limit")
            citations = [
                {key: value for key, value in item.items()
                 if key not in {"library_source_id", "librarySourceId"}}
                for item in citations
            ]
            skilled = made_by or {}
            if kind == "slides" and isinstance(skilled.get("skill"), dict):
                from gunther.slide_review import invalidate_reviews

                skilled = {
                    **skilled,
                    "skill": invalidate_reviews(
                        skilled["skill"], content, outline, citations, skilled.get("use")
                    ),
                }
            verified = artifact_detail(session, parent).provenance if parent else None
            parent_provenance = json.loads(parent.provenance_json) if parent else {}
            style = output_type_for(kind, style).id
            approved = None
            approval_row = None
            if first_write is not None:
                approval_row, approved = _approved_creation(
                    session, first_write, scope, kind=kind, style=style, snapshots=snapshots,
                    inputs=inputs, citations=citations, verified_assets=verified_assets,
                )
            conversation_id = skilled.get("conversation_id") or parent_provenance.get(
                "conversation_id"
            )
            if approval_row is not None:
                if conversation_id not in (None, approval_row.id):
                    raise LookupError("This plan belongs to a different conversation.")
                conversation_id = approval_row.id
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
                searched=skilled.get("searched", []),
                key_results_used=skilled.get("key_results_used", []),
            )
            conversation = None
            if provenance.conversation_id:
                conversation = session.get(ArtifactConversation, provenance.conversation_id)
                if conversation is not None and (
                    conversation.trashed_at is not None
                    or (conversation.workspace_id, conversation.knowledge_base_id)
                    != (workspace_id, base_id)
                ):
                    raise LookupError("The output's conversation was not found")
            sealed = provenance.model_dump(exclude={"searched": {"__all__": {"library_source_id"}}})
            effective = skilled.get("effective_preferences")
            if approved is not None:
                sealed["creationPlan"] = approved.model_dump(mode="json", by_alias=True)
                effective = approved.preferences
            if isinstance(verified, OutputProvenanceOut):
                if verified.creation_version_id is not None and verified.creation_plan:
                    first = session.get(Artifact, verified.creation_version_id)
                    if first is not None:
                        sealed["creationPlanRef"] = creation_plan_reference(first, verified)
                if effective is None:
                    effective = (
                        workspaces.load_brief(conversation.brief_json)
                        if conversation is not None else verified.preferences
                    )
            if effective is not None:
                preferences = PlanPreferencesOut.model_validate(effective)
                sealed["effectivePreferences"] = PlanPreferencesOut(
                    emphasize=preferences.emphasize, avoid=preferences.avoid,
                ).model_dump(mode="json", by_alias=True)
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
            if SHAPES[kind].speech:
                previous_audio = session.get(ArtifactAudio, parent.id) if parent else None
                session.add(ArtifactAudio(
                    artifact_id=artifact.id,
                    content_hash=artifact.content_hash,
                    voices_json=(previous_audio.voices_json if previous_audio and voices is None
                                else json.dumps(voices)),
                ))
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
            if first_write is not None and requested_images - verified_assets.keys():
                raise PlanConflictError(
                    "plan_changed", "The output uses a figure outside the plan.",
                )
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
            if approval_row is not None:
                state = workspaces.load_workspace(approval_row.state_json)
                saved = next(
                    (work for work in state.works if work.key == artifact.lineage_id), None,
                )
                if (
                    saved is None or saved.version_id != artifact.id or saved.plan is None
                    or saved.plan.approval is None or approved is None
                    or saved.plan.approval.user_message_id != approved.approval_message_id
                ):
                    raise PlanConflictError("plan_changed", "The approved work could not be saved.")
                saved.plan.approval.first_version_id = artifact.id
                approval_row.state_json = workspaces.dump_workspace(state)
            return shown
    except (IntegrityError, ArtifactConflictError) as error:
        if first_write is not None:
            if isinstance(error, IntegrityError):
                raise ArtifactConflictError("The approved version could not be saved.") from error
            raise
        earlier = earlier_output(sessions, workspace_id, client_request_id, fingerprint)
        if earlier is not None:
            return earlier
        if isinstance(error, ArtifactConflictError):
            raise
        raise ArtifactConflictError(
            "Artifact version creation conflicted with another writer; refresh and retry"
        ) from error
