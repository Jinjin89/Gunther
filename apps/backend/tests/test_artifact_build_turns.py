"""Explicit Studio builds and refinements through the artifact conversation API."""

import json
import re
import threading
from dataclasses import asdict
from unittest.mock import patch
from uuid import uuid4

import pytest
from fake_models import FakeProvider, is_orchestrating, is_sentence_checking
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
    legacy_build,
    library,
    output_replies,
    output_turn,
    route,
    wait_for,
    workspace,
)
from test_plan_approval import approve_request, plan_request, plan_worker
from test_search_and_build import saved_web_snapshot, web_hit
from test_studio_pipeline import DECK, PASSAGES, request_text, router
from test_studio_plan_stages import dispatch

from evals.sse import read_events
from gunther import key_results, usage
from gunther.answer_runs import Stopped
from gunther.artifact_actions import Act, Decision
from gunther.artifact_agent import ArtifactAgent
from gunther.artifact_manifest import artifact_manifest_hash
from gunther.artifact_workspace import Line, Work, WorkSpec, dump_workspace, evidence_from_citation
from gunther.evidence import KeyResultMatch, Tool
from gunther.library_lookup import MAX_SEARCHED
from gunther.models import (
    Artifact,
    ArtifactConversation,
    ArtifactMessage,
    ContentBlock,
    SourceIndexHead,
)
from gunther.output_types import OUTPUT_TYPES
from gunther.outputs import OutputLimits, Pool, Spec
from gunther.schemas import BuildOutputInput, BuildTurnInput, ConversationCitationOut
from gunther.slide_render import digest
from gunther.studio.look import vision_model
from gunther.studio.pipeline import StudioAgents
from gunther.studio.science import pool_print


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


def studio_plan(client, base, cid, options, **request_fields):
    options = {**options}
    payload = plan_request(
        plan_id=f"draft_{uuid4().hex}", type_id=options.pop("type"), **options,
    ) | request_fields
    decision = Decision(actions=[Act(do="plan", change="whole")])
    with patch.object(ArtifactAgent, "_decide", return_value=decision):
        return output_turn(client, base, cid, payload)


def studio_build(client, base, cid, options):
    plan = done(studio_plan(client, base, cid, options))["plan"]
    return done(output_turn(client, base, cid, approve_request(plan)))["artifact"]


def deck_worker(request):
    if is_orchestrating(request):
        return plan_worker(request)
    prompt = request_text(request)
    if "presentation strategist" in prompt:
        return dispatch("slides")(request)
    if "Write the report" in prompt:
        return plan_worker(request)
    match = re.search(r"^\[(\d+)\] \(", prompt, re.M)
    reference = match[1] if match else "1"
    return router(request, re.sub(r"\[[12]\]", f"[{reference}]", DECK))


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
        plans = {
            cid: done(studio_plan(client, base, cid, {"type": "report"}))["plan"] for cid in ids
        }
        runs = client.app.state.answer_runs
        keys = {cid: f"output-conv:{cid}" for cid in ids}

        def build_report(cid):
            return output_turn(client, base, cid, approve_request(plans[cid]))

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
                assert item["detail"].startswith("Checking")
                assert item["progress"] is None
                assert item["open"]["conversationId"] == cid
                assert item["open"]["planId"] == plans[cid]["planId"]

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
        assert (name, resumed["question"]) == ("resumed", "Generating Report")
        assert done(followed[0]) == done(second_events[0])
        assert [data["conversationId"] for name, data in followed[0] if name == "turn"] == [
            "report_b",
        ]
        assert activity_of(client)[keys["report_b"]]["open"] == {
            "view": "library", "libraryId": base, "tab": "outputs", "conversationId": "report_b",
            "typeId": "report", "lineageId": saved["lineageId"], "artifactId": saved["id"],
            "planId": plans["report_b"]["planId"],
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
        deck_worker,
        # The report runs its model calls serially.
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
        plan = done(studio_plan(client, base, cid, options))["plan"]
        approve = approve_request(plan, "tile_build_01")
        events = output_turn(client, base, cid, approve)
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
        assert summary["lastTurn"]["kind"] == "approve"
        assert detail["messages"][0]["payload"]["setup"]["type"] == type_id
        assert summary["lastTurn"]["error"] is None
        calls = len(fake.requests)
        assert done(output_turn(client, base, cid, approve)) == result
        conflict = output_turn(client, base, cid, approve | {"planId": "draft_elsewhere"})
        assert conflict[-1][1]["status"] == 409 and len(fake.requests) == calls
    assert searches == ["Summarize the cooling trial"]
    if shape == "report":
        assert fake.requests and not fake.write_lock_failures, fake.write_lock_failures


def test_studio_search_provenance_is_sealed_enriched_and_kept_out_of_later_prompts(
    tmp_path, monkeypatch,
):
    def reply(request):
        task = request_text(request)
        if "planning a written report" in task:
            return json.dumps({
                "title": "Findings", "summary_heading": "Summary",
                "sections": [{"heading": "Results"}],
            })
        if "Write the report" in task:
            return (
                "# Findings\n\n## Summary\nThe web result [3]. The trial record [1]."
                "\n\n## Results\nThe trial record gives the measured temperature [1]."
            )
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
        saved = studio_build(client, base, cid, {
            "type": "report", "web": True, "focus": "Find the trial record",
            "scope": {"mode": "selection", "sourceIds": [trial, sources["Uncited reading"]]},
        })
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
        first = studio_build(client, base, cid, {"type": "report"})
        assert first["versionNumber"] == 1
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


def test_studio_research_notes_fill_the_limit_after_removing_citations(tmp_path):
    with app_for(tmp_path, FakeProvider(plan_worker), ai_summaries="off") as client:
        base, sources = library(client, notes={
            f"Record {n}": f"The trial record gives observation {n}." for n in range(23)
        })
        cid = create(client, base)["id"]
        plan = done(studio_plan(client, base, cid, {"type": "report"}))["plan"]
        state, _ = kept(client, cid)
        [work] = state.works
        candidates = work.studio_progress["manifest"]["searched"]
        assert len(candidates) == len(sources) == 23
        passages = {item["ref"]: item for item in state.pool}
        assert all(set(candidate) == {"kind", "ref", "query", "kept"} for candidate in candidates)
        candidates_in_order = [passages[candidate["ref"]]["source_id"] for candidate in candidates]
        assert set(candidates_in_order) == set(sources.values())

        saved = done(output_turn(client, base, cid, approve_request(plan)))["artifact"]
        [citation] = saved["citations"]
        searched = saved["provenance"]["searched"]
        assert [hit["sourceId"] for hit in searched] == [
            source for source in candidates_in_order if source != citation["sourceId"]
        ][:MAX_SEARCHED]
        assert len(searched) == MAX_SEARCHED == 20


def test_studio_research_notes_keep_other_blocks_with_the_cited_locator(tmp_path):
    with app_for(tmp_path, FakeProvider(plan_worker), ai_summaries="off") as client:
        base, sources = library(client, notes={"Trial": "The trial establishes the outcome."})
        service = client.app.state.knowledge_service
        second_text = "A separate passage describes another observation."
        with service.sessions.begin() as session:
            revision = session.get(SourceIndexHead, sources["Trial"]).revision_id
            first = session.scalar(select(ContentBlock).where(ContentBlock.revision_id == revision))
            first_id, second_id = first.id, "block_other_observation"
            session.add(ContentBlock(
                id=second_id, revision_id=revision, ordinal=first.ordinal + 1, kind=first.kind,
                # This long block is read as a separate passage even under the same locator.
                content=" ".join([second_text] * 32), locator=first.locator,
                heading_path_json=first.heading_path_json,
            ))

        cid = create(client, base)["id"]
        plan = done(studio_plan(client, base, cid, {"type": "report"}))["plan"]
        state, _ = kept(client, cid)
        assert {item["block_id"] for item in state.pool} == {first_id, second_id}
        saved = done(output_turn(client, base, cid, approve_request(plan)))["artifact"]
        [citation] = saved["citations"]
        assert citation["blockId"] == first_id
        [note] = saved["provenance"]["searched"]
        assert (note["sourceId"], note["locator"]) == (citation["sourceId"], citation["locator"])
        assert note["snippet"].startswith(second_text)


def test_retry_reuses_a_failed_generations_memo_and_story(tmp_path):
    failing = True

    def reply(request):
        if failing and "Write the report" in request_text(request):
            return "# Incomplete draft"
        return plan_worker(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = "retry_report"
        plan = done(studio_plan(client, base, cid, {"type": "report", "focus": "Trial results"}))[
            "plan"
        ]
        approve = approve_request(plan)
        events = output_turn(client, base, cid, approve)
        assert events[-1][0] == "error" and events[-1][1]["status"] == 502
        assert versions_of(client) == []
        [draft] = kept(client, cid)[0].works
        assert draft.studio_progress["storyline"]
        [summary] = client.get(conversations(base), headers=workspace(client)).json()
        assert summary["lastTurn"]["error"] == events[-1][1]["detail"]
        assert summary["studioWorks"][0]["status"] == "failed"
        assert summary["lineageId"] is None
        assert output_turn(client, base, cid, approve)[-1][1]["status"] == 409

        failing = False
        fake.requests.clear()
        result = done(output_turn(client, base, cid, approve_request(plan)))
        [published] = kept(client, cid)[0].works
        assert published.version_id == result["artifact"]["id"]
        assert len(versions_of(client)) == 1
        assert not any(
            marker in request_text(request)
            for marker in ("lead analyst", "planning a written report")
            for request in fake.requests
        )


@pytest.mark.parametrize("stop", [False, True], ids=["failed", "stopped"])
def test_interrupted_targeted_build_rolls_back_child_and_turn(tmp_path, monkeypatch, stop):
    fake = FakeProvider(plan_worker)
    with app_for(tmp_path, fake) as client:
        base, sources = library(client, notes={"Initial": PASSAGES[0], "Follow-up": PASSAGES[1]})
        headers = workspace(client)
        cid = "rebuild_report"
        path = conversations(base, f"/{cid}")
        first = studio_build(client, base, cid, {
            "type": "report", "focus": "Initial findings",
            "scope": {"mode": "selection", "sourceIds": [sources["Initial"]]},
        })
        old, old_brief = kept(client, cid)
        before = client.get(path, headers=headers).raise_for_status().json()
        options = {
            "type": "report", "focus": "Follow-up findings", "audience": "student",
            "scope": {"mode": "selection", "sourceIds": [sources["Follow-up"]]},
        }

        service = client.app.state.knowledge_service
        store = service._commit_turn_reply

        def interrupt_publish(*args, **kwargs):
            result = store(*args, **kwargs)
            if kwargs.get("artifact") is not None:
                if stop:
                    raise Stopped
                raise ValueError("Could not publish the rebuilt report")
            return result

        with monkeypatch.context() as patcher:
            patcher.setattr(service, "_commit_turn_reply", interrupt_publish)
            ends = turn(client, base, cid, "", "build", build=options,
                        targetLineageId=first["lineageId"])
        event, error = ends[-1]
        if stop:
            assert event == "stopped"
        else:
            assert (event, error["detail"]) == ("error", "Could not publish the rebuilt report")

        after, brief = kept(client, cid)
        assert after == old and brief == old_brief
        failed = client.get(path, headers=headers).raise_for_status().json()
        assert failed["brief"] == before["brief"] and failed["scope"] == before["scope"]
        assert [version.id for version in versions_of(client)] == [first["id"]]
        assert failed["messages"][-1]["kind"] == ("stopped" if stop else "failed")

        version = done(turn(client, base, cid, "", "build", build=options,
                            targetLineageId=first["lineageId"]))["artifact"]
        assert version["versionNumber"] == 2 and version["supersedesArtifactId"] == first["id"]
        final = client.get(path, headers=headers).raise_for_status().json()
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
        first = studio_build(client, base, cid, {"type": "report"})
        fake.replies[:] = [deck_worker]
        second = studio_build(client, base, cid, {"type": "slides"})
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
    fake = FakeProvider(deck_worker)
    with app_for(tmp_path, fake) as client:
        passage = " ".join(PASSAGES)
        base, sources = library(client, notes={"First": passage, "Second": passage})
        cid = create(client, base)["id"]
        headers = workspace(client)
        first_scope = {"mode": "selection", "sourceIds": [sources["First"]]}
        second_scope = {"mode": "selection", "sourceIds": [sources["Second"]]}
        first = studio_build(client, base, cid, {"type": "briefing", "scope": first_scope})
        second = studio_build(client, base, cid, {"type": "slides", "scope": second_scope})
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


def test_plan_rejects_an_empty_selection_before_creating_a_conversation(tmp_path):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        events = output_turn(client, base, "no_sources", plan_request(scope={"mode": "selection"}))
        assert events[-1][0] == "error" and events[-1][1]["status"] == 422
        assert versions_of(client) == [] and not fake.requests
        assert client.get(conversations(base), headers=workspace(client)).json() == []


def test_legacy_build_maps_style_and_replays_a_previous_fingerprint(tmp_path):
    fake = FakeProvider(plan_worker)
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
        assert legacy_build(client, base, request) == [("done", saved)]
        assert len(fake.requests) == calls


def test_legacy_rebuild_inherits_a_studio_deck_theme(tmp_path):
    fake = FakeProvider(deck_worker)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Trial": " ".join(PASSAGES)})
        first = built(build(client, base, body(kind="slides", theme="ink", use="read")))
        rebuilt = built(build(client, base, body(kind="slides", supersedesArtifactId=first["id"])))
        assert rebuilt["supersedesArtifactId"] == first["id"]
        assert "theme: ink" in first["content"] and "theme: ink" in rebuilt["content"]


def test_conversation_make_preserves_theme_from_the_exact_text_edit(tmp_path):
    fake = FakeProvider(deck_worker)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Trial": " ".join(PASSAGES)})
        first = studio_build(client, base, "deck_make", {"type": "slides", "theme": "paper"})
        content = first["content"].replace("theme: paper", "theme: ink", 1)
        assert content != first["content"]
        head = client.post(route(base, first["id"], "/edits"), headers=workspace(client), json={
            "clientRequestId": "change_saved_cover_theme", "content": content,
        }).raise_for_status().json()

        def changed_deck(request):
            result = deck_worker(request)
            if "presentation writer" in request_text(request):
                return result.replace("Plan with the adjusted figure", "Use the adjusted figure")
            return result

        fake.replies[:] = [script([
            {"actions": [{"do": "make"}, {"do": "save", "text": "Rebuilt the deck."}]},
        ], changed_deck)]
        result = done(turn(
            client, base, "deck_make", "Rebuild the deck", targetLineageId=first["lineageId"],
        ))
        assert result["outcome"] == "saved", result["reply"]
        rebuilt = result["artifact"]
        assert rebuilt["supersedesArtifactId"] == head["id"]
        assert "theme: ink" in rebuilt["content"]
        assert kept(client, "deck_make")[0].works[0].spec.theme == "ink"


def _checkpoint_legacy_build(client, base, cid, build, *, web=None):
    service = client.app.state.knowledge_service
    workspace_id = workspace(client)["X-Gunther-Workspace-Id"]
    model, effort = service._output_model(None, "low")
    kind = OUTPUT_TYPES[build.type].shape
    limits, pool = OutputLimits(), Pool(1_000_000)
    searched = []
    with service.sessions() as session:
        material = service._output_material(session, base, build.scope)
    notes = service._read_studio_material(
        base, material, pool, build.focus, limits, searched, {},
    )
    if web is not None:
        service._studio_web(
            web, None, build.focus, pool, limits.per_section, searched=searched,
        )
    spec = Spec(
        kind=kind, audience=build.audience, output_type=build.type, brief=build.focus,
        title=build.title, theme=build.theme, design_version=build.design_version,
        length=build.length, language=build.language,
    )
    # The pre-upgrade builder hashed the raw focus, selection, inputs, Web flag and outline.
    # It checkpointed under approach.studio_progress and kept the outline in the request.
    old_spec = asdict(spec)
    del old_spec["emphasize"], old_spec["avoid"]
    old_notes = asdict(notes)
    if not notes.key_results:
        del old_notes["key_results"]
    use = (build.use or "talk") if kind == "slides" else ""
    vision = vision_model(service.models, model, effort) if kind == "slides" else None
    outline = [(row.id, row.heading.strip(), row.goal) for row in build.outline or ()]
    scope = {"selection": build.scope.model_dump(), "inputs": material.inputs}
    progress = {
        "material_key": digest([
            pool_print(pool.items), scope, old_notes, model.ref, effort, build.web, kind,
            [vision[0].ref, vision[1]] if vision else None,
        ]),
        "request_key": digest([old_spec, use, None, outline]),
        "memo": {"answer": "Previously reviewed trial", "language": build.language or "English"},
        "read": {"notes": StudioAgents._notes_text(notes), "digest": ""},
    }
    if use:
        progress["purpose"] = {"purpose": "general", "reason": "Previously decided"}
    old = Work(
        key=f"draft_{cid}",
        spec=WorkSpec(
            kind=kind, output_type=Line(text=build.type, mark="you"),
            use=Line(text=build.use, mark="you") if build.use else None,
            length=Line(text=build.length, mark="you") if build.length else None,
            language=build.language, theme=build.theme, design_version=build.design_version,
        ),
        build_key=service._output_fingerprint(
            workspace_id, base, "build-draft", build.model_dump(mode="json"),
        ),
        approach={"studio_progress": progress},
    )
    with service.sessions.begin() as session:
        row = session.get(ArtifactConversation, cid)
        row.state_json = json.dumps({"works": [old.model_dump()], "active": old.key})
        row.brief_json = '{"goal":{"text":"Different conversation goal"}}'
        session.add_all([
            ArtifactMessage(
                id="acm_interrupted_origin", conversation_id=cid, role="user", kind="build",
                text=build.focus, payload_json=json.dumps({
                    "build": build.model_dump(mode="json", by_alias=True),
                    "model": model.ref, "effort": effort, "web": build.web,
                }),
            ),
            ArtifactMessage(
                id="acm_interrupted_failure", conversation_id=cid, role="assistant", kind="failed",
                text="Writing failed", payload_json=json.dumps({
                    "to": "acm_interrupted_origin", "error": "Writing failed",
                }),
            ),
        ])
    return old, model, effort


def test_pre_upgrade_checkpoint_reopens_with_its_request_options(tmp_path, monkeypatch):
    fake = FakeProvider(deck_worker)
    with app_for(tmp_path, fake) as client:
        base, sources = library(client, notes={"Trial": " ".join(PASSAGES), "Other": "Background"})
        headers = workspace(client)
        service = client.app.state.knowledge_service
        cid = create(client, base, "interrupted_old_deck")["id"]
        build = BuildTurnInput(
            type="slides", scope={"mode": "selection", "sourceIds": [sources["Trial"]]},
            focus="Explain the trial", title="A handout", audience="student", use="read",
            length="short", language="French", theme="ink", design_version=1, web=True,
        )
        web = Tool("search_web", "Recorded results", lambda _: [
            web_hit("Outside evidence", "https://example.org/published-trial"),
        ], "web")
        monkeypatch.setattr(service, "_web_tool", lambda enabled: (web if enabled else None, None))
        old, model, effort = _checkpoint_legacy_build(client, base, cid, build, web=web)
        [shown] = client.get(
            conversations(base, f"/{cid}"), headers=headers,
        ).raise_for_status().json()["works"]
        assert shown["plan"] is None
        assert shown["options"] == (
            build.model_dump(mode="json", by_alias=True, exclude={"outline"})
            | {"voices": None, "model": model.ref, "effort": effort}
        )
        calls = len(fake.requests)
        resumed = done(output_turn(client, base, cid, plan_request(
            plan_id=old.key, type_id="slides", scope=shown["options"]["scope"],
        )))["plan"]
        assert resumed["options"] == shown["options"] and resumed["planStatus"] == "ready"
        saved = done(output_turn(client, base, cid, approve_request(resumed)))["artifact"]
        assert "theme: ink" in saved["content"] and saved["provenance"]["language"] == "French"
        assert sum("presentation strategist" in request_text(r) for r in fake.requests[calls:]) == 1
        assert not any(
            "lead analyst" in request_text(r)
            or "You own the presentation purpose" in request_text(r)
            for r in fake.requests[calls:]
        )


@pytest.mark.parametrize("replace_outline", [False, True], ids=["stored", "replacement"])
def test_pre_upgrade_report_publishes_the_fixed_outline(tmp_path, replace_outline):
    report_worker = dispatch("report")

    def reply(request):
        if "planning a written report" in request_text(request):
            return report_worker(request)
        return plan_worker(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        cid = create(client, base, "interrupted_old_report")["id"]
        outline = [
            {"id": "approved_z", "heading": "Overall result", "goal": "Summarize the finding",
             "layout": None, "notes": "Keep the qualification"},
            {"id": "approved_a", "heading": "Trial details", "goal": "Describe the trial",
             "layout": None, "notes": "Explain the method"},
        ]
        build = BuildTurnInput(type="report", focus="Summarize the trial", outline=outline)
        old, _, _ = _checkpoint_legacy_build(client, base, cid, build)
        request = plan_request(plan_id=old.key)
        if replace_outline:
            outline = [
                {**row, "id": f"new_{row['id']}", "heading": f"Revised {row['heading']}"}
                for row in outline
            ]
            request["initialOutline"] = outline

        calls = len(fake.requests)
        plan = done(output_turn(client, base, cid, request))["plan"]
        assert plan["planStatus"] == "ready"
        assert [{field: row[field] for field in outline[0]} for row in plan["outline"]] == outline
        assert plan["basis"] == "user_edited"
        assert plan["proposal"]["count"] == {"mode": "exact", "value": 2}
        assert versions_of(client) == []
        assert not any("lead analyst" in request_text(r) for r in fake.requests[calls:])

        saved = done(output_turn(client, base, cid, approve_request(plan)))["artifact"]
        assert saved["versionNumber"] == 1
        assert [(row["id"], row["heading"]) for row in saved["outline"]] == [
            (row["id"], row["heading"]) for row in outline
        ]
        assert [section["heading"] for section in saved["sections"]] == [
            row["heading"] for row in outline
        ]
        assert saved["provenance"]["creationPlan"]["outline"] == plan["outline"]


@pytest.mark.parametrize("missing", ["model", "material"])
def test_legacy_preflight_rejects_without_creating_a_conversation(tmp_path, missing):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={} if missing == "material" else None)
        if missing == "model":
            client.app.state.knowledge_service.models = None
        before = len(fake.requests)
        event, error = legacy_build(client, base, body())[-1]
        assert event == "error" and (error["status"], error["code"]) == (409, "plan_required")
        assert len(fake.requests) == before
        listed = client.get(conversations(base), headers=workspace(client))
        assert listed.raise_for_status().json() == []
