import json

import pytest
from fake_models import FakeProvider, is_sentence_checking, orchestrator_replies, studio_task
from test_artifact_agent import done, turn
from test_output_cards import EVIDENCE, EXAMPLES, checks_in, conversation, document
from test_outputs import app_for, route, workspace

from gunther.citations import check_citations, renumber_citations
from gunther.output_types import SHAPES
from gunther.outputs import section_hash

MAP = SHAPES["map"]
EXAMPLE = next(case["joined"] for case in EXAMPLES if case["shape"] == "map")
CLAIM = "Blood cell development › Factor T › Steers progenitors toward the red-cell branch [1]"


@pytest.mark.parametrize("case", [case for case in EXAMPLES if case["shape"] == "map"])
def test_map_split_preserves_ancestry_and_checks_cited_nodes(case):
    _, units = MAP.split(case["content"])
    assert [[*unit.ancestors, unit.label] for unit in units] == case["paths"]
    assert not MAP.format_check(case["content"])
    assert len({section_hash(unit) for unit in units}) == len(units)
    if case["joined"] == EXAMPLE:
        assert [MAP.check_text(unit) for unit in units] == [[], [], [CLAIM]]
    else:
        assert MAP.check_text(units[2]) == ["Trial › Setup › Timing [2]"]


def test_detached_node_indentation_does_not_hide_its_prose_citations():
    node = "    - Call `f[7]` [3] [9]"
    checked, used = check_citations(MAP.markup(node), {3})
    assert used == [3] and checked.rstrip() == "    - Call `f[7]` [3]"
    assert renumber_citations(MAP.markup(checked), used).rstrip() == "    - Call `f[7]` [1]"


@pytest.mark.parametrize(("nodes", "problem"), [
    (["- Root", "- Another"], "exactly one root"),
    (["- Root", "   - Child"], "two spaces per level"),
    (["- Root", "    - Child"], "must have a parent"),
    (["- Root", "  - [1]"], "non-empty text"),
    (["- Root", *("  - Child" for _ in range(60))], "60 nodes"),
])
def test_map_requires_one_root_valid_nesting_labels_and_at_most_sixty_nodes(nodes, problem):
    problems = MAP.format_check(document("mind_map", *nodes))
    assert any(problem in text for text in problems)


def model_replies(draft, rewrite=None):
    def reply(request):
        task = studio_task(request)
        system = request["messages"][0]["content"]
        if "lead analyst" in task:
            return json.dumps({"title": "Cell development", "answer": EVIDENCE})
        if "Write the complete Mind map" in task:
            return draft
        if is_sentence_checking(request):
            claims = checks_in(request)
            assert all(" › " in text and "[1]" in text for _, text in claims)
            return json.dumps({"sentences": [{"n": n, "verdict": "supported"} for n, _ in claims]})
        if "as instructed, changing only what" in system:
            assert rewrite is not None and "complete replacement subtree only" in system
            return rewrite
        if "You are the relevance" in system:
            return '{"relevant":[1]}'
        raise AssertionError(task or system)

    return reply


def build_map(client, base_id, cid):
    return turn(client, base_id, cid, "", "build", build={"type": "mind_map", "length": "short"})


def checked_claims(requests):
    return [text for request in requests if is_sentence_checking(request)
            for _, text in checks_in(request)]


def test_mind_map_build_checks_ancestor_context_and_enqueues_key_results(tmp_path):
    fake = FakeProvider(model_replies(EXAMPLE), write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        events = build_map(client, base_id, cid)
        artifact = done(events)["artifact"]
        assert artifact["content"] == EXAMPLE
        assert (artifact["kind"], artifact["style"], artifact["outputType"]) == (
            "map", "mind_map", "Mind map",
        )
        assert checked_claims(fake.requests) == [CLAIM]
        assert all(section["checked"] for section in artifact["sections"])
        assert artifact["keyResults"]["state"] == "queued"
        complete = [event["id"] for name, event in events
                    if name == "stage" and event["state"] == "done"]
        assert complete == ["read", "memo", "write", "format", "verify", "assemble"]
        assert not fake.write_lock_failures

        def distill(request):
            data = json.loads(request["messages"][-1]["content"])
            assert data["claims"] == [CLAIM]
            return json.dumps({"items": [{"text": EVIDENCE.split(". ")[0] + ".", "passages": [1]}]})

        fake.replies[:] = [distill, '{"sentences":[{"n":1,"verdict":"supported"}]}']
        assert client.app.state.processing_worker.run_once(ai=True)
        kept = client.get(
            f"/api/knowledge-bases/{base_id}/key-results", headers=workspace(client),
        ).raise_for_status().json()
        assert len(kept) == 1


def test_refine_replaces_only_the_selected_subtree_and_rechecks_its_context(tmp_path):
    initial = EXAMPLE + "\n  - Without Factor T\n    - The white-cell branch expands [1]"
    rewrite = "- With Factor T\n  - Steers progenitors toward the red-cell branch [1]\n  - Context"
    reply = model_replies(initial, rewrite)
    fake = FakeProvider(reply)
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        before = done(build_map(client, base_id, cid))["artifact"]
        fake.replies[:] = [orchestrator_replies([
            {"actions": [{"do": "write", "parts": [2], "instruction": "Clarify this branch."}]},
            {"actions": [{"do": "save", "text": "Updated the first branch."}]},
        ], reply)]
        start = len(fake.requests)
        after = done(turn(
            client, base_id, cid, "Clarify the first branch.",
            targetLineageId=before["lineageId"], focus={"section": 2},
        ))["artifact"]
        assert checked_claims(fake.requests[start:]) == [CLAIM.replace("Factor T", "With Factor T")]
    expected = initial.replace("  - Factor T\n", "  - With Factor T\n").replace(
        "\n  - Without Factor T", "\n    - Context\n  - Without Factor T",
    )
    assert after["content"] == expected
    assert after["supersedesArtifactId"] == before["id"]
    before_siblings = [row["id"] for row in before["outline"][-2:]]
    assert [row["id"] for row in after["outline"][-2:]] == before_siblings
    assert all(section["checked"] for section in after["sections"])


def test_typed_ancestor_change_invalidates_the_leaf_check(tmp_path):
    fake = FakeProvider(model_replies(EXAMPLE))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        built = done(build_map(client, base_id, cid))["artifact"]
        edited = client.post(
            route(base_id, built["id"], "/edits"), headers=workspace(client), json={
                "clientRequestId": "map_edit_01",
                "content": built["content"].replace("Factor T", "Presence of Factor T"),
            },
        ).raise_for_status().json()
        assert not edited["sections"][2]["checked"]
        rechecked = client.post(
            route(base_id, edited["id"], "/check"), headers=workspace(client),
        ).raise_for_status().json()
        assert rechecked["sections"][2]["checked"]
        expected = CLAIM.replace("Factor T", "Presence of Factor T")
        assert checked_claims(fake.requests)[-1] == expected
