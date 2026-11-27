"""First-version approval, replay and attempt fencing through the Studio endpoints."""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fake_models import FakeProvider, is_orchestrating
from sqlalchemy import event, func, select
from test_artifact_agent import conversations, done, kept, versions_of
from test_outputs import app_for, in_background, library, output_turn, route, workspace
from test_search_and_build import web_hit
from test_studio_pipeline import request_text, router
from test_studio_plan_stages import dispatch

from evals.sse import read_events
from gunther import artifact_conversations
from gunther.answer_runs import AnswerRuns, output_key
from gunther.artifact_plans import PlanConflictError
from gunther.artifact_turn_result import TurnResult
from gunther.artifact_versions import commit_version
from gunther.artifact_workspace import Line
from gunther.evidence import Tool
from gunther.models import (
    Artifact,
    ArtifactConversation,
    ArtifactMessage,
    Asset,
    DocumentElement,
    ElementAsset,
    KnowledgeSession,
    SessionMessage,
    SourceIndexHead,
)
from gunther.output_material import reviewed_studio_material
from gunther.outputs import FirstWrite, Pool
from gunther.schemas import ArtifactTurnInput, RevisePlanTurnInput
from gunther.studio.pipeline import StudioAgents


def plan_request(plan_id="draft_review", *, type_id="report", **options):
    return {
        "kind": "plan",
        "clientRequestId": f"plan_{uuid4().hex}",
        "planId": plan_id,
        "setup": {"type": type_id, "scope": {"mode": "library"}, **options},
    }


def approve_request(plan, request_id=None):
    return {
        "kind": "approve",
        "clientRequestId": request_id or f"approve_{uuid4().hex}",
        "planId": plan["planId"],
        "planRevision": plan["planRevision"],
    }


def detail(client, base, cid):
    return (
        client.get(conversations(base, f"/{cid}"), headers=workspace(client))
        .raise_for_status()
        .json()
    )


def plan_worker(request):
    if is_orchestrating(request):
        return json.dumps({"actions": [{"do": "plan", "change": "whole"}]})
    if "Write the report" in (task := request_text(request)):
        marker = "Use exactly these approved section headings in order."
        rows, _ = json.JSONDecoder().raw_decode(task.split(marker, 1)[1].lstrip())
        return "# Findings\n" + "".join(
            f"\n## {row['heading']}\nThe trial establishes the outcome [1].\n" for row in rows
        )
    return router(request)


def test_client_addressed_plan_reuses_material_and_seals_the_approved_revision(
    tmp_path, monkeypatch
):
    fake = FakeProvider(plan_worker)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "review_from_setup"
        setup = plan_request(focus="Summarize the trial", title="Trial findings")
        events = output_turn(client, base, cid, setup)
        planned = done(events)
        plan = planned["plan"]
        assert planned["outcome"] == "planned" and plan["planStatus"] == "ready"
        assert versions_of(client) == []
        assert [event for name, event in events if name == "plan"] == [
            {
                "type": "plan",
                "conversationId": cid,
                "workKey": setup["planId"],
                "editVersion": planned["editVersion"],
                "plan": plan,
            }
        ]
        assert not any("Write the report" in request_text(call) for call in fake.requests)
        assert detail(client, base, cid)["works"][0]["plan"] == plan
        [summary] = client.get(conversations(base), headers=workspace(client)).json()
        assert summary["studioWorks"][0]["planId"] == plan["planId"]

        service = client.app.state.knowledge_service
        assert service.answer_runs is client.app.state.answer_runs

        def unexpected(*args, **kwargs):
            pytest.fail("Approval reread selected material.")

        monkeypatch.setattr(service, "_studio_reading", unexpected)
        monkeypatch.setattr(service, "_studio_web", unexpected)
        calls = len(fake.requests)
        assert done(output_turn(client, base, cid, setup)) == planned
        assert len(fake.requests) == calls
        approve = approve_request(plan)
        saved = done(output_turn(client, base, cid, approve))
        artifact = saved["artifact"]
        assert saved["outcome"] == "saved" and saved["plan"]["planStatus"] == "saved"
        assert artifact["versionNumber"] == 1 and artifact["conversationId"] == cid
        assert artifact["provenance"]["creationPlan"]["revision"] == plan["planRevision"]
        assert artifact["provenance"]["creationPlan"]["outline"] == plan["outline"]
        assert artifact["provenance"]["creationPlan"]["options"] == plan["options"]
        assert sum("Write the report" in request_text(call) for call in fake.requests[calls:]) == 1
        assert not any(
            "lead analyst" in request_text(call)
            or "planning a written report" in request_text(call)
            for call in fake.requests[calls:]
        )

        [work] = kept(client, cid)[0].works
        assert work.plan.approval.first_version_id == artifact["id"]
        assert work.plan.attempt is None
        assert work.key == artifact["lineageId"]
        with service.sessions() as session:
            sealed = json.loads(session.get(Artifact, artifact["id"]).provenance_json)
        assert sealed["creationPlan"] == artifact["provenance"]["creationPlan"]
        assert sealed["effectivePreferences"] == plan["preferences"]

        edited = (
            client.post(
                route(base, artifact["id"], "/edits"),
                headers=workspace(client),
                json={
                    "clientRequestId": "literal_child_request",
                    "content": artifact["content"] + "\n",
                },
            )
            .raise_for_status()
            .json()
        )
        assert edited["provenance"]["creationVersionId"] == artifact["id"]
        assert edited["provenance"]["creationPlan"] == sealed["creationPlan"]
        with service.sessions() as session:
            child = json.loads(session.get(Artifact, edited["id"]).provenance_json)
        assert child["creationPlanRef"] == {
            "firstVersionId": artifact["id"],
            "planId": plan["planId"],
            "revision": plan["planRevision"],
        }
        assert "creationPlan" not in child

        calls = len(fake.requests)
        service.models = None
        assert done(output_turn(client, base, cid, approve)) == saved
        conflict = output_turn(client, base, cid, approve | {"planId": "draft_other"})
        assert conflict[-1][1]["status"] == 409 and len(fake.requests) == calls


@pytest.mark.parametrize("old_hash", [False, True], ids=["current", "historical"])
@pytest.mark.parametrize("explicit_clear", [False, True], ids=["inherit", "clear"])
def test_setup_replay_distinguishes_inheriting_and_clearing_preferences(
    tmp_path, explicit_clear, old_hash,
):
    fake = FakeProvider(plan_worker)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "setup_replay"
        headers = workspace(client)
        client.post(conversations(base), headers=headers, json={"id": cid}).raise_for_status()
        preference = {"id": "avoid_detail", "text": "Leave out procedural detail"}
        service = client.app.state.knowledge_service
        with service.sessions.begin() as session:
            row = session.get(ArtifactConversation, cid)
            row.brief_json = json.dumps({"avoid": [preference | {"mark": "you"}]})

        omitted = plan_request()
        cleared = omitted | {"setup": omitted["setup"] | {"avoid": []}}
        submitted, changed = (cleared, omitted) if explicit_clear else (omitted, cleared)
        planned = done(output_turn(client, base, cid, submitted))
        assert planned["plan"]["preferences"]["avoid"] == (
            [] if explicit_clear else [preference | {"mark": "you"}]
        )
        calls = len(fake.requests)
        if old_hash:
            command = ArtifactTurnInput.model_validate(submitted).root
            fingerprint = service._output_fingerprint(
                headers["X-Gunther-Workspace-Id"], base, "artifact-turn", {
                    "conversationId": cid,
                    **command.model_dump(mode="json", by_alias=True, exclude={"client_request_id"}),
                },
            )
            with service.sessions.begin() as session:
                row = session.scalar(select(ArtifactMessage).where(
                    ArtifactMessage.conversation_id == cid, ArtifactMessage.role == "user",
                ))
                row.payload_json = json.dumps(
                    json.loads(row.payload_json) | {"requestFingerprint": fingerprint},
                )

        name, conflict = output_turn(client, base, cid, changed)[-1]
        assert name == "error" and conflict["status"] == 409
        assert "already used" in conflict["detail"]
        with client.stream(
            "POST", conversations(base, f"/{cid}/messages/stream"),
            headers=headers, json=submitted,
        ) as replay:
            assert replay.status_code == 200
            assert done(read_events(replay)) == planned
        assert len(fake.requests) == calls


def test_direct_patch_is_cas_and_approves_the_edited_topics_and_exact_count(tmp_path):
    direct = dispatch("flashcards")
    fake = FakeProvider(lambda call: plan_worker(call) if is_orchestrating(call) else direct(call))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "review_cards"
        first = done(output_turn(client, base, cid, plan_request(type_id="flashcards")))
        plan = first["plan"]
        path = conversations(base, f"/{cid}/works/{plan['planId']}")
        headers = workspace(client)
        patch = {
            "baseEditVersion": first["editVersion"],
            "coverage": [{"id": "topic_cooling", "text": "Cooling electricity"}],
            "count": {"mode": "exact", "value": 2},
        }
        calls = len(fake.requests)
        bad = client.patch(
            path, headers=headers, json=patch | {"count": {"mode": "exact", "value": 900}}
        )
        assert bad.status_code == 422
        edited = client.patch(path, headers=headers, json=patch).raise_for_status().json()
        assert len(fake.requests) == calls and versions_of(client) == []
        [work] = edited["works"]
        reviewed = work["plan"]
        assert reviewed["basis"] == "user_edited" and reviewed["planStatus"] == "ready"
        assert reviewed["proposal"]["count"] == patch["count"]
        assert reviewed["proposal"]["coverage"] == patch["coverage"]
        assert edited["editVersion"] == first["editVersion"] + 1
        conflict = client.patch(path, headers=headers, json=patch)
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "edit_conflict"
        again = client.patch(
            path, headers=headers, json=patch | {"baseEditVersion": edited["editVersion"]}
        )
        assert again.raise_for_status().json() == edited

        stale = output_turn(client, base, cid, approve_request(plan))[-1]
        assert stale[0] == "error" and stale[1]["code"] == "plan_changed"
        assert len(fake.requests) == calls
        artifact = done(output_turn(client, base, cid, approve_request(reviewed)))["artifact"]
        assert artifact["provenance"]["creationPlan"]["proposal"] == reviewed["proposal"]
        assert len(artifact["sections"]) == 2


def test_planned_answer_checks_the_pending_message_and_preserves_selected_text(tmp_path):
    asking = True

    def reply(request):
        if asking and is_orchestrating(request):
            return json.dumps(
                {
                    "actions": [
                        {
                            "do": "ask",
                            "question": "Which scope?",
                            "options": ["Trial", "Comparison"],
                        },
                    ]
                }
            )
        return plan_worker(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "planning_question"
        asked = done(output_turn(client, base, cid, plan_request()))
        plan = asked["plan"]
        assert plan["planStatus"] == "needs_answer"
        assert detail(client, base, cid)["pending"]["messageId"] == plan["pending"]["messageId"]
        answer = {
            "kind": "answer",
            "clientRequestId": "planning_answer_request",
            "planId": plan["planId"],
            "planRevision": plan["planRevision"],
            "questionMessageId": "acm_older",
            "answer": {"type": "choice", "index": 1},
        }
        calls = len(fake.requests)
        for kind in ("message", "answer", "start"):
            name, denied = output_turn(client, base, cid, {
                "kind": kind,
                "clientRequestId": f"unbound_{kind}_request",
                "text": "An unrelated note",
            })[-1]
            assert name == "error" and denied["code"] == "question_changed"
        assert detail(client, base, cid)["works"][0]["plan"] == plan
        stale = output_turn(client, base, cid, answer)[-1][1]
        assert stale["status"] == 409 and stale["code"] == "question_changed"
        answer["questionMessageId"] = plan["pending"]["messageId"]
        stale_revision = output_turn(
            client, base, cid, answer | {"planRevision": "p0-" + "0" * 64},
        )[-1][1]
        assert stale_revision["status"] == 409 and stale_revision["code"] == "plan_changed"
        invalid = output_turn(
            client, base, cid, answer | {"answer": {"type": "choice", "index": 9}}
        )
        assert invalid[-1][1]["status"] == 422 and len(fake.requests) == calls
        asking = False
        replied = done(output_turn(client, base, cid, answer))
        assert replied["plan"]["planStatus"] == "ready"
        state = detail(client, base, cid)
        assert state["pending"] is None
        assert state["messages"][-2]["text"] == "Comparison"
        assert state["messages"][-2]["payload"]["answer"] == answer["answer"]
        assert versions_of(client) == []


@pytest.mark.parametrize("same_boot", [False, True])
def test_missing_attempt_cannot_checkpoint_stop_or_complete_before_replacement(
    tmp_path, monkeypatch, same_boot,
):
    fake = FakeProvider(plan_worker)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "recoverable_plan"
        plan = done(output_turn(client, base, cid, plan_request()))["plan"]
        headers = workspace(client)
        service = client.app.state.knowledge_service
        registry = service.answer_runs
        old_registry = registry if same_boot else AnswerRuns()
        monkeypatch.setattr(service, "answer_runs", old_registry)
        key = output_key(cid)
        scope = (headers["X-Gunther-Workspace-Id"], base)
        command = RevisePlanTurnInput(
            kind="plan",
            client_request_id="interrupted_request",
            plan_id=plan["planId"],
            plan_revision=plan["planRevision"],
        )
        fingerprint = service._turn_fingerprints(*scope, cid, ArtifactTurnInput(command))[0]
        finish = threading.Event()

        def hold(run):
            assert finish.wait(10)
            if not run.finished:
                run.publish("stopped", {"type": "stopped"})

        old = old_registry.start(key, "Plan", hold, scope=scope)
        assert old is not None
        turns = []
        state_before = None

        def raw_state():
            with service.sessions() as session:
                return session.get(ArtifactConversation, cid).state_json

        def orphaned_agent(_base, _workspace_id, _cid, _workspace, brief, *_, admitted, **_kwargs):
            nonlocal state_before
            turn = admitted
            if same_boot:
                old.publish("error", {"status": 500, "detail": "The worker disappeared."})
            else:
                monkeypatch.setattr(service, "answer_runs", registry)
            assert not registry.matches(
                key, scope=scope, server_boot_id=old.server_boot_id, attempt_id=old.attempt_id,
            )
            assert (
                detail(client, base, cid)["works"][0]["plan"]["attempt"]["state"] == "interrupted"
            )
            state_before = raw_state()
            with pytest.raises(PlanConflictError, match="different attempt"):
                service._checkpoint_plan(base, scope[0], cid, turn, {}, Pool(1))
            service.stop_artifact_plan(base, scope[0], cid, turn.plan_id, turn.request_id, old)
            assert raw_state() == state_before
            turns.append(turn)
            return SimpleNamespace(brief=brief, result=TurnResult("replied", "Late reply."))

        monkeypatch.setattr(service, "_run_output_agent", orphaned_agent)
        try:
            with pytest.raises(PlanConflictError, match="different attempt"):
                service._creation_turn(
                    base, scope[0], cid, command, old, fingerprint, lambda _: None,
                )
            assert raw_state() == state_before
            assert all(m["role"] == "user" or m["text"] != "Late reply."
                       for m in detail(client, base, cid)["messages"])
            [turn] = turns
            new = registry.start(key, "New plan", hold, scope=scope)
            assert new is not None
            newer = command.model_copy(update={"client_request_id": "replacement_request"})
            fingerprint = service._turn_fingerprints(*scope, cid, ArtifactTurnInput(newer))[0]
            service._admit_plan(base, scope[0], cid, newer, new, fingerprint)
            with pytest.raises(PlanConflictError, match="different attempt"):
                service._checkpoint_plan(base, scope[0], cid, turn, {}, Pool(1))
            assert detail(client, base, cid)["works"][0]["plan"]["attempt"]["state"] == "running"
        finally:
            finish.set()


def test_orphaned_approval_cannot_publish_even_before_replacement(tmp_path):
    with app_for(tmp_path, FakeProvider(plan_worker)) as client:
        base, _ = library(client)
        cid = "orphaned_publish"
        plan = done(output_turn(client, base, cid, plan_request()))["plan"]
        command = ArtifactTurnInput.model_validate(approve_request(plan))
        service = client.app.state.knowledge_service
        registry = service.answer_runs
        scope = (workspace(client)["X-Gunther-Workspace-Id"], base)
        finish = threading.Event()
        run = registry.start(output_key(cid), "Approve", lambda _: finish.wait(10), scope=scope)
        assert run is not None
        try:
            fingerprint = service._turn_fingerprints(*scope, cid, command)[0]
            turn, _, _ = service._admit_plan(base, scope[0], cid, command.root, run, fingerprint)
            assert turn.authority is not None
            [work] = kept(client, cid)[0].works
            assert work.plan.approval.revision == plan["planRevision"]
            run.publish("error", {"status": 500, "detail": "The worker disappeared."})
            interrupted = detail(client, base, cid)["works"][0]["plan"]["attempt"]
            assert interrupted["state"] == "interrupted"
            with pytest.raises(PlanConflictError, match="no longer live"):
                commit_version(
                    service.sessions, workspace_id=scope[0], base_id=base,
                    client_request_id=turn.request_id, fingerprint=fingerprint, parent_id=None,
                    kind="report", style="report", audience="scientist", title="Findings",
                    brief="", origin="build", content="# Findings", scope=plan["options"]["scope"],
                    inputs={"sources": [
                        {"id": pin["id"], "revisionId": pin["revision"]}
                        for pin in turn.manifest["members"]
                    ]},
                    outline=plan["outline"], citations=[], snapshots=[], model=None, checks=[],
                    publish=lambda *_: pytest.fail("An orphan tried to publish."),
                    first_write=turn.authority, answer_runs=registry,
                )
            assert versions_of(client) == []
        finally:
            finish.set()


def test_storage_rejects_missing_and_forged_first_write_before_insert(tmp_path):
    fake = FakeProvider(plan_worker)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "storage_gate"
        planned = done(output_turn(client, base, cid, plan_request()))
        plan = planned["plan"]
        headers = workspace(client)
        workspace_id = headers["X-Gunther-Workspace-Id"]
        service = client.app.state.knowledge_service
        request = {
            "workspace_id": workspace_id,
            "base_id": base,
            "client_request_id": "unapproved_request",
            "fingerprint": "not_an_approval",
            "parent_id": None,
            "kind": "report",
            "style": "report",
            "audience": "scientist",
            "title": "Report",
            "brief": "",
            "origin": "build",
            "content": "# Report",
            "scope": {"mode": "library"},
            "inputs": {},
            "outline": [],
            "citations": [],
            "snapshots": [],
            "model": None,
            "checks": [],
        }
        forged = FirstWrite(
            workspace_id,
            base,
            cid,
            plan["planId"],
            plan["planRevision"],
            request["client_request_id"],
            service.answer_runs.server_boot_id,
            str(uuid4()),
        )
        for permit in (None, forged, replace(forged, work_key="missing")):
            with pytest.raises(PlanConflictError) as error:
                commit_version(
                    service.sessions,
                    **request,
                    first_write=permit,
                    answer_runs=service.answer_runs,
                    publish=lambda *_: pytest.fail("The gate allowed publication."),
                )
            assert error.value.code == "plan_required"
        assert versions_of(client) == []


def test_live_generation_blocks_edits_and_trash_until_the_same_run_is_stopped(
    tmp_path, monkeypatch
):
    fake = FakeProvider(plan_worker)
    entered, release = threading.Event(), threading.Event()
    generate = StudioAgents.generate

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return generate(*args, **kwargs)

    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "stop_the_review"
        first = done(output_turn(client, base, cid, plan_request()))
        plan = first["plan"]
        headers = workspace(client)
        path = conversations(base, f"/{cid}")
        patch_path = f"{path}/works/{plan['planId']}"
        request = approve_request(plan)
        monkeypatch.setattr(StudioAgents, "generate", hold)
        job, results = in_background(lambda: output_turn(client, base, cid, request))
        try:
            assert entered.wait(10)
            current = detail(client, base, cid)
            assert current["works"][0]["plan"]["planStatus"] == "generating"
            equal = client.patch(
                patch_path,
                headers=headers,
                json={
                    "baseEditVersion": current["editVersion"],
                    "options": {"focus": ""},
                },
            )
            assert equal.status_code == 200
            for patch_url, changes in (
                (
                    patch_path,
                    {"baseEditVersion": current["editVersion"], "options": {"focus": "Changed"}},
                ),
                (f"{path}/brief", {"constraints": ["Use plain language"]}),
            ):
                blocked = client.patch(patch_url, json=changes, headers=headers)
                assert (
                    blocked.status_code == 409 and blocked.json()["detail"]["code"] == "run_active"
                )
            assert client.post(f"{path}/trash", headers=headers).status_code == 409
            conflict = output_turn(client, base, cid, request)[-1][1]
            assert conflict["status"] == 409 and conflict["detail"]["code"] == "run_active"
            assert conflict["detail"]["follow"] == f"{path}/stream"
            assert client.app.state.activity.stop(output_key(cid))
            assert detail(client, base, cid)["works"][0]["plan"]["attempt"]["state"] == "stopped"
        finally:
            release.set()
            job.join(10)
        assert results[0][-1][0] == "stopped" and versions_of(client) == []
        state = detail(client, base, cid)
        assert state["works"][0]["plan"]["planStatus"] == "ready"
        stopped = [message for message in state["messages"] if message["kind"] == "stopped"]
        assert len(stopped) == 1
        [work] = kept(client, cid)[0].works
        assert work.plan.approval is None

        trashed = client.post(f"{path}/trash", headers=headers).raise_for_status().json()
        client.post(
            f"/api/trash/output/{trashed['id']}/restore", headers=headers
        ).raise_for_status()
        assert detail(client, base, cid) == state
        assert client.get(f"{path}/stream", headers=headers).status_code == 204


def test_stop_before_initial_admission_leaves_no_conversation_and_retries_cleanly(
    tmp_path, monkeypatch
):
    fake = FakeProvider(plan_worker)
    entered, release = threading.Event(), threading.Event()
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "pre_admission_stop"
        request = plan_request()
        service = client.app.state.knowledge_service
        admit = service._admit_plan

        def hold(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return admit(*args, **kwargs)

        monkeypatch.setattr(service, "_admit_plan", hold)
        job, results = in_background(lambda: output_turn(client, base, cid, request))
        try:
            assert entered.wait(10)
            stop = client.post(conversations(base, f"/{cid}/stop"), headers=workspace(client))
            assert stop.status_code == 204
        finally:
            release.set()
            job.join(10)
        assert results[0][-1][0] == "stopped"
        assert len(fake.requests) == 0
        with service.sessions() as session:
            assert session.get(ArtifactConversation, cid) is None
        assert done(output_turn(client, base, cid, request))["outcome"] == "planned"


def test_scope_edit_wins_over_a_proposal_already_running(tmp_path, monkeypatch):
    fake = FakeProvider(plan_worker)
    entered, release = threading.Event(), threading.Event()
    propose = StudioAgents.propose

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return propose(*args, **kwargs)

    with app_for(tmp_path, fake) as client:
        base, sources = library(client)
        cid = "concurrent_scope_edit"
        first = done(output_turn(client, base, cid, plan_request()))
        plan = first["plan"]
        monkeypatch.setattr(StudioAgents, "propose", hold)
        request = {
            "kind": "plan",
            "clientRequestId": "revise_concurrent_request",
            "planId": plan["planId"],
            "planRevision": plan["planRevision"],
            "text": "Try again.",
        }
        job, results = in_background(lambda: output_turn(client, base, cid, request))
        try:
            assert entered.wait(10)
            scope = {"mode": "selection", "sourceIds": [next(iter(sources.values()))]}
            current = detail(client, base, cid)
            patched = (
                client.patch(
                    conversations(base, f"/{cid}"),
                    headers=workspace(client),
                    json={"scope": scope},
                )
                .raise_for_status()
                .json()
            )
            assert patched["editVersion"] == current["editVersion"] + 1
        finally:
            release.set()
            job.join(10)
        paused = done(results[0])
        assert paused["outcome"] == "paused"
        assert paused["plan"]["options"]["scope"]["sourceIds"] == scope["sourceIds"]
        assert paused["plan"]["planStatus"] == "planning" and paused["plan"]["blocker"]
        assert versions_of(client) == []


@pytest.mark.parametrize(
    ("refs", "required_refs", "blocked"),
    [([1, 3], [], False), ([3], [], True), ([1, 3], [3], True)],
)
def test_web_off_keeps_local_pins_and_identifies_rows_with_web_dependencies(
    tmp_path,
    monkeypatch,
    refs,
    required_refs,
    blocked,
):
    def reply(request):
        if "planning a written report" in request_text(request):
            return json.dumps({
                "title": "Findings",
                "sections": [{
                    "heading": "Trial outcomes", "job": "Describe the outcome",
                    "refs": refs, "required_refs": required_refs,
                }],
            })
        return plan_worker(request)

    fake = FakeProvider(reply)
    queries = []

    def search(query):
        queries.append(query)
        return [web_hit("Outside evidence", "https://example.org/analysis")]

    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        service = client.app.state.knowledge_service
        web = Tool("search_web", "Recorded results", search, "web")
        monkeypatch.setattr(service, "_web_tool", lambda enabled: (web if enabled else None, None))
        cid = "review_local_material"
        first = done(output_turn(client, base, cid, plan_request(web=True)))
        plan = first["plan"]
        assert plan["planStatus"] == "ready" and len(queries) == 1
        state, _ = kept(client, cid)
        [work] = state.works
        reference = next(item["ref"] for item in state.pool if item["kind"] == "web")
        assert reference == 3
        row = plan["outline"][1]
        assert row["refs"] == work.outline[1].refs == refs
        assert row["requiredRefs"] == work.outline[1].required_refs == required_refs
        path = conversations(base, f"/{cid}/works/{plan['planId']}")
        headers = workspace(client)
        calls = len(fake.requests)
        patched = (
            client.patch(
                path, headers=headers,
                json={"baseEditVersion": first["editVersion"], "options": {"web": False}},
            )
            .raise_for_status()
            .json()
        )
        edited = patched["works"][0]["plan"]
        assert len(fake.requests) == calls and len(queries) == 1
        if blocked:
            assert edited["planStatus"] == "planning"
            assert row["id"] in edited["blocker"]
            denied = output_turn(client, base, cid, approve_request(edited))[-1][1]
            assert denied["status"] == 409 and denied["code"] == "plan_changed"
            outline = [
                {key: part.get(key, "") for key in ("id", "heading", "goal", "layout", "notes")}
                for part in edited["outline"]
            ]
            outline[1].update(heading="Local trial outcomes", goal="Explain the local findings")
            edited = client.patch(path, headers=headers, json={
                "baseEditVersion": patched["editVersion"], "outline": outline,
            }).raise_for_status().json()["works"][0]["plan"]
            assert edited["outline"][1]["id"] == row["id"]
            assert edited["outline"][1]["refs"] == edited["outline"][1]["requiredRefs"] == []
        assert edited["planStatus"] == "ready" and edited["blocker"] is None
        artifact = done(output_turn(client, base, cid, approve_request(edited)))["artifact"]
        assert not artifact["provenance"]["creationPlan"]["options"]["web"]
        assert all(item["kind"] != "web" for item in artifact["citations"])
        assert len(queries) == 1


@pytest.mark.parametrize("field", ["searched", "key_results_used"])
def test_web_off_converter_rejects_conflicting_outer_provenance(tmp_path, monkeypatch, field):
    with app_for(tmp_path, FakeProvider(plan_worker)) as client:
        base, _ = library(client)
        cid = "conflicting_provenance"
        service = client.app.state.knowledge_service
        web = Tool("search_web", "Recorded results", lambda _: [
            web_hit("Outside evidence", "https://example.org/analysis"),
        ], "web")
        monkeypatch.setattr(service, "_web_tool", lambda enabled: (web if enabled else None, None))
        done(output_turn(client, base, cid, plan_request(web=True)))
        state, _ = kept(client, cid)
        [work] = state.works
        scope = work.spec.scope
        base_args = workspace(client)["X-Gunther-Workspace-Id"], base, scope, state, work
        with service.sessions() as session:
            clean = reviewed_studio_material(session, *base_args, local_only=True)
            assert clean.key and clean.blocker is None
            assert all(hit["kind"] != "web" for hit in clean.progress["manifest"]["searched"])
            work.studio_progress[field] = [{"kind": "library", "id": "wrong", "refs": [1]}]
            altered = reviewed_studio_material(session, *base_args, local_only=True)
            assert altered.key is None and altered.progress is None and altered.blocker


def test_patch_acknowledges_its_own_revision_when_another_edit_commits(tmp_path, monkeypatch):
    with app_for(tmp_path, FakeProvider(plan_worker)) as client:
        base, _ = library(client)
        cid = "patch_ack"
        first = done(output_turn(client, base, cid, plan_request()))
        plan = first["plan"]
        service = client.app.state.knowledge_service
        original = artifact_conversations.session_scope
        wrote_again = False

        @contextmanager
        def write_after_commit(factory):
            nonlocal wrote_again
            with original(factory) as session:
                yield session
            if wrote_again:
                return
            wrote_again = True
            with original(factory) as session:
                row = session.get(ArtifactConversation, cid)
                state, brief = service._load_workspace(row)
                brief.constraints.append(Line(text="A concurrent constraint", mark="edited"))
                service._mark_plan_edit(state, state.works[0], brief)
                state.edit_version += 1
                service._store_workspace(row, state, brief)

        monkeypatch.setattr(artifact_conversations, "session_scope", write_after_commit)
        ack = client.patch(
            conversations(base, f"/{cid}/works/{plan['planId']}"), headers=workspace(client),
            json={"baseEditVersion": first["editVersion"], "options": {"focus": "Your focus"}},
        ).raise_for_status().json()
        assert wrote_again and ack["editVersion"] == first["editVersion"] + 1
        assert ack["brief"]["constraints"] == []
        assert ack["works"][0]["plan"]["options"]["focus"] == "Your focus"
        latest = detail(client, base, cid)
        assert latest["editVersion"] == ack["editVersion"] + 1
        assert latest["brief"]["constraints"][0]["text"] == "A concurrent constraint"
        assert latest["works"][0]["plan"]["planRevision"] != ack["works"][0]["plan"]["planRevision"]


def test_model_feedback_cannot_change_the_plan_catalog_type(tmp_path):
    feedback = "Expand the figures."

    def reply(request):
        if is_orchestrating(request) and feedback in request_text(request):
            return json.dumps({
                "brief": [{"field": "output_type", "text": "faq", "quote": "the figures"}],
                "actions": [{"do": "plan", "change": "whole"}],
            })
        return plan_worker(request)

    with app_for(tmp_path, FakeProvider(reply)) as client:
        base, _ = library(client)
        cid = "immutable_recipe"
        plan = done(output_turn(client, base, cid, plan_request()))["plan"]
        revised = done(output_turn(client, base, cid, {
            "kind": "plan", "clientRequestId": "feedback_recipe_request",
            "planId": plan["planId"], "planRevision": plan["planRevision"], "text": feedback,
        }))["plan"]
        assert revised["planStatus"] == "ready" and revised["options"]["type"] == "report"
        assert kept(client, cid)[0].works[0].spec.chosen_output_type == "report"


@pytest.mark.parametrize("when", ["before_approval", "before_publication"])
def test_reviewed_figure_blob_integrity_is_checked_before_write(tmp_path, monkeypatch, when):
    with app_for(tmp_path, FakeProvider(plan_worker)) as client:
        base, sources = library(client)
        cid = "figure_integrity"
        service = client.app.state.knowledge_service
        source_id = next(iter(sources.values()))
        asset_id = f"ast_{uuid4().hex}"
        element_id = "reviewed_figure_element"
        blob = b"The original managed figure"
        content_hash = hashlib.sha256(blob).hexdigest()
        path = tmp_path / "assets" / "figure.png"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(blob)
        with service.sessions.begin() as session:
            revision = session.get(SourceIndexHead, source_id).revision_id
            ordinal = session.scalar(select(func.max(DocumentElement.ordinal)).where(
                DocumentElement.revision_id == revision,
            ))
            session.add_all([
                Asset(
                    id=asset_id, content_hash=content_hash, original_name=path.name,
                    media_type="image/png", size_bytes=len(blob), relative_path=path.name,
                ),
                DocumentElement(
                    id=element_id, revision_id=revision, ordinal=ordinal + 1, kind="figure",
                    content="Reviewed result", locator="Figure 1",
                    payload_json=json.dumps({"imageAssetId": asset_id}),
                ),
            ])
            session.flush()
            session.add(ElementAsset(element_id=element_id, asset_id=asset_id))
        plan = done(output_turn(client, base, cid, plan_request()))["plan"]
        assert plan["planStatus"] == "ready"
        [work] = kept(client, cid)[0].works
        assert work.studio_progress["manifest"]["assets"] == {asset_id: content_hash}
        downloads = []
        reader = service.asset_reader

        def read_without_writer_lock(requested_id):
            # A separate writer can lock SQLite while the asset is being verified.
            with sqlite3.connect(tmp_path / "gunther.sqlite", timeout=0.2) as connection:
                connection.execute("BEGIN IMMEDIATE")
            downloads.append(requested_id)
            return reader(requested_id)

        monkeypatch.setattr(service, "asset_reader", read_without_writer_lock)
        if when == "before_approval":
            path.unlink()
        else:
            publish = service._publish_turn

            def corrupt_at_publish(*args, **kwargs):
                path.write_bytes(b"x" + blob[1:])
                return publish(*args, **kwargs)

            monkeypatch.setattr(service, "_publish_turn", corrupt_at_publish)
        name, error = output_turn(client, base, cid, approve_request(plan))[-1]
        assert name == "error" and error["code"] == "plan_changed" and "figure" in error["detail"]
        assert downloads == [asset_id] * (1 if when == "before_approval" else 2)
        assert versions_of(client) == []
        with service.sessions() as session:
            assert session.get(Asset, asset_id).content_hash == content_hash
            assert session.get(ElementAsset, (element_id, asset_id)) is not None


def test_frozen_discussion_approval_does_not_load_message_bodies_or_passages(tmp_path):
    with app_for(tmp_path, FakeProvider(plan_worker)) as client:
        base, sources = library(client)
        headers = workspace(client)
        cid = "frozen_discussion"
        service = client.app.state.knowledge_service
        discussion_id = "chosen_discussion"
        with service.sessions.begin() as session:
            session.add(KnowledgeSession(
                id=discussion_id, knowledge_base_id=base, title="Prior discussion",
                summary="Earlier interpretation of the trial.",
            ))
            session.flush()
            session.add(SessionMessage(
                id="stored_discussion_answer", session_id=discussion_id, role="assistant",
                content="This prior answer belongs to the chosen discussion.",
            ))
            engine = session.get_bind()
        loads = []

        def inspect(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().startswith("SELECT") and any(
                column in statement for column in (
                    "session_messages.content", "session_messages.citations_json",
                    "FROM content_blocks",
                )
            ):
                loads.append(statement)

        event.listen(engine, "before_cursor_execute", inspect)
        try:
            setup = plan_request(scope={
                "mode": "selection", "sourceIds": [next(iter(sources.values()))],
                "sessionIds": [discussion_id],
            })
            plan = done(output_turn(client, base, cid, setup))["plan"]
            assert any("session_messages.content" in query for query in loads)
            assert any("FROM content_blocks" in query for query in loads)
            loads.clear()
            assert detail(client, base, cid)["works"][0]["plan"] == plan
            saved = done(output_turn(client, base, cid, approve_request(plan), headers))
            assert saved["artifact"]["inputs"]["sessions"] == 1
            assert loads == []
        finally:
            event.remove(engine, "before_cursor_execute", inspect)


def test_selection_eligibility_ignores_unrelated_additions_and_blocks_removed_pins(tmp_path):
    fake = FakeProvider(plan_worker)
    with app_for(tmp_path, fake) as client:
        base, sources = library(client)
        headers = workspace(client)
        selected = next(iter(sources.values()))
        cid = "review_selected_material"
        first = done(
            output_turn(
                client,
                base,
                cid,
                plan_request(scope={"mode": "selection", "sourceIds": [selected]}),
            )
        )
        plan = first["plan"]
        client.post(
            "/api/sources",
            headers=headers,
            json={
                "title": "Unrelated",
                "kind": "note",
                "knowledgeBaseId": base,
                "content": "Other data.",
            },
        ).raise_for_status()
        assert detail(client, base, cid)["works"][0]["plan"] == plan
        client.post(f"/api/sources/{selected}/trash", headers=headers).raise_for_status()
        assert detail(client, base, cid)["works"][0]["plan"]["planStatus"] == "planning"
        calls = len(fake.requests)
        refused = output_turn(client, base, cid, approve_request(plan))[-1][1]
        assert refused["status"] == 409 and refused["planRevision"] == plan["planRevision"]
        assert len(fake.requests) == calls and versions_of(client) == []
