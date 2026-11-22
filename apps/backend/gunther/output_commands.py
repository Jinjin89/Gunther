"""Run direct output builds, revisions, reviews, restyles, typed edits and checks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace

from gunther import artifact_workspace as workspaces
from gunther import usage
from gunther.agent import AskAgent
from gunther.artifact_read import artifact_detail, latest_check_records
from gunther.artifact_versions import (
    MAX_OUTPUT_BYTES,
    ArtifactConflictError,
    commit_version,
    earlier_output,
    output_library,
    output_version,
)
from gunther.artifact_workspace import evidence_from_citation
from gunther.checks import complete, merge_verdicts
from gunther.database import session_scope
from gunther.llm import ModelInfo
from gunther.model_profiles import Effort
from gunther.models import ArtifactCheck
from gunther.output_checks import check_saved_part
from gunther.outputs import (
    OutputLimits,
    Planned,
    Pool,
    Spec,
    _marked,
    join_document,
    section_hash,
    split_document,
    title_of,
)
from gunther.schemas import (
    ArtifactOut,
    ArtifactUnitSnapshotOut,
    BuildOutputInput,
    ConversationCitationOut,
    EditOutputInput,
    OutputModelOut,
    OutputScope,
    RestyleSlidesInput,
    ReviseOutputInput,
)
from gunther.service_records import _id

OUTPUTS_NEED_A_MODEL = "Outputs need a model. Set one up under Settings → Models."


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


class OutputCommands:
    # Outputs built by agents (see outputs) --------------------------------------------------

    def _output_model(
        self, model_ref: str | None = None, effort: Effort | None = None
    ) -> tuple[ModelInfo, Effort]:
        """The model that writes outputs: the Outputs job's, else Ask's."""

        chosen = None
        if self.models is not None:
            if model_ref:
                model = self.models.get(model_ref)
                if model is None:
                    raise ValueError(
                        "The selected model is not available. Choose a configured model."
                    )
                return model, effort or "high"
            chosen = self.models.for_role("outputs") or self.models.for_role("ask")
        if chosen is None:
            raise ValueError(OUTPUTS_NEED_A_MODEL)
        return chosen[0], effort or chosen[1]

    @staticmethod
    def _output_request_fields(payload: BuildOutputInput | ReviseOutputInput) -> dict:
        fields = payload.model_dump(mode="json", exclude={"client_request_id"})
        # Requests made before the conversation UI retain their original fingerprint.
        for name in ("model", "effort", "conversation_id", "conversation_title", "theme"):
            if fields.get(name) is None:
                fields.pop(name, None)
        return fields

    @staticmethod
    def _output_fingerprint(
        workspace_id: str, base_id: str, operation: str, request: dict[str, object]
    ) -> str:
        return hashlib.sha256(
            _canonical(
                {
                    "workspaceId": workspace_id,
                    "knowledgeBaseId": base_id,
                    "operation": operation,
                    "request": request,
                }
            )
        ).hexdigest()

    def build_output(
        self,
        base_id: str,
        workspace_id: str,
        payload: BuildOutputInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactOut:
        """Build a report or slides from the library, or from what was picked.

        Studio reads, plans, writes and verifies first; the version is
        written afterwards, so a failed build saves nothing. ``supersedesArtifactId`` makes
        it the next version of an existing output (a rebuild). ``events`` hears the
        progress and may raise to stop the work.
        """

        limits = OutputLimits()
        fields = self._output_request_fields(payload)
        # The retired option was part of saved fingerprints, including the old style hash.
        previous = [self._output_fingerprint(workspace_id, base_id, "build", fields)]
        inherits_style = payload.kind == "report" and "style" not in payload.model_fields_set
        if payload.kind == "report" and payload.supersedes_artifact_id:
            fields["inherit_style"] = inherits_style
            previous.append(self._output_fingerprint(workspace_id, base_id, "build", fields))
        fields.pop("library_supplements")
        fingerprint = self._output_fingerprint(workspace_id, base_id, "build", fields)
        earlier = earlier_output(
            self.sessions,
            workspace_id, payload.client_request_id, fingerprint, previous=previous
        )
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        with (
            usage.job("outputs", payload.kind, ref=payload.conversation_id),
            session_scope(self.sessions) as session,
        ):
            library = output_library(session, workspace_id, base_id)
            parent = (
                output_version(
                    session, workspace_id, base_id, payload.supersedes_artifact_id, latest=True
                )
                if payload.supersedes_artifact_id
                else None
            )
            style = payload.style
            if inherits_style and parent and parent.kind == "report":
                style = parent.style or "auto"
            parent_theme = None
            parent_sections = []
            if parent and parent.kind == payload.kind == "slides":
                parent_sections = split_document(parent.content, "slides")[1]
                if parent_sections:
                    parent_theme = _marked(parent_sections[0]).theme
            theme = payload.theme or parent_theme
            design_version = payload.design_version
            if design_version is None and parent and payload.kind == "slides" and parent_sections:
                design_version = _marked(parent_sections[0]).design_version
            spec = Spec(
                payload.kind,
                payload.audience,
                style,
                payload.brief,
                payload.title,
                theme,
                design_version,
            )
            if parent and payload.conversation_id:
                owner = json.loads(parent.provenance_json).get("conversation_id")
                if owner and owner != payload.conversation_id:
                    raise ValueError("This artifact belongs to a different conversation.")
            material = self._output_material(session, base_id, payload.scope)
            pool = self._output_pool(material.seeds, limits)
            outline = (
                [Planned(item.heading, item.goal, id=item.id) for item in payload.outline]
                if payload.outline else None
            )
            fallback_title = parent.title if parent else library.title
            from gunther.studio import format as studio_format
            from gunther.studio.pipeline import StudioAgents

            if parent_sections:
                cover = studio_format.parse_slide(parent_sections[0])
                spec = replace(
                    spec,
                    theme=payload.theme or cover.attrs.get("theme") or theme,
                    deck_style=cover.attrs.get("style"),
                )
            reading_gaps = self._studio_reading(
                session, material.source_revisions, pool, pinned=material.seeds
            )
            notes = replace(material.notes, extraction_gaps=tuple(reading_gaps))
            studio = StudioAgents(
                self.models,
                limits,
                use=None if payload.use == "auto" else payload.use,
                renderer=self.slide_renderer,
                asset_reader=self.asset_reader,
                reusable_figures=self._reusable_figure_readings(session, base_id),
            )
            purpose = None
            same_plan = parent and (outline is not None or payload.brief == parent.brief)
            if same_plan and parent.kind == payload.kind:
                saved = json.loads(parent.provenance_json).get("approach") or {}
                purpose = "explain_study" if saved.get("study_map") else saved.get("purpose")
                if outline is None:
                    outline = [
                        Planned(p["heading"], p.get("goal", ""), id=p.get("id", ""))
                        for p in json.loads(parent.outline_json)
                    ]
            if payload.web:
                web, reason = self._web_tool(True)
                warning = self._studio_web(web, reason, payload.brief or payload.title or "", pool,
                                           limits.per_section)
                if warning:
                    studio.skipped.append(warning)
            built = studio.make(
                model, effort, spec, pool, notes, events,
                fallback_title=fallback_title,
                purpose=purpose,
                fixed_outline=outline,
                scope={"selection": payload.scope.model_dump(), "inputs": material.inputs},
                web=payload.web,
            )
            made_by = {
                "generator": "gunther.studio.v1",
                "skill": built.skill,
                "approach": built.approach,
                "use": studio.use or (built.approach or {}).get("use"),
                "conversation_id": payload.conversation_id,
                "conversation_title": payload.conversation_title,
                "request": payload.brief,
            }
            parent_id = parent.id if parent else None
        if events:
            events({"type": "saving"})  # a reader who pressed Stop by now gets nothing saved
        return commit_version(
            self.sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=parent_id,
            kind=payload.kind,
            style=style,
            audience=payload.audience,
            title=built.title,
            brief=payload.brief,
            origin="rebuild" if parent_id else "build",
            content=built.content,
            scope=payload.scope.model_dump(),
            inputs=material.inputs,
            outline=built.outline,
            citations=[item.model_dump() for item in built.citations],
            snapshots=material.snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by=made_by,
        )

    def revise_output(
        self,
        base_id: str,
        workspace_id: str,
        artifact_id: str,
        payload: ReviseOutputInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactOut:
        """Change one section (or all) of the latest version as instructed: the next version.

        The passages the version cites keep their numbers; new research on the
        instruction searches the scope the output was built from. Sections that change
        are checked again; the others keep what the Checker found in them.
        """
        from gunther.studio.work import StudioWorkAgents

        limits = OutputLimits()
        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "revise",
            {
                "artifactId": artifact_id,
                **self._output_request_fields(payload),
            },
        )
        earlier = earlier_output(
            self.sessions, workspace_id, payload.client_request_id, fingerprint,
        )
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        with usage.job("outputs", ref=artifact_id), session_scope(self.sessions) as session:
            library = output_library(session, workspace_id, base_id)
            try:
                parent = output_version(
                    session, workspace_id, base_id, artifact_id, latest=True
                )
            except ArtifactConflictError:
                earlier = earlier_output(
                    self.sessions, workspace_id, payload.client_request_id, fingerprint,
                )
                if earlier is not None:
                    return earlier
                raise
            self._require_agent_output(parent)
            if payload.conversation_id:
                owner = json.loads(parent.provenance_json).get("conversation_id")
                if owner and owner != payload.conversation_id:
                    raise ValueError("This artifact belongs to a different conversation.")
            material = self._output_material(
                session,
                base_id,
                OutputScope.model_validate(json.loads(parent.scope_json)),
                strict=False,
                allow_empty=True,
            )
            pool = Pool(limits.pool)
            citations = map(
                ConversationCitationOut.model_validate, json.loads(parent.citations_json)
            )
            pool.restore(
                [
                    evidence_from_citation(citation.model_copy(update={"ref": citation.ref or n}))
                    for n, citation in enumerate(citations, 1)
                ]
            )
            tool = self._output_search_tool(
                session, list(material.source_revisions), f"{library.title}: {library.question}"
            )
            made_by = self._made_by(parent) or {}
            agents = StudioWorkAgents(self.models, parent.kind, limits)
            agents.use = made_by.get("use")
            saved = made_by.get("approach") or {}
            if saved.get("purpose") == "explain_study":
                numbers = {int(ref): n for ref, n in (saved.get("ref_map") or {}).items()
                           if ref.isdecimal()}
                agents.scientific_context = (
                    workspaces.rebind_approach_refs(saved, numbers) if numbers else saved
                )
            with usage.job(detail=parent.kind):
                built = agents.revise(
                    model,
                    effort,
                    Spec(
                        parent.kind,
                        parent.audience,
                        parent.style,
                        parent.brief,
                        title=parent.title,
                    ),
                    parent.content,
                    json.loads(parent.outline_json),
                    latest_check_records(session, parent),
                    payload.instruction,
                    payload.section_index,
                    tool,
                    pool,
                    material.notes,
                    events,
                )
            made_by = {
                **made_by,
                "generator": "gunther.studio.v1",
                "skill": {**(made_by.get("skill") or {}), **agents.record()},
                "conversation_id": payload.conversation_id,
                "conversation_title": payload.conversation_title,
                "request": payload.instruction,
            }
            fields = {
                "kind": parent.kind,
                "style": parent.style,
                "audience": parent.audience,
                "brief": parent.brief,
                "scope": json.loads(parent.scope_json),
                "outline": built.outline,
                "title": built.title or parent.title,
            }
        if events:
            events({"type": "saving"})
        return commit_version(
            self.sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=artifact_id,
            origin="revise",
            content=built.content,
            inputs=material.inputs,
            citations=[item.model_dump() for item in built.citations],
            snapshots=material.snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by=made_by,
            **fields,
        )

    def review_slides(
        self, base_id, workspace_id, artifact_id, payload, events=None,
    ) -> ArtifactOut:
        """Review the current renders without running the writer. Seal a new version."""
        from gunther.outputs import BuiltOutput
        from gunther.slide_review import review_deck

        fingerprint = self._output_fingerprint(workspace_id, base_id, "visual-review",
            {"artifactId": artifact_id, **self._output_request_fields(payload)})
        earlier = earlier_output(
            self.sessions, workspace_id, payload.client_request_id, fingerprint,
        )
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            parent = output_version(session, workspace_id, base_id, artifact_id, latest=True)
            self._require_agent_output(parent)
            if parent.kind != "slides":
                raise ValueError("Rendered-slide review is only available for decks.")
            provenance = json.loads(parent.provenance_json)
            made_by = self._made_by(parent) or {}
            previous_skill = provenance.get("skill") or {}
            use = provenance.get("use") or "talk"
            built = BuiltOutput(parent.title, parent.content, json.loads(parent.outline_json),
                [ConversationCitationOut.model_validate(c)
                 for c in json.loads(parent.citations_json)],
                list(latest_check_records(session, parent).values()), model, effort,
                skill={"name": "slides", "version": 11, **previous_skill})
            fields = {
                "kind": parent.kind, "style": parent.style, "audience": parent.audience,
                "brief": parent.brief, "scope": json.loads(parent.scope_json),
                "inputs": json.loads(parent.inputs_json),
                "snapshots": [ArtifactUnitSnapshotOut.model_validate(s)
                              for s in json.loads(parent.revision_snapshot_json)],
            }
        with usage.job("outputs", "slides", ref=artifact_id):
            built = review_deck(built, self.slide_renderer, self.models, model, "medium",
                                brief=fields["brief"], use=use, emit=events or (lambda _: None))
        if events:
            events({"type": "saving"})
        return commit_version(self.sessions, workspace_id=workspace_id, base_id=base_id,
            client_request_id=payload.client_request_id, fingerprint=fingerprint,
            parent_id=artifact_id, title=built.title, origin="revise", content=built.content,
            outline=built.outline, citations=[c.model_dump() for c in built.citations],
            model=OutputModelOut(ref=model.ref, label=model.display, effort="medium"),
            checks=built.checks, made_by={**made_by, "skill": built.skill, "use": use}, **fields)

    def restyle_output(
        self,
        base_id: str,
        workspace_id: str,
        artifact_id: str,
        payload: RestyleSlidesInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactOut:
        """Draw every slide of the latest version of a deck again in another style: the next
        version. The words, numbers, citations and notes are kept exactly (the checks carry
        over to the redrawn slides); a slide whose design fails the fact guard twice is left
        plain. The citation numbers are the version's own: the designer and the guard see the
        stored slides as they are."""

        from gunther.studio import design as D
        from gunther.studio.pipeline import StudioAgents
        from gunther.studio.requests import WORKERS

        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "restyle",
            {
                "artifactId": artifact_id,
                "style": payload.style,
                **self._output_request_fields(payload),
            },
        )
        earlier = earlier_output(
            self.sessions, workspace_id, payload.client_request_id, fingerprint,
        )
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            parent = output_version(session, workspace_id, base_id, artifact_id, latest=True)
            self._require_agent_output(parent)
            if parent.kind != "slides":
                raise ValueError("Only a deck has a style.")
            records = latest_check_records(session, parent)
            made_by = self._made_by(parent) or {}
            content = parent.content
            fields = {
                "kind": parent.kind,
                "style": parent.style,
                "audience": parent.audience,
                "brief": parent.brief,
                "scope": json.loads(parent.scope_json),
                "outline": json.loads(parent.outline_json),
                "inputs": json.loads(parent.inputs_json),
                "snapshots": [
                    ArtifactUnitSnapshotOut.model_validate(item)
                    for item in json.loads(parent.revision_snapshot_json)
                ],
                "title": parent.title,
            }
            citations = json.loads(parent.citations_json)
        preamble, slides = split_document(content, "slides")
        if not slides:
            raise ValueError("The deck has no slides.")
        agents = StudioAgents(self.models, OutputLimits(), design=True)
        label = "Designing the slides"
        agents._stage(events or (lambda _event: None), "design", label, "running")
        with usage.job("outputs", "slides", ref=artifact_id):
            designed, count, notes = D.restyle(
                agents, model, slides, payload.style, workers=WORKERS
            )
        plain = len(slides) - count
        agents._stage(
            events or (lambda _event: None),
            "design",
            label,
            "done",
            f"{count} slides designed" + (f", {plain} drawn plainly" if plain else ""),
        )
        # The checks belong to the words, which did not change: they follow each slide to its
        # new text (a check is found by the hash of the section it checked).
        checks = []
        for old, new in zip(slides, designed, strict=True):
            record = records.get(section_hash(old))
            if record is None:
                continue
            checks.append({**record, "hash": section_hash(new)})
        skill = dict(made_by.get("skill") or {})
        skill["skipped"] = [
            *[item for item in skill.get("skipped", []) if item.get("step") != "design"],
            *notes,
        ]
        skill.pop("renderCheck", None)
        made_by = {
            **made_by,
            "skill": skill,
            "conversation_id": payload.conversation_id,
            "conversation_title": payload.conversation_title,
            "request": f"Restyle: {payload.style}",
        }
        if events:
            events({"type": "saving"})
        return commit_version(
            self.sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=artifact_id,
            origin="revise",
            content=join_document(preamble, designed, "slides"),
            citations=citations,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=checks,
            made_by=made_by,
            **fields,
        )

    def edit_output(
        self,
        base_id: str,
        workspace_id: str,
        artifact_id: str,
        payload: EditOutputInput,
    ) -> ArtifactOut:
        """Save text the person typed as the next version, exactly as typed.

        Nothing is renumbered or rewritten, and no model runs. The Checker's findings
        stay with the sections whose text did not change; the others are not re-checked.
        """

        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "edit",
            {
                "artifactId": artifact_id,
                "contentHash": hashlib.sha256(payload.content.encode("utf-8")).hexdigest(),
            },
        )
        earlier = earlier_output(
            self.sessions, workspace_id, payload.client_request_id, fingerprint,
        )
        if earlier is not None:
            return earlier
        if len(payload.content.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError(f"The output is larger than the {MAX_OUTPUT_BYTES:,}-byte limit")
        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            parent = output_version(session, workspace_id, base_id, artifact_id, latest=True)
            self._require_agent_output(parent)
            records = latest_check_records(session, parent)
            hashes = dict.fromkeys(
                section_hash(text) for text in split_document(payload.content, parent.kind)[1]
            )
            checks = [records[digest] for digest in hashes if digest in records]
            fields = {
                "kind": parent.kind,
                "style": parent.style,
                "audience": parent.audience,
                "brief": parent.brief,
                "scope": json.loads(parent.scope_json),
                "inputs": json.loads(parent.inputs_json),
                "outline": json.loads(parent.outline_json),
                "citations": json.loads(parent.citations_json),
                "snapshots": [
                    ArtifactUnitSnapshotOut.model_validate(item)
                    for item in json.loads(parent.revision_snapshot_json)
                ],
                "title": title_of(payload.content) or parent.title,
                "made_by": self._made_by(parent),
            }
        return commit_version(
            self.sessions,
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=artifact_id,
            origin="edit",
            content=payload.content,
            model=None,
            checks=checks,
            **fields,
        )

    def check_output(self, base_id: str, workspace_id: str, artifact_id: str) -> ArtifactOut:
        """Have the Checker look at the sections it has not looked at as they read now.

        The version is not changed: the findings are stored beside it.
        """

        model, _ = self._output_model()
        with session_scope(self.sessions) as session:
            output_library(session, workspace_id, base_id)
            artifact = output_version(
                session, workspace_id, base_id, artifact_id, latest=False
            )
            self._require_agent_output(artifact)
            records = latest_check_records(session, artifact)
            known = {key: row["verdicts"] for key, row in records.items()}
            artifact_kind = artifact.kind
            sections = split_document(artifact.content, artifact.kind)[1]
            if all(
                (key := section_hash(text)) in known and complete(known[key]) for text in sections
            ):
                return artifact_detail(session, artifact, check_records=records)
            by_ref = {
                citation.ref: evidence_from_citation(citation)
                for citation in (
                    ConversationCitationOut.model_validate(item)
                    for item in json.loads(artifact.citations_json)
                )
                if citation.ref is not None
            }
        with usage.job("outputs", artifact_kind, ref=artifact_id):
            checker = AskAgent(self.models)
            limit = OutputLimits().sentences
            checks = []
            for text in sections:
                digest = section_hash(text)
                state = known.get(digest)
                if state is None or not complete(state):
                    state = check_saved_part(checker, model, text, by_ref, state, limit=limit)
                checks.append({"hash": digest, "verdicts": state})
        with session_scope(self.sessions) as session:
            artifact = output_version(
                session, workspace_id, base_id, artifact_id, latest=False
            )
            latest = latest_check_records(session, artifact)
            for check in checks:
                saved = latest.get(check["hash"], {}).get("verdicts", {})
                found = check["verdicts"]
                check["verdicts"] = merge_verdicts(found, saved)
            session.add(
                ArtifactCheck(
                    id=_id("chk"),
                    artifact_id=artifact.id,
                    content_hash=artifact.content_hash,
                    checks_json=json.dumps({"sections": checks}, ensure_ascii=False),
                )
            )
            session.flush()
            return artifact_detail(session, artifact)
