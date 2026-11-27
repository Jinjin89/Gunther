"""Output Trash through the same API as the readers, runs and history."""

import hashlib
from threading import Event

from fake_models import FakeProvider, agent_replies
from sqlalchemy import select, text
from test_artifact_agent import done
from test_outputs import (
    ONE_SECTION,
    SIDECAR,
    activity_of,
    app_for,
    body,
    build,
    built,
    in_background,
    library,
    output_replies,
    output_turn,
    plan_and_approve,
    route,
    saved_output,
    workspace,
)
from test_plan_approval import plan_request, plan_worker

from evals.sse import ask_turn
from gunther.models import (
    Artifact,
    ArtifactAsset,
    ArtifactCheck,
    ArtifactMessage,
    ArtifactUnitBinding,
    Asset,
)


def report_reply():
    return output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])


def edit(client, base, artifact, headers):
    return client.post(route(base, artifact["id"], "/edits"), headers=headers, json={
        "clientRequestId": body()["clientRequestId"],
        "content": artifact["content"] + "\n\nA revision.",
    }).raise_for_status().json()


def conversation_path(base, id_=""):
    return f"/api/knowledge-bases/{base}/artifact-conversations" + (f"/{id_}" if id_ else "")


def restore(client, entry, headers):
    return client.post(
        f"/api/trash/output/{entry['id']}/restore", headers=headers,
    ).raise_for_status().json()


def test_trashing_an_old_version_hides_the_lineage_and_restores_all_three(tmp_path):
    with app_for(tmp_path, FakeProvider(report_reply())) as client:
        base, _ = library(client)
        headers = workspace(client)
        request_id = body()["clientRequestId"]
        first = built(plan_and_approve(
            client, base, {"type": "report"}, conversation_id="field-report",
            request_id=request_id, headers=headers,
        ))["artifact"]
        creation = first["provenance"]["creationPlan"]
        approval = {
            "kind": "approve", "clientRequestId": request_id, "planId": creation["planId"],
            "planRevision": creation["revision"],
        }
        second = edit(client, base, first, headers)
        third = edit(client, base, second, headers)
        replayed = built(output_turn(client, base, "field-report", approval, headers))
        assert replayed["artifact"] == first
        versions = [first, second, third]
        paths = [route(base, v["id"]) for v in versions]
        history = conversation_path(base, "field-report")
        before = client.get(history, headers=headers).raise_for_status().json()
        assert len(before["works"]) == 1 and before["messages"][-1]["artifactId"] == first["id"]
        assert activity_of(client)["output-conv:field-report"]["open"]["artifactId"] == first["id"]

        entry = client.post(f"{paths[0]}/trash", headers=headers).raise_for_status().json()
        assert (entry["kind"], entry["title"], entry["outputType"], entry["versionCount"]) == (
            "output", third["title"], third["outputType"], 3,
        )
        assert entry["open"] == {
            "view": "library", "libraryId": base, "tab": "outputs",
            "artifactId": first["id"], "lineageId": first["lineageId"],
            "conversationId": "field-report", "typeId": "report", "planId": creation["planId"],
        }
        assert client.get("/api/trash", headers=headers).json() == [entry]
        assert client.get(f"/api/knowledge-bases/{base}/artifacts", headers=headers).json() == []
        for path in paths:
            assert client.get(path, headers=headers).status_code == 404
        render = client.get(
            f"{paths[0]}/renders/snapshot/contact.png", headers=headers,
            params={"workspace_id": headers["X-Gunther-Workspace-Id"]},
        )
        assert render.status_code == 404 and "Artifact" in render.json()["detail"]
        assert client.post(f"{paths[2]}/check", headers=headers).status_code == 404
        replay = output_turn(client, base, "field-report", approval, headers)
        assert replay[-1][0] == "error" and replay[-1][1]["status"] == 404
        assert "output-conv:field-report" not in activity_of(client)
        hidden = client.get(history, headers=headers).raise_for_status().json()
        assert hidden["works"] == [] and all(m["artifactId"] is None for m in hidden["messages"])
        [summary] = client.get(conversation_path(base), headers=headers).json()
        assert summary["artifactCount"] == 0 and summary["lineageId"] is None

        assert restore(client, entry, headers)["open"] == entry["open"]
        assert client.get("/api/trash", headers=headers).json() == []
        for path, saved in zip(paths, versions, strict=True):
            assert client.get(path, headers=headers).raise_for_status().json() == saved
        assert client.get(history, headers=headers).json() == before


def test_restore_keeps_the_unsaved_plan_address_after_restart(tmp_path):
    cid = "trashed_unsaved_plan"
    with app_for(tmp_path, FakeProvider(plan_worker)) as client:
        base, _ = library(client)
        headers = workspace(client)
        plan = done(output_turn(client, base, cid, plan_request(), headers))["plan"]
        entry = client.post(
            conversation_path(base, cid) + "/trash", headers=headers,
        ).raise_for_status().json()
        expected = {
            "view": "library", "libraryId": base, "tab": "outputs", "conversationId": cid,
            "typeId": "report", "planId": plan["planId"], "artifactId": None, "lineageId": None,
        }
        assert entry["open"] == expected

    fake = FakeProvider([])
    with app_for(tmp_path, fake) as client:
        headers = workspace(client)
        assert client.get("/api/trash", headers=headers).json() == [entry]
        assert restore(client, entry, headers)["open"] == expected
        reopened = client.get(
            conversation_path(base, cid), headers=headers,
        ).raise_for_status().json()
        assert reopened["works"][0]["plan"] == plan
        assert not fake.requests and activity_of(client) == {}


def test_an_output_run_refuses_trash_of_both_the_output_and_conversation(tmp_path):
    entered, release = Event(), Event()
    initial = True

    def pause(_request):
        if not initial:
            entered.set()
            assert release.wait(15)

    with app_for(tmp_path, FakeProvider(report_reply(), rule=pause)) as client:
        base, _ = library(client)
        headers = workspace(client)
        first = built(build(client, base, body(conversationId="field-report"), headers))
        initial = False
        thread, events = in_background(lambda: build(
            client, base, body(supersedesArtifactId=first["id"]), headers,
        ))
        try:
            assert entered.wait(10)
            for path in (route(base, first["id"]), conversation_path(base, "field-report")):
                response = client.post(f"{path}/trash", headers=headers)
                assert response.status_code == 409
                assert "Stop the output run" in response.json()["detail"]
            assert client.get("/api/trash", headers=headers).json() == []
        finally:
            release.set()
            thread.join(10)
        assert built(events[0])["versionNumber"] == 2


def test_saving_a_turn_preserves_the_work_of_a_trashed_output(tmp_path):
    with app_for(tmp_path, FakeProvider(report_reply())) as client:
        base, _ = library(client)
        headers = workspace(client)
        conversation = "field-report"
        first = built(build(client, base, body(conversationId=conversation), headers))
        revised = built(build(client, base, body(supersedesArtifactId=first["id"]), headers))
        other = built(build(client, base, body(conversationId=conversation), headers))
        history = conversation_path(base, conversation)

        entry = client.post(
            route(base, first["id"], "/trash"), headers=headers,
        ).raise_for_status().json()
        assert entry["versionCount"] == 2
        pending = client.get(history, headers=headers).raise_for_status().json()
        assert [work["key"] for work in pending["works"]] == [other["lineageId"]]

        updated = built(build(client, base, body(supersedesArtifactId=other["id"]), headers))
        active = client.get(history, headers=headers).raise_for_status().json()
        assert [(work["key"], work["versionId"]) for work in active["works"]] == [
            (other["lineageId"], updated["id"]),
        ]
        assert active["active"] == other["lineageId"]
        assert active["messages"][-1]["artifactId"] == updated["id"]

        restore(client, entry, headers)
        restored = client.get(history, headers=headers).raise_for_status().json()
        assert len(restored["works"]) == 2
        assert {work["key"]: work["versionId"] for work in restored["works"]} == {
            first["lineageId"]: revised["id"],
            other["lineageId"]: updated["id"],
        }
        for saved in (first, revised):
            assert client.get(route(base, saved["id"]), headers=headers).json() == saved


def test_conversation_trash_takes_only_owned_versions_and_restores_its_history(tmp_path):
    with app_for(tmp_path, FakeProvider(report_reply())) as client:
        base, _ = library(client)
        headers = workspace(client)
        made = [
            built(build(client, base, body(conversationId=owner), headers))
            for owner in ("main", "main", "other")
        ]
        path = conversation_path(base, "main")
        before = client.get(path, headers=headers).raise_for_status().json()
        # A historical message can link to an output without owning it.
        sessions = client.app.state.knowledge_service.sessions
        with sessions.begin() as session:
            session.add(ArtifactMessage(
                id="linked", conversation_id="main", role="assistant", kind="result",
                artifact_id=made[-1]["id"],
            ))
        entry = client.post(f"{path}/trash", headers=headers).raise_for_status().json()
        assert entry["outputType"] == "Output conversation" and entry["versionCount"] == 2
        assert client.get(path, headers=headers).status_code == 404
        recreated = client.post(conversation_path(base), headers=headers, json={"id": "main"})
        assert recreated.status_code == 404
        [remaining] = client.get(conversation_path(base), headers=headers).json()
        assert remaining["id"] == "other"
        for version in made[:2]:
            assert client.get(route(base, version["id"]), headers=headers).status_code == 404
        assert client.get(route(base, made[-1]["id"]), headers=headers).json() == made[-1]
        restore(client, entry, headers)
        after = client.get(path, headers=headers).raise_for_status().json()
        assert after["works"] == before["works"]
        assert any(m["id"] == "linked" for m in after["messages"])


def test_a_separate_output_batch_stays_in_trash_when_its_conversation_is_restored(tmp_path):
    with app_for(tmp_path, FakeProvider()) as client:
        base, _ = library(client)
        headers = workspace(client)
        made = saved_output(client, base, conversationId="main")
        client.get(conversation_path(base), headers=headers).raise_for_status()
        output = route(base, made["id"])
        conversation = conversation_path(base, "main")
        separate = client.post(f"{output}/trash", headers=headers).raise_for_status().json()
        together = client.post(f"{conversation}/trash", headers=headers).raise_for_status().json()
        assert together["versionCount"] == 0
        refused = client.post(f"/api/trash/output/{separate['id']}/restore", headers=headers)
        assert refused.status_code == 409

        restore(client, together, headers)
        assert client.get(output, headers=headers).status_code == 404
        assert client.get("/api/trash", headers=headers).json() == [separate]
        restore(client, separate, headers)
        assert client.get(output, headers=headers).raise_for_status().json() == made

        client.post(f"{output}/trash", headers=headers).raise_for_status()
        purged = client.post(f"{conversation}/trash", headers=headers).raise_for_status().json()
        client.delete(f"/api/trash/output/{purged['id']}", headers=headers).raise_for_status()
        assert client.get(conversation_path(base), headers=headers).json() == []
        assert client.get("/api/trash", headers=headers).json() == []


def test_delete_forever_releases_versions_checks_bindings_and_only_unused_files(tmp_path):
    fake = FakeProvider(agent_replies(
        "CD3D marks T cells [1].",
        {"action": "search_library", "query": "CD3D"}, {"action": "answer"},
    ))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        headers = workspace(client)
        asked = client.post(
            f"/api/knowledge-bases/{base}/sessions", headers=SIDECAR, json={},
        ).raise_for_status().json()["id"]
        turn = ask_turn(client, asked, {"content": "CD3D?"}, headers=SIDECAR)
        answer_id = turn["assistantMessage"]["id"]
        saved = client.post(
            f"/api/sessions/{asked}/messages/{answer_id}/proposal", headers=SIDECAR,
            json={"title": "Cell markers"},
        ).raise_for_status().json()
        scope = {"mode": "selection", "unitIds": [saved["knowledgeUnitId"]]}
        first = saved_output(client, base, conversationId="to-delete", scope=scope)
        second = edit(client, base, first, headers)
        third = edit(client, base, second, headers)
        other = saved_output(client, base)
        ids = [v["id"] for v in (first, second, third)]
        path = conversation_path(base, "to-delete")
        client.get(conversation_path(base), headers=headers).raise_for_status()
        sessions = client.app.state.knowledge_service.sessions
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir(exist_ok=True)
        with sessions.begin() as session:
            for name, version_ids in (
                ("shared.png", [*ids, other["id"]]), ("exclusive.png", ids),
            ):
                data = name.encode()
                (asset_dir / name).write_bytes(data)
                session.add(Asset(
                    id=name, content_hash=hashlib.sha256(data).hexdigest(), relative_path=name,
                    original_name=name, media_type="image/png", size_bytes=len(data),
                ))
                session.flush()
                session.add_all(
                    ArtifactAsset(artifact_id=id_, asset_id=name) for id_ in version_ids
                )
            session.add(ArtifactMessage(
                id="tracked-result", conversation_id="to-delete", role="assistant", kind="result",
                artifact_id=first["id"],
            ))
            assert session.scalar(select(ArtifactCheck.id).where(
                ArtifactCheck.artifact_id.in_(ids),
            ))
            assert session.scalar(select(ArtifactUnitBinding.id).where(
                ArtifactUnitBinding.artifact_id.in_(ids),
            ))
        entry = client.post(f"{path}/trash", headers=headers).raise_for_status().json()
        client.delete(f"/api/trash/output/{entry['id']}", headers=headers).raise_for_status()
        with sessions() as session:
            assert session.scalars(select(Artifact.id).where(Artifact.id.in_(ids))).all() == []
            for table in (ArtifactCheck, ArtifactUnitBinding, ArtifactAsset, ArtifactMessage):
                assert session.scalar(select(table).where(table.artifact_id.in_(ids))) is None
            assert session.get(Asset, "shared.png") and session.get(Asset, "exclusive.png") is None
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert client.get(path, headers=headers).status_code == 404
        assert client.get("/api/trash", headers=headers).json() == []
        assert (asset_dir / "shared.png").read_bytes() == b"shared.png"
        assert not (asset_dir / "exclusive.png").exists()
