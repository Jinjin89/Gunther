"""Explicit Studio builds and refinements through the artifact conversation API."""

import json
import threading

import pytest
from fake_models import FakeProvider, is_orchestrating, is_sentence_checking, output_replies
from sqlalchemy import select
from test_artifact_agent import conversations, done, kept, script, turn, versions_of
from test_outputs import (
    OUTLINE,
    REPORT,
    activity_of,
    app_for,
    body,
    build,
    built,
    in_background,
    library,
    route,
    wait_for,
    workspace,
)
from test_search_and_build import saved_web_snapshot, web_hit
from test_studio_pipeline import DECK, PASSAGES, request_text, router

from evals.sse import read_events
from gunther import key_results, usage
from gunther.answer_runs import Stopped
from gunther.artifact_manifest import artifact_manifest_hash
from gunther.artifact_workspace import approach_state, dump_workspace, evidence_from_citation
from gunther.evidence import KeyResultMatch, Tool
from gunther.models import Artifact, ArtifactConversation, ContentBlock, SourceIndexHead
from gunther.schemas import BuildOutputInput, ConversationCitationOut


def create(client, base_id, identifier="studio"):
    return (
        client.post(
            conversations(base_id),
            headers=workspace(client),
            json={"id": identifier},
        )
        .raise_for_status()
        .json()
    )


@pytest.mark.parametrize("stop_first", [False, True], ids=["both finish", "stop one"])
def test_conversations_in_one_library_build_and_follow_independently(tmp_path, stop_first):
    ids = ("report_a", "report_b")
    release = {cid: threading.Event() for cid in ids}
    entered = {cid: threading.Event() for cid in ids}
    checks = dict.fromkeys(ids, 0)
    guard = threading.Lock()

    def pause(request):
        cid = usage.current()["ref"]
        if cid not in release or not is_sentence_checking(request):
            return
        with guard:
            checks[cid] += 1
            second = checks[cid] == 2
        if second:
            assert threading.current_thread().name.startswith("studio")
            entered[cid].set()
            assert release[cid].wait(15)

    fake = FakeProvider(output_replies(OUTLINE, REPORT), rule=pause)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        other_base, _ = library(client)
        headers = workspace(client)
        for cid in ids:
            create(client, base, cid)
        runs = client.app.state.answer_runs
        keys = {cid: f"output-conv:{cid}" for cid in ids}

        def build_report(cid):
            return turn(client, base, cid, f"Build {cid}", "build", build={"type": "report"})

        def follow_second():
            path = conversations(base, "/report_b/stream")
            with client.stream("GET", path, headers=headers) as response:
                response.raise_for_status()
                return read_events(response)

        first, first_events = in_background(lambda: build_report("report_a"))
        second, second_events = in_background(lambda: build_report("report_b"))
        try:
            assert all(entered[cid].wait(10) for cid in ids)
            activity = activity_of(client)
            assert set(activity) == set(keys.values())
            for cid, key in keys.items():
                item = activity[key]
                assert item["state"] == "running"
                assert item["detail"] == (
                    "Checking every claim against its source · 1 of 2 sections checked"
                )
                assert item["progress"] is None
                assert item["open"]["conversationId"] == cid

            assert client.get(
                conversations(other_base, "/report_b/stream"), headers=headers,
            ).status_code == 204
            assert client.post(
                conversations(other_base, "/report_b/stop"), headers=headers,
            ).status_code == 204
            assert not runs.get(keys["report_b"]).stop_requested.is_set()

            follower, followed = in_background(follow_second)
            wait_for(lambda: len(runs.get(keys["report_b"])._watchers) == 2)
            if stop_first:
                assert client.post(
                    conversations(base, "/report_a/stop"), headers=headers,
                ).status_code == 204
                release["report_a"].set()
                first.join(10)
                assert first_events[0][-1] == ("stopped", {"type": "stopped"})
                assert runs.get(keys["report_b"]) is not None
                assert not runs.get(keys["report_b"]).stop_requested.is_set()
                assert keys["report_a"] not in activity_of(client)
        finally:
            for gate in release.values():
                gate.set()
            first.join(10)
            second.join(10)

        follower.join(10)
        saved = done(second_events[0])["artifact"]
        name, resumed = followed[0][0]
        assert (name, resumed["question"]) == ("resumed", "Build report_b")
        assert done(followed[0]) == done(second_events[0])
        assert [data["conversationId"] for name, data in followed[0] if name == "turn"] == [
            "report_b",
        ]
        assert activity_of(client)[keys["report_b"]]["open"] == {
            "view": "library", "libraryId": base, "tab": "outputs", "conversationId": "report_b",
            "typeId": "report", "lineageId": saved["lineageId"], "artifactId": saved["id"],
        }
        assert len(versions_of(client)) == (1 if stop_first else 2)
        if not stop_first:
            assert done(first_events[0])["artifact"]["lineageId"] != saved["lineageId"]


@pytest.mark.parametrize(
    ("type_id", "shape", "label"),
    [("briefing", "report", "Briefing doc"), ("slides", "slides", "Slides")],
)
def test_explicit_build_uses_the_selected_type_scope_and_options(
    tmp_path, monkeypatch, type_id, shape, label
):
    fake = FakeProvider(
        lambda request: router(request, DECK.replace("[2]", "[1]")),
        # The one-section report runs its model calls serially.
        write_lock_path=tmp_path / "gunther.sqlite" if shape == "report" else None,
    )
    searches = []

    def search(query):
        searches.append(query)
        return []

    with app_for(tmp_path, fake) as client:
        base, sources = library(
            client, notes={"Trial": " ".join(PASSAGES), "Excluded": "Other data"}
        )
        service = client.app.state.knowledge_service
        tool = Tool("web", "Recorded search", search, "the web")
        monkeypatch.setattr(
            service, "_web_tool", lambda enabled: (tool if enabled else None, None)
        )
        cid = create(client, base)["id"]
        scope = {"mode": "selection", "sourceIds": [sources["Trial"]]}
        options = {
            "type": type_id,
            "focus": "Summarize the cooling trial",
            "scope": scope,
            "web": True,
            "language": "French",
            "length": "short",
            "title": "Trial digest",
        }
        if shape == "slides":
            options |= {"use": "read", "theme": "ink"}
        events = turn(
            client, base, cid, "", "build", build=options, clientRequestId="tile_build_01"
        )
        result = done(events)
        saved = result["artifact"]
        assert (saved["versionNumber"], saved["style"], saved["conversationId"]) == (
            1,
            type_id,
            cid,
        )
        assert saved["provenance"]["language"] == "French"
        assert saved["inputs"]["sources"] == 1
        assert saved["scope"]["sourceIds"] == scope["sourceIds"]
        if shape == "slides":
            assert saved["provenance"]["use"] == "read" and "theme: ink" in saved["content"]
        assert not {name for name, _ in events} & {"question", "decision"}
        assert not any(is_orchestrating(request) for request in fake.requests)
        [summary] = (
            client.get(conversations(base), headers=workspace(client)).raise_for_status().json()
        )
        detail = client.get(conversations(base, f"/{cid}"), headers=workspace(client)).json()
        assert detail["scope"] == saved["scope"]
        assert (
            summary["title"],
            summary["outputType"],
            summary["shape"],
            summary["lineageId"],
        ) == (
            "Trial digest",
            label,
            shape,
            saved["lineageId"],
        )
        assert summary["lastTurn"] == detail["lastTurn"]
        assert summary["lastTurn"]["kind"] == "build"
        assert summary["lastTurn"]["build"] == detail["messages"][0]["payload"]["build"]
        assert summary["lastTurn"]["error"] is None
        calls = len(fake.requests)
        assert (
            done(
                turn(
                    client,
                    base,
                    cid,
                    "",
                    "build",
                    build=options,
                    clientRequestId="tile_build_01",
                )
            )
            == result
        )
        conflict = turn(
            client,
            base,
            cid,
            "",
            "build",
            build=options | {"language": "Italian"},
            clientRequestId="tile_build_01",
        )
        assert conflict[-1][1]["status"] == 409 and len(fake.requests) == calls
    assert searches == ["Summarize the cooling trial"]
    if shape == "report":
        assert fake.requests and not fake.write_lock_failures, fake.write_lock_failures


def test_studio_search_provenance_is_sealed_enriched_and_kept_out_of_later_prompts(
    tmp_path, monkeypatch,
):
    def reply(request):
        if "Write the report" in request_text(request):
            return "# Findings\n\n## Results\nThe web result [3]. The trial record [1]."
        return router(request)

    fake = FakeProvider(reply)
    web = [web_hit(f"Build web result {n}", f"https://example.org/{n}") for n in range(9)]
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base, sources = library(client, notes={
            "Trial": "The trial records a measured temperature.",
            "Uncited reading": "The second reading gives some background.",
            "Captured": "An imported reading.",
        })
        service = client.app.state.knowledge_service
        captured = sources["Captured"]
        trial = sources["Trial"]
        with service.sessions.begin() as session:
            saved_web_snapshot(session, captured, web[0].url, web[1].url)
            revision = session.get(SourceIndexHead, trial).revision_id
            block = session.scalar(select(ContentBlock).where(ContentBlock.revision_id == revision))
            evidence = evidence_from_citation(ConversationCitationOut(
                id="checked_trial", source_id=trial, source_title="Trial", quote=block.content,
                locator=block.locator, status="provisional", confidence=1,
                source_revision_id=revision, block_id=block.id,
            ))
        match = KeyResultMatch("checked", "The trial records temperature.", ((evidence,),))
        monkeypatch.setattr(key_results, "retrieve", lambda *args, **kwargs: [match])
        tool = Tool("search_web", "Recorded hits", lambda _: web, "web", grade=True)
        monkeypatch.setattr(service, "_web_tool", lambda enabled: (tool if enabled else None, None))
        library_queries = []

        def search_library(query):
            library_queries.append(query)
            return []

        library_tool = Tool("search_library", "Recorded passages", search_library, "your library")
        monkeypatch.setattr(service, "_output_search_tool", lambda *_: library_tool)
        cid = create(client, base)["id"]
        saved = done(turn(client, base, cid, "", "build", build={
            "type": "report", "web": True, "focus": "Find the trial record",
            "scope": {"mode": "selection", "sourceIds": [trial, sources["Uncited reading"]]},
        }))["artifact"]
        path, headers = route(base, saved["id"]), workspace(client)

        def detail():
            return client.get(path, headers=headers).raise_for_status().json()

        assert [(citation["kind"], citation.get("librarySourceId"))
                for citation in saved["citations"]] == [("web", captured), ("library", None)]
        assert saved["provenance"]["keyResultsUsed"] == [
            {"id": "checked", "text": match.sentence, "refs": [2]},
        ]
        searched = saved["provenance"]["searched"]
        web_searched = [hit for hit in searched if hit["kind"] == "web"]
        assert [hit["url"] for hit in web_searched] == [hit.url for hit in web[1:]]
        assert web_searched[0]["librarySourceId"] == captured
        assert all(hit["kept"] for hit in web_searched[:-1]) and not web_searched[-1]["kept"]
        assert library_queries == []
        assert [(hit["sourceId"], hit["query"], hit["kept"])
                for hit in searched if hit["kind"] == "library"] == [
            (sources["Uncited reading"], "", True),
        ]

        with service.sessions() as session:
            row = session.get(Artifact, saved["id"])
            sealed = row.provenance_json
            assert "library_source_id" not in sealed + row.citations_json
            assert row.manifest_hash == artifact_manifest_hash(row.__getattribute__)
        assert detail()["provenance"]["searched"] == searched

        client.post(f"/api/sources/{captured}/trash", headers=headers).raise_for_status()
        trashed = detail()
        assert "librarySourceId" not in trashed["citations"][0]
        assert "librarySourceId" not in trashed["provenance"]["searched"][0]
        assert trashed["manifestHash"] == saved["manifestHash"]
        client.post(f"/api/trash/source/{captured}/restore", headers=headers).raise_for_status()
        assert detail()["provenance"]["searched"] == searched

        sent = len(fake.requests)
        fake.replies[:] = [script([{"actions": [{"do": "reply", "text": "Saved."}]}])]
        assert done(turn(client, base, cid, "Did it finish?"))["outcome"] == "replied"
        prompts = "\n".join(request_text(request) for request in fake.requests[sent:])
        assert web[0].title in prompts
        assert all(hit.title not in prompts for hit in web[1:])

        with service.sessions.begin() as session:
            row = session.get(Artifact, saved["id"])
            assert row.provenance_json == sealed
            tampered = json.loads(sealed)
            tampered["searched"][0]["title"] = "Changed"
            row.provenance_json = json.dumps(tampered)
        assert client.get(path, headers=headers).status_code == 409

        # Earlier version-2 artifacts sealed the payload before these two fields existed.
        with service.sessions.begin() as session:
            row = session.get(Artifact, saved["id"])
            old = json.loads(sealed)
            del old["searched"], old["key_results_used"]
            row.provenance_json = json.dumps(old)
            row.manifest_hash = artifact_manifest_hash(row.__getattribute__)
        legacy = detail()["provenance"]
        assert legacy["searched"] == legacy["keyResultsUsed"] == []


def test_studio_records_current_rejection_for_a_previously_held_hit(tmp_path, monkeypatch):
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    hit = web_hit("Earlier outside result", "https://example.org/held")
    queries = []

    def search(query):
        queries.append(query)
        return [hit]

    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base, _ = library(client)
        cid = create(client, base)["id"]
        first = done(turn(client, base, cid, "", "build", build={"type": "report"}))
        assert first["outcome"] == "saved"
        service = client.app.state.knowledge_service
        web = Tool("search_web", "Recorded hits", search, "web", grade=True)
        monkeypatch.setattr(service, "_web_tool", lambda enabled: (web if enabled else None, None))

        fake.replies[:] = [script([
            {"actions": [
                {"do": "search_web", "query": "first lookup"},
                {"do": "reply", "text": "Finished the lookup."},
            ]},
        ], output_replies(OUTLINE, REPORT, relevant=[1]))]
        lookup = done(turn(client, base, cid, "Look up more background", web=True))
        assert lookup["outcome"] == "replied"
        state, _ = kept(client, cid)
        assert hit.url in [citation["url"] for citation in state.pool]

        fake.replies[:] = [script([
            {"actions": [
                {"do": "search_web", "query": "refinement sources"},
                {"do": "plan", "change": "title", "heading": "Reviewed findings"},
            ]},
            {"actions": [{"do": "save", "text": "Renamed the report."}]},
        ], output_replies(OUTLINE, REPORT, relevant=[]))]
        revised = done(turn(client, base, cid, "Recheck the background and rename it", web=True))
        assert revised["outcome"] == "saved"
        assert queries == ["first lookup", "refinement sources"]
        [result] = revised["artifact"]["provenance"]["searched"]
        assert (result["url"], result["query"], result["kept"]) == (
            hit.url, "refinement sources", False,
        )


def test_retry_reuses_a_failed_builds_memo_and_story(tmp_path):
    failing = True

    def reply(request):
        if failing and "Write the report" in request_text(request):
            return "# Incomplete draft"
        return router(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = create(client, base)["id"]
        events = turn(
            client,
            base,
            cid,
            "Build a report",
            "build",
            build={"type": "report", "focus": "Trial results"},
        )
        assert events[-1][0] == "error" and events[-1][1]["status"] == 502
        assert versions_of(client) == []
        [draft] = kept(client, cid)[0].works
        assert draft.key.startswith("draft_")
        assert draft.approach["studio_progress"]["storyline"]
        [summary] = client.get(conversations(base), headers=workspace(client)).json()
        retry = summary["lastTurn"]
        assert retry["error"] == events[-1][1]["detail"] and summary["lineageId"] is None
        assert summary["outputType"] == "Report" and retry["createdAt"]

        failing = False
        fake.requests.clear()
        result = done(
            turn(
                client,
                base,
                cid,
                "",
                "build",
                build=retry["build"],
                clientRequestId="tile_retry_01",
            )
        )
        [published] = kept(client, cid)[0].works
        assert published.version_id == result["artifact"]["id"]
        assert len(versions_of(client)) == 1 and published.build_key is None
        assert not any(
            marker in request_text(request)
            for marker in ("lead analyst", "planning a written report")
            for request in fake.requests
        )


@pytest.mark.parametrize("stop", [False, True], ids=["failed", "stopped"])
def test_interrupted_targeted_build_only_keeps_draft_checkpoints(tmp_path, monkeypatch, stop):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, sources = library(client, notes={"Initial": PASSAGES[0], "Follow-up": PASSAGES[1]})
        headers = workspace(client)
        cid = create(client, base)["id"]
        path = conversations(base, f"/{cid}")
        first = done(turn(client, base, cid, "", "build", build={
            "type": "report", "focus": "Initial findings",
            "scope": {"mode": "selection", "sourceIds": [sources["Initial"]]},
        }))["artifact"]
        old, old_brief = kept(client, cid)
        before = client.get(path, headers=headers).raise_for_status().json()
        options = {
            "type": "report", "focus": "Follow-up findings", "audience": "student",
            "scope": {"mode": "selection", "sourceIds": [sources["Follow-up"]]},
        }

        service = client.app.state.knowledge_service
        store = service._store_turn

        def interrupt_publish(*args, **kwargs):
            message = store(*args, **kwargs)
            if kwargs.get("existing_session") is not None:
                if stop:
                    raise Stopped
                raise ValueError("Could not publish the rebuilt report")
            return message

        with monkeypatch.context() as patch:
            patch.setattr(service, "_store_turn", interrupt_publish)
            ends = turn(client, base, cid, "", "build", build=options,
                        targetLineageId=first["lineageId"])
        event, error = ends[-1]
        if stop:
            assert event == "stopped"
        else:
            assert (event, error["detail"]) == ("error", "Could not publish the rebuilt report")

        saved, brief = kept(client, cid)
        [draft] = [work for work in saved.works if work.key.startswith("draft_")]
        saved.works.remove(draft)
        assert saved == old and brief == old_brief
        assert approach_state(saved.works[0], brief).state == "ok"
        assert draft.approach["studio_progress"]["storyline"]
        assert not draft.outline and not draft.parts
        failed = client.get(path, headers=headers).raise_for_status().json()
        assert failed["brief"] == before["brief"] and failed["scope"] == before["scope"]
        assert [version.id for version in versions_of(client)] == [first["id"]]
        assert failed["messages"][-1]["kind"] == ("stopped" if stop else "failed")

        fake.requests.clear()
        version = done(turn(client, base, cid, "", "build", build=failed["lastTurn"]["build"],
                            targetLineageId=first["lineageId"]))["artifact"]
        assert version["versionNumber"] == 2 and version["supersedesArtifactId"] == first["id"]
        assert not any(
            marker in request_text(request)
            for marker in ("lead analyst", "planning a written report")
            for request in fake.requests
        )
        final = client.get(path, headers=headers).raise_for_status().json()
        assert final["brief"]["goal"]["text"] == options["focus"]
        assert final["scope"] == version["scope"]
        assert version["scope"]["sourceIds"] == options["scope"]["sourceIds"]
        assert [work["key"] for work in final["works"]] == [first["lineageId"]]


def test_target_lineage_imports_and_refines_the_named_output(tmp_path):
    baseline = output_replies(
        OUTLINE, REPORT, rewrite=lambda *_: "## T cells\n\nCD3D identifies T cells [2]."
    )
    fake = FakeProvider(baseline)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = create(client, base)["id"]
        first = done(turn(client, base, cid, "", "build", build={"type": "report"}))["artifact"]
        fake.replies[:] = [lambda request: router(request, DECK.replace("[2]", "[1]"))]
        second = done(turn(client, base, cid, "", "build", build={"type": "slides"}))[
            "artifact"
        ]
        state, _ = kept(client, cid)
        assert state.active == second["lineageId"] and len(state.works) == 2
        # A saved lineage can outlive the workspace record that points to it.
        state.works = [work for work in state.works if work.key == second["lineageId"]]
        with client.app.state.knowledge_service.sessions.begin() as session:
            session.get(ArtifactConversation, cid).state_json = dump_workspace(state)

        fake.replies[:] = [
            script(
                [
                    {
                        "actions": [
                            {
                                "do": "write",
                                "parts": [1],
                                "instruction": "Make the first section shorter.",
                            }
                        ]
                    },
                    {"actions": [{"do": "save", "text": "Revised the report."}]},
                ],
                baseline,
            )
        ]
        options = {"targetLineageId": first["lineageId"], "clientRequestId": "target_report_01"}
        changed = done(turn(client, base, cid, "Shorten the first section", **options))[
            "artifact"
        ]
        assert changed["supersedesArtifactId"] == first["id"] and changed["kind"] == "report"
        assert "CD3D identifies T cells" in changed["content"]
        assert client.get(route(base, second["id"]), headers=workspace(client)).json() == second
        assert kept(client, cid)[0].active == first["lineageId"]
        conflict = turn(
            client,
            base,
            cid,
            "Shorten the first section",
            **(
                options
                | {
                    "targetLineageId": second["lineageId"],
                }
            ),
        )
        assert conflict[-1][1]["status"] == 409
        other = create(client, base, "other")["id"]
        calls = len(fake.requests)
        refused = turn(client, base, other, "Edit the deck", targetLineageId=second["lineageId"])
        assert refused[-1][1]["status"] == 404 and len(fake.requests) == calls


def test_refinement_uses_each_outputs_scope_and_allows_an_explicit_update(tmp_path, monkeypatch):
    fake = FakeProvider(lambda request: router(request, DECK.replace("[2]", "[1]")))
    with app_for(tmp_path, fake) as client:
        passage = " ".join(PASSAGES)
        base, sources = library(client, notes={"First": passage, "Second": passage})
        cid = create(client, base)["id"]
        headers = workspace(client)
        first_scope = {"mode": "selection", "sourceIds": [sources["First"]]}
        second_scope = {"mode": "selection", "sourceIds": [sources["Second"]]}
        first = done(turn(
            client, base, cid, "", "build", build={"type": "briefing", "scope": first_scope},
        ))["artifact"]
        second = done(turn(
            client, base, cid, "", "build", build={"type": "slides", "scope": second_scope},
        ))["artifact"]
        service = client.app.state.knowledge_service
        material = service._output_material
        used = []

        def record(session, base_id, scope, **options):
            used.append(scope.source_ids)
            return material(session, base_id, scope, **options)

        monkeypatch.setattr(service, "_output_material", record)

        def refine(artifact):
            fake.replies[:] = [script([{"actions": [{"do": "reply", "text": "Reviewed."}]}])]
            return done(turn(
                client, base, cid, "Review the evidence", targetLineageId=artifact["lineageId"],
            ))

        refine(first)
        refine(second)
        assert used == [first_scope["sourceIds"], second_scope["sourceIds"]]
        path = conversations(base, f"/{cid}")
        before = client.get(path, headers=headers).raise_for_status().json()
        requested = {"mode": "selection", "sourceIds": list(sources.values())}
        update = {"targetLineageId": first["lineageId"], "scope": requested}
        other = create(client, base, "other")["id"]
        refused = client.patch(conversations(base, f"/{other}"), headers=headers, json=update)
        assert refused.status_code == 404
        changed = client.patch(path, headers=headers, json=update).raise_for_status().json()
        assert changed["scope"] == before["scope"]
        assert changed["refinementScopes"] == {
            first["lineageId"]: requested | {"unitIds": [], "sessionIds": []},
        }
        refine(first)
        refine(second)
        assert used[-2:] == [requested["sourceIds"], second_scope["sourceIds"]]
        original = client.get(route(base, first["id"]), headers=headers).raise_for_status().json()
        assert original["scope"] == first["scope"]


def test_build_rejects_an_empty_selection_before_calling_models(tmp_path):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = create(client, base)["id"]
        events = turn(
            client,
            base,
            cid,
            "",
            "build",
            build={"type": "report", "scope": {"mode": "selection"}},
        )
        assert events[-1][0] == "error" and events[-1][1]["status"] == 400
        assert versions_of(client) == [] and not fake.requests


def test_legacy_build_maps_style_and_replays_a_previous_fingerprint(tmp_path):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        request = body(kind="report", style="decision_brief", brief="Give the options")
        saved = built(build(client, base, request))
        assert saved["style"] == "briefing"
        assert kept(client, saved["conversationId"])[0].active == saved["lineageId"]
        service = client.app.state.knowledge_service
        calls = len(fake.requests)
        with service.sessions.begin() as session:
            row = session.get(Artifact, saved["id"])
            row.request_fingerprint = service._output_fingerprint(
                row.workspace_id,
                base,
                "build",
                service._output_request_fields(BuildOutputInput(**request)),
            )
        assert build(client, base, request) == [("done", saved)]
        assert len(fake.requests) == calls


def test_legacy_rebuild_inherits_a_studio_deck_theme(tmp_path):
    fake = FakeProvider(lambda request: router(request, DECK.replace("[2]", "[1]")))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Trial": " ".join(PASSAGES)})
        first = built(build(client, base, body(kind="slides", theme="ink", use="read")))
        rebuilt = built(build(client, base, body(kind="slides", supersedesArtifactId=first["id"])))
        assert rebuilt["supersedesArtifactId"] == first["id"]
        assert "theme: ink" in first["content"] and "theme: ink" in rebuilt["content"]


@pytest.mark.parametrize("missing", ["model", "material"])
def test_legacy_preflight_rejects_without_creating_a_conversation(tmp_path, missing):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={} if missing == "material" else None)
        if missing == "model":
            client.app.state.knowledge_service.models = None
        before = len(fake.requests)
        event, error = build(client, base, body())[-1]
        assert event == "error" and error["status"] == 400
        assert len(fake.requests) == before
        listed = client.get(conversations(base), headers=workspace(client))
        assert listed.raise_for_status().json() == []
