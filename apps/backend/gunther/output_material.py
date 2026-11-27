"""Resolve the sources, sessions, readings and evidence for an output."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only, selectinload

from gunther import artifact_workspace as workspaces
from gunther.artifact_plans import PlanConflictError, ReviewedMaterial
from gunther.ask_turns import conversation_pool
from gunther.evidence import Evidence, Tool, ToolFailure
from gunther.evidence_reader import (
    FIGURE_DISTANCE,
    element_positions,
    figure_reference,
    pinned_revision,
    read_snapshot,
    readable_blocks,
)
from gunther.library_lookup import SearchHit
from gunther.models import (
    Asset,
    ContentBlock,
    DocumentElement,
    KnowledgeBaseSource,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    SessionMessage,
    Source,
    SourceDigest,
    SourceIndexHead,
    SourcePaper,
    SourceRevision,
)
from gunther.outputs import Notes, Pool
from gunther.schemas import ArtifactUnitSnapshotOut, ConversationCitationOut, OutputScope
from gunther.service_records import _id
from gunther.slide_render import digest

NOTHING_TO_BUILD_FROM = "There is nothing to build from yet. Add sources."
_NOTE_PAYLOAD = TypeAdapter(Notes)
AssetReader = Callable[[str], tuple[Path, str, str]]
_ASSET_BLOCKER = "A reviewed figure is unavailable or failed its integrity check. Plan again."


def scope_members(
    session: Session, base_id: str, scope: OutputScope, *, strict: bool = False,
) -> list[dict[str, Any]]:
    """Read the membership and revision identities without loading source passages."""

    sources = (
        select(
            Source.id, Source.content_hash, SourceRevision.id,
            SourceRevision.fingerprint, SourceRevision.state,
        )
        .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
        .outerjoin(SourceIndexHead, SourceIndexHead.source_id == Source.id)
        .outerjoin(
            SourceRevision,
            (SourceRevision.id == SourceIndexHead.revision_id)
            & (SourceRevision.source_id == Source.id),
        )
        .where(KnowledgeBaseSource.knowledge_base_id == base_id, Source.trashed_at.is_(None))
    )
    if scope.mode == "selection":
        sources = sources.where(Source.id.in_(scope.source_ids))
    members = [
        {
            "kind": "source", "id": source, "revision": revision,
            "content_hash": digest([content_hash, fingerprint, state]),
        }
        for source, content_hash, revision, fingerprint, state in session.execute(sources)
    ]
    if scope.unit_ids:
        units = session.execute(
            select(KnowledgeUnit.id, KnowledgeUnitRevision.id, KnowledgeUnitRevision.content)
            .join(
                KnowledgeUnitRevision,
                (KnowledgeUnitRevision.id == KnowledgeUnit.head_revision_id)
                & (KnowledgeUnitRevision.unit_id == KnowledgeUnit.id),
            )
            .where(
                KnowledgeUnit.knowledge_base_id == base_id,
                KnowledgeUnit.status == "trusted",
                KnowledgeUnit.id.in_(scope.unit_ids),
            )
        )
        members.extend(
            {
                "kind": "unit", "id": unit, "revision": revision,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            for unit, revision, content in units
        )
    if scope.session_ids:
        # Ask appends immutable messages and advances the session timestamp in the same write.
        # Count and latest ID also identify message additions/removals in restored libraries.
        messages = SessionMessage.session_id == KnowledgeSession.id
        count = select(func.count()).where(messages).correlate(KnowledgeSession).scalar_subquery()
        latest = (
            select(SessionMessage.id).where(messages).correlate(KnowledgeSession)
            .order_by(SessionMessage.created_at.desc(), SessionMessage.id.desc())
            .limit(1).scalar_subquery()
        )
        discussions = session.execute(
            select(KnowledgeSession.id, KnowledgeSession.title, KnowledgeSession.summary,
                   KnowledgeSession.updated_at, count, latest)
            .where(KnowledgeSession.knowledge_base_id == base_id,
                   KnowledgeSession.id.in_(scope.session_ids))
        )
        members.extend(
            {"kind": "session", "id": id_, "revision": None,
             "content_hash": digest([title, summary, updated.isoformat(), size, head])}
            for id_, title, summary, updated, size, head in discussions
        )
    if strict and scope.mode == "selection":
        found = {(member["kind"], member["id"]) for member in members}
        for kind, ids in (
            ("source", scope.source_ids), ("unit", scope.unit_ids), ("session", scope.session_ids),
        ):
            missing = [id_ for id_ in ids if (kind, id_) not in found]
            if missing:
                raise ValueError(f"Selected {kind} items are unavailable: {', '.join(missing)}.")
    return sorted(members, key=lambda item: (item["kind"], item["id"]))


def studio_manifest(
    workspace_id: str, base_id: str, scope: OutputScope, members: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_id,
        "library_id": base_id,
        "scope": {
            "mode": scope.mode,
            **{name: sorted(set(getattr(scope, name)))
               for name in ("source_ids", "unit_ids", "session_ids")},
        },
        "members": members,
        "web_pass": "off",
        "web_query": None,
    }


def pinned_scope(manifest: dict[str, Any]) -> OutputScope:
    members = manifest["members"]
    return OutputScope.model_construct(
        mode="selection",
        **{f"{kind}_ids": [pin["id"] for pin in members if pin["kind"] == kind]
           for kind in ("source", "unit", "session")},
    )


def restore_studio_material(
    workspace: workspaces.Workspace, progress: dict[str, Any], *, limit: int,
) -> tuple[Pool, Notes]:
    by_ref = {item["ref"]: item for item in workspace.pool if item.get("ref") is not None}
    refs = progress["pool_refs"]
    if any(type(ref) is not int or ref < 1 for ref in refs) or len(set(refs)) != len(refs):
        raise ValueError("The stored evidence references are invalid.")
    pool = Pool(limit)
    pool.restore([
        workspaces.evidence_from_citation(ConversationCitationOut.model_validate(by_ref[ref]))
        for ref in refs
    ])
    return pool, _NOTE_PAYLOAD.validate_python(progress["notes"])


def _pool_print(pool: Pool) -> str:
    from gunther.studio.science import pool_print

    return pool_print(pool.items, full=True)


def studio_asset_hashes(session: Session, pool: Pool) -> dict[str, str]:
    from gunther.studio.science import figure_inventory

    ids = set(figure_inventory(pool.items))
    hashes = dict(session.execute(
        select(Asset.id, Asset.content_hash).where(Asset.id.in_(ids))
    ).tuples().all()) if ids else {}
    if hashes.keys() != ids:
        raise PlanConflictError("plan_changed", _ASSET_BLOCKER)
    return hashes


def verify_studio_assets(work: workspaces.Work, read_asset: AssetReader | None) -> dict[str, str]:
    """Read the reviewed bytes before holding the run registry or SQLite write lock."""

    progress = work.studio_progress or {}
    manifest = progress.get("manifest") or {}
    pins = manifest.get("assets", {}) if isinstance(manifest, dict) else {}
    if not isinstance(pins, dict) or (pins and read_asset is None):
        raise PlanConflictError("plan_changed", _ASSET_BLOCKER)
    if read_asset:
        try:
            for asset_id in pins:
                read_asset(asset_id)
        except (LookupError, OSError, TypeError) as error:
            raise PlanConflictError("plan_changed", _ASSET_BLOCKER) from error
    return pins


def _pin_revisions_are_eligible(session: Session, base_id: str, pool: Pool) -> bool:
    pins = {
        (item.citation.source_id, item.citation.source_revision_id)
        for item in pool.items if item.kind == "library"
    }
    if not pins:
        return True
    sources = select(Source.id).join(KnowledgeBaseSource).where(
        KnowledgeBaseSource.knowledge_base_id == base_id, Source.trashed_at.is_(None),
        Source.id.in_({source for source, _ in pins}),
    )
    live = set(session.scalars(sources))
    revisions = set(session.execute(
        select(SourceRevision.source_id, SourceRevision.id).where(
            SourceRevision.id.in_({revision for _, revision in pins if revision}),
        )
    ))
    return all(source in live and (not revision or (source, revision) in revisions)
               for source, revision in pins)


def reviewed_studio_material(
    session: Session, workspace_id: str, base_id: str, scope: OutputScope,
    workspace: workspaces.Workspace, work: workspaces.Work, *,
    local_only: bool = False, frozen: bool = False,
    verified_assets: dict[str, str] | None = None,
) -> ReviewedMaterial | None:
    """Verify the checkpoint using stored identities and the durable passages it names."""

    progress = work.studio_progress
    if not progress or not isinstance(progress.get("manifest"), dict):
        return None
    saved = progress["manifest"]
    current = studio_manifest(workspace_id, base_id, scope, [])
    if any(saved.get(key) != current[key] for key in ("workspace_id", "library_id", "scope")):
        return None
    try:
        members = scope_members(session, base_id, scope, strict=True)
        previous = saved["members"]
        if frozen:
            by_id = {(pin["kind"], pin["id"]): pin for pin in members}
            matches = all(by_id.get((pin["kind"], pin["id"])) == pin for pin in previous)
        else:
            matches = previous == members
        if not previous or not matches:
            return ReviewedMaterial(None, blocker="Plan with these sources before generating.")
        pool, notes = restore_studio_material(workspace, progress, limit=len(progress["pool_refs"]))
        printed = _pool_print(pool)
        # Use the same material identity as Studio.prepare, including the retained provenance.
        if (
            progress.get("pool") != printed
            or progress.get("material_key") != digest([saved, printed, asdict(notes)])
            or not _pin_revisions_are_eligible(session, base_id, pool)
            or any(
                name in progress and progress[name] != saved.get(name, [])
                for name in ("searched", "key_results_used")
            )
        ):
            return ReviewedMaterial(None, blocker="Some reviewed evidence is unavailable.")
        assets = studio_asset_hashes(session, pool)
        if assets != saved.get("assets", {}) or (
            verified_assets is not None and assets != verified_assets
        ):
            return ReviewedMaterial(None, blocker=_ASSET_BLOCKER)
        web_pass = saved["web_pass"]
        if local_only:
            local = Pool(pool.limit)
            local.restore([item for item in pool.items if item.kind != "web"])
            pool = local
            if not (pool.items or notes.knowledge or notes.discussions):
                return ReviewedMaterial(None, blocker="Plan with eligible local material.")
            refs = list(pool.by_ref())
            searched = [hit for hit in saved.get("searched", []) if hit["kind"] != "web"]
            results = [
                {**item, "refs": [ref for ref in item["refs"] if ref in refs]}
                for item in saved.get("key_results_used", [])
            ]
            saved = {
                **saved, "web_pass": "off", "web_query": None,
                "searched": searched, "key_results_used": results,
                "assets": studio_asset_hashes(session, pool),
            }
            printed = _pool_print(pool)
            progress = {
                "format": progress["format"], "manifest": saved, "pool_refs": refs,
                "notes": asdict(notes), "pool": printed,
                "material_key": digest([saved, printed, asdict(notes)]),
            }
            web_pass = "off"
        blocker = (
            "Retry web search or turn Web off." if web_pass == "unavailable" else None
        )
        return ReviewedMaterial(
            progress["material_key"], frozenset(pool.by_ref()), web_pass == "complete",
            blocker, progress,
        )
    except PlanConflictError as error:
        return ReviewedMaterial(None, blocker=str(error))
    except (KeyError, TypeError, ValueError):
        return ReviewedMaterial(None, blocker="Plan with these sources before generating.")


@dataclass
class _OutputScope:
    """What an output may be built from, found in the database."""

    # Every live source of the library (what a seed passage must belong to).
    library_source_ids: list[str]
    # The sources the library tool searches.
    source_ids: list[str]
    units: list[KnowledgeUnit]
    sessions: list[KnowledgeSession]


@dataclass
class _OutputMaterial:
    source_revisions: dict[str, str | None]
    notes: Notes
    seeds: list[Evidence]
    inputs: dict[str, list[dict[str, object]]]
    snapshots: list[ArtifactUnitSnapshotOut]


class OutputMaterialService:
    def _resolve_output_scope(
        self, session: Session, base_id: str, scope: OutputScope, *, strict: bool = True,
        include_discussion_messages: bool = False,
    ) -> _OutputScope:
        """Resolve live sources and any explicitly named saved answers or discussions.

        With ``strict``, a pick outside this library or since removed is an error.
        """

        members = list(
            session.scalars(
                select(KnowledgeBaseSource.source_id)
                .join(Source, Source.id == KnowledgeBaseSource.source_id)
                .where(
                    KnowledgeBaseSource.knowledge_base_id == base_id,
                    Source.trashed_at.is_(None),
                )
                .order_by(Source.created_at.desc())
            )
        )
        if scope.mode == "library":
            return _OutputScope(members, members, [], [])
        units = (
            select(KnowledgeUnit)
            .options(selectinload(KnowledgeUnit.revisions))
            .where(KnowledgeUnit.knowledge_base_id == base_id, KnowledgeUnit.status == "trusted")
        )
        in_library = set(members)
        found_units = {
            unit.id: unit
            for unit in session.scalars(units.where(KnowledgeUnit.id.in_(scope.unit_ids)))
        }
        discussion_query = select(KnowledgeSession).where(
            KnowledgeSession.id.in_(scope.session_ids),
            KnowledgeSession.knowledge_base_id == base_id,
        )
        if include_discussion_messages:
            discussion_query = discussion_query.options(selectinload(KnowledgeSession.messages))
        found_sessions = {
            item.id: item
            for item in session.scalars(discussion_query)
        }
        if strict:
            for wanted, have, what in (
                (scope.source_ids, in_library, "sources"),
                (scope.unit_ids, found_units, "saved answers"),
                (scope.session_ids, found_sessions, "discussions"),
            ):
                gone = [item for item in wanted if item not in have]
                if gone:
                    raise ValueError(
                        f"These {what} are not in this library, or were removed: {', '.join(gone)}"
                    )
        return _OutputScope(
            members,
            [item for item in scope.source_ids if item in in_library],
            [found_units[item] for item in scope.unit_ids if item in found_units],
            [found_sessions[item] for item in scope.session_ids if item in found_sessions],
        )

    def _output_material(
        self,
        session: Session,
        base_id: str,
        scope: OutputScope,
        *,
        strict: bool = True,
        allow_empty: bool = False,
        frozen_manifest: Mapping[str, Any] | None = None,
    ) -> _OutputMaterial:
        """Resolve what the output may read and pin each selected answer once."""

        include_readings = frozen_manifest is None
        retained = frozen_manifest or {}
        unit_evidence_counts = retained.get("unit_evidence_counts", {})
        resolved = self._resolve_output_scope(
            session, base_id, scope, strict=strict, include_discussion_messages=include_readings,
        )
        if not allow_empty and not (resolved.source_ids or resolved.units or resolved.sessions):
            raise ValueError(NOTHING_TO_BUILD_FROM)
        in_library = set(resolved.library_source_ids)
        seeds: list[Evidence] = []

        def take(messages: list[SessionMessage]) -> None:
            for item in conversation_pool(messages):
                # A passage of a source that has left this library is not the library's now.
                if item.kind == "library" and item.payload[0].source_id not in in_library:
                    continue
                seeds.append(replace(item, ref=None))

        snapshots = []
        for unit in resolved.units:
            head = next((item for item in unit.revisions if item.id == unit.head_revision_id), None)
            if head is None:
                raise ValueError(f"Knowledge Unit {unit.id} has no head revision")
            proposal = session.get(KnowledgeProposal, head.source_proposal_id)
            if proposal is None:
                raise ValueError(f"Knowledge Unit {unit.id} has no source proposal")
            if include_readings:
                message = session.get(SessionMessage, proposal.message_id)
                if message is not None:
                    take([message])
                evidence_count = len(json.loads(message.citations_json or "[]")) if message else 0
            elif unit.id in unit_evidence_counts:
                evidence_count = unit_evidence_counts[unit.id]
            else:
                # Older verified manifests did not retain the count for a trusted answer.
                evidence_count = session.scalar(
                    select(func.json_array_length(SessionMessage.citations_json))
                    .where(SessionMessage.id == proposal.message_id)
                ) or 0
            snapshots.append(
                ArtifactUnitSnapshotOut(
                    unit_id=unit.id,
                    revision_id=head.id,
                    revision_number=head.revision_number,
                    title=unit.title,
                    content=head.content,
                    content_hash=hashlib.sha256(head.content.encode("utf-8")).hexdigest(),
                    source_proposal_id=head.source_proposal_id,
                    source_session_id=proposal.session_id,
                    source_message_id=proposal.message_id,
                    evidence_count=evidence_count,
                )
            )
        if sum(len(item.content.encode("utf-8")) for item in snapshots) > 2_000_000:
            raise ValueError(
                "The saved answers chosen are larger than the 2,000,000-byte limit; choose fewer"
            )
        discussions: list[tuple[str, str]] = []
        session_inputs: list[dict[str, object]] = []
        if include_readings:
            for item in resolved.sessions:
                discussions.append((item.title, item.summary))
                answers = [
                    message.id for message in item.messages
                    if message.role == "assistant" and json.loads(message.citations_json or "[]")
                ]
                session_inputs.append(
                    {"id": item.id, "title": item.title, "messageIds": answers}
                )
                take(list(item.messages))
        else:
            session_inputs = list(retained.get("discussion_inputs", []))
        sources = (
            list(
                session.scalars(
                    select(Source)
                    .where(Source.id.in_(resolved.source_ids))
                    .order_by(Source.created_at.desc())
                    .options(*(
                        (load_only(Source.id, Source.title),) if not include_readings else ()
                    ))
                )
            )
            if resolved.source_ids
            else []
        )
        shown = [source.id for source in sources[:40]] if include_readings else []
        digests = (
            dict(
                session.execute(
                    select(SourceDigest.source_id, SourceDigest.overview).where(
                        SourceDigest.source_id.in_(shown)
                    )
                ).all()
            )
            if shown
            else {}
        )
        heads = (
            dict(
                session.execute(
                    select(SourceIndexHead.source_id, SourceIndexHead.revision_id).where(
                        SourceIndexHead.source_id.in_([source.id for source in sources])
                    )
                ).all()
            )
            if sources
            else {}
        )
        notes = Notes(
            knowledge=tuple((item.title, item.content) for item in snapshots[:20]),
            discussions=tuple(discussions[:10]),
            sources=tuple(
                (source.title, digests.get(source.id) or source.content[:300])
                for source in sources[:40] if include_readings
            ),
        )
        inputs = {
            "sources": [
                {"id": source.id, "title": source.title, "revisionId": heads.get(source.id)}
                for source in sources
            ],
            "units": [{"id": item.unit_id, "revisionId": item.revision_id} for item in snapshots],
            "sessions": session_inputs,
        }
        revisions = {source_id: heads.get(source_id) for source_id in resolved.source_ids}
        return _OutputMaterial(revisions, notes, seeds, inputs, snapshots)

    @staticmethod
    def _studio_metadata(
        session: Session,
        group: list[ContentBlock],
        elements: list[DocumentElement],
        mapped: dict[str, list[int]],
    ) -> tuple[list[dict], list[dict]]:
        """A passage's figures and what its extraction could not vouch for.

        Figures come from the passage's own elements and those just around it on the same
        page (a figure sits between paragraphs). Warnings come from the passage's own text
        only: uncertain glyphs (shown as U+FFFD), glyphs the check could not verify (their
        numbers cannot be trusted) and equations withheld from the text. The caller passes
        only the selected source's pinned revision; captions are not interpreted here.
        """
        from gunther.studio.format import numbers_in

        ordinals = [n for block in group for n in mapped.get(block.id, [])]
        pages = {json.loads(b.anchor_json or "{}").get("page") for b in group} - {None}
        paths = {b.heading_path_json for b in group}
        own: list[DocumentElement] = []
        nearby: list[DocumentElement] = []
        if ordinals:
            low, high = min(ordinals), max(ordinals)
            for element in elements:
                if low <= element.ordinal <= high:
                    own.append(element)
                    continue
                anchor = json.loads(element.anchor_json or "{}")
                if (
                    low - FIGURE_DISTANCE <= element.ordinal <= high + FIGURE_DISTANCE
                    and (not pages or anchor.get("page") in pages)
                    and (
                        element.heading_path_json in paths
                        or not json.loads(element.heading_path_json or "[]")
                    )
                ):
                    nearby.append(element)
        figures: list[dict] = []
        warnings: dict[tuple, dict] = {}
        for node in [*group, *own, *nearby]:
            if node.kind == "figure" and isinstance(node, DocumentElement):
                figure = figure_reference(session, node, detailed=True)
                if figure is not None:
                    figures.append(figure)
                continue
            if node in nearby:
                continue
            payload = json.loads(node.payload_json or "{}")
            anchor = json.loads(node.anchor_json or "{}")
            provenance = {"revisionId": node.revision_id, "page": anchor.get("page")}
            provenance["elementId" if isinstance(node, DocumentElement) else "blockId"] = node.id
            page = anchor.get("page")
            if "\ufffd" in node.content or any(
                span.get("mark") == "uncertain" for span in payload.get("inlineSpans", [])
            ):
                warnings[("uncertain_glyph", page, node.content)] = {
                    **provenance,
                    "kind": "uncertain_glyph",
                    "reason": "Some glyphs could not be read; they show as U+FFFD.",
                }
            if payload.get("transcription") in {"unavailable", "unreliable"}:
                warnings[("withheld_formula", node.id)] = {
                    **provenance,
                    "kind": "withheld_formula",
                    "reason": "An equation was kept as an image, not as text: "
                    + str(payload.get("transcriptionIssue") or payload["transcription"]),
                }
            elif payload.get("glyphCheck") in {"unverified", "uncertain"} and node.content:
                numbers = sorted(numbers_in(node.content))
                warnings[("unverified_glyphs", page, node.content)] = {
                    **provenance,
                    "kind": "unverified_glyphs",
                    "reason": "The glyph check could not verify this text's characters.",
                    "numbers": numbers,
                }
        return figures, list(warnings.values())

    @staticmethod
    def _output_evidence_in_scope(
        item: Evidence, heads: Mapping[str, str | None], pinned_keys: set[tuple[str, str, str]], *,
        web: bool = True,
    ) -> bool:
        if item.key in pinned_keys:
            return True
        if item.kind != "library":
            return item.kind != "web" or web
        citation = item.payload[0] if isinstance(item.payload, tuple) and item.payload else None
        source = getattr(citation, "source_id", "")
        revision = heads.get(source) if source else None
        return revision is not None and citation.source_revision_id == revision

    def _studio_reading(
        self, session: Session, revisions: Mapping[str, str | None], pool: Pool, *,
        pinned: Sequence[Evidence] = (), most: int = 900,
        searched: list[SearchHit] | None = None,
    ) -> list[str]:
        """Every selected source read whole into the pool, in its own order, as passages of
        consecutive blocks under one heading (about 1,400 characters each). The studio reads
        the material, not search results; each passage stays citable like a search hit."""

        target = 1400
        source_ids = list(revisions)
        sources = sorted(
            session.scalars(select(Source).where(Source.id.in_(source_ids))),
            key=lambda source: source.created_at,
        )
        found_sources = {source.id for source in sources}
        gaps = [
            f"Source {source_id} is selected but unavailable, so it was not read."
            for source_id in sorted(revisions.keys() - found_sources)
        ]
        verified: dict[str, str] = {}
        for source in sources:
            revision_id = revisions[source.id]
            if not revision_id:
                gaps.append(
                    f"Source {source.title} has no indexed revision, so its main content "
                    "was not read."
                )
            elif pinned_revision(session, source.id, revision_id) is None:
                gaps.append(f"The selected revision of {source.title} could not be read.")
            else:
                verified[source.id] = revision_id

        # Pinned evidence comes from the selected notes and discussions in the live library.
        pinned_keys = {item.key for item in pinned}
        scoped = [
            item
            for item in pool.items
            if self._output_evidence_in_scope(item, verified, pinned_keys)
        ]
        pool.items = scoped
        pool.known = {item.key: item for item in scoped}
        papers = {
            row.source_id: row for row in session.scalars(
                select(SourcePaper).where(SourcePaper.source_id.in_(list(verified)))
            )
        }
        for source in sources:
            revision_id = verified.get(source.id)
            if revision_id is None:
                continue
            blocks = list(session.scalars(readable_blocks(revision_id)))
            elements = list(
                session.scalars(
                    select(DocumentElement)
                    .where(DocumentElement.revision_id == revision_id)
                    .order_by(DocumentElement.ordinal)
                )
            )
            mapped: dict[str, list[int]] = {}
            for block_id, ordinal in session.execute(element_positions(revision_id)):
                mapped.setdefault(block_id, []).append(ordinal)
            groups: list[list[ContentBlock]] = []
            size = 0
            for block in blocks:
                if not block.content.strip():
                    continue
                fresh = (
                    not groups
                    or block.kind == "heading"
                    or block.heading_path_json != groups[-1][-1].heading_path_json
                    and groups[-1][-1].kind != "heading"
                    or size + len(block.content) > target
                )
                if fresh:
                    groups.append([])
                    size = 0
                groups[-1].append(block)
                size += len(block.content) + 2
            for group in groups:
                if len(pool) >= most:
                    gaps.append(
                        f"The build pool held {len(pool)} of {len(groups)} passage groups from "
                        f"{source.title}; the remaining groups were not read."
                    )
                    return gaps
                snapshot = read_snapshot(group)
                if not snapshot.text.strip():
                    continue
                path = json.loads(group[0].heading_path_json or "[]")
                locator = " › ".join(str(part) for part in path if part) or group[0].locator
                figures, warnings = self._studio_metadata(session, group, elements, mapped)
                paper = papers.get(source.id)
                source_metadata = (
                    {
                        key: value
                        for key, value in {
                            "title": paper.title,
                            "authors": paper.authors,
                            "year": paper.year,
                        }.items()
                        if value not in (None, "")
                    }
                    if paper is not None and paper.revision_id == revision_id
                    else {}
                )
                citation = ConversationCitationOut(
                    id=_id("cit"),
                    source_id=source.id,
                    source_title=source.title,
                    quote=snapshot.text,
                    locator=locator[:240],
                    status="provisional",
                    confidence=1.0,
                    source_revision_id=revision_id,
                    block_id=group[0].id,
                    anchor=json.loads(group[0].anchor_json or "{}"),
                    support_text=snapshot.text,
                    support_spans=snapshot.spans,
                    source_origin="selected",
                    figures=figures,
                    extraction_warnings=warnings,
                    source_metadata=source_metadata,
                )
                held = pool.adopt(
                    Evidence(
                        kind="library",
                        title=source.title,
                        text=snapshot.text,
                        locator=locator[:240],
                        status="provisional",
                        confidence=1.0,
                        payload=(citation, None),
                        # The checker reads ``context`` whole (up to 6,000 characters) and
                        # only the start of ``text``: a fact late in a passage must be seen.
                        context=snapshot.text,
                        extraction_warnings=tuple(warnings),
                    )
                )
                if searched is not None:
                    searched.append(SearchHit(held, "", True))
        return gaps

    @staticmethod
    def _studio_web(
        web: Tool | None, reason: str | None, query: str, pool: Pool, per_section: int,
        *, searched: list[SearchHit],
    ) -> dict[str, str] | None:
        if web is not None and query:
            try:
                for index, item in enumerate(web.run(query)):
                    kept = index < per_section and len(pool) < pool.limit
                    if kept:
                        pool.adopt(item)
                    searched.append(SearchHit(item, query, kept))
            except ToolFailure as error:
                reason = str(error)
        else:
            reason = reason or "Web search could not run."
        if reason:
            return {"step": "web", "label": "Web search unavailable", "reason": reason}
        return None
