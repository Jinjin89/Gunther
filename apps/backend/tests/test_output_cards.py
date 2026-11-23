import json
import re
from pathlib import Path

import pytest
from fake_models import FakeProvider, is_sentence_checking, orchestrator_replies, studio_task
from test_artifact_agent import done, turn
from test_outputs import app_for, library, route, workspace

from gunther.output_types import OUTPUT_TYPES, SHAPES
from gunther.outputs import join_document, split_document

FIXTURE = Path(__file__).parents[2] / "desktop/src/outputs/fixtures/document-sections.json"
EXAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))
FLASH = "## What does factor T do?\nIt steers cells toward the red-cell branch [1]."
QUIZ = (
    "## (quiz) Which branch grows without factor T?\n"
    "- [ ] Glass cells\n- [x] White-cell\n"
    "Why: Loss of T expanded the white-cell branch [1]."
)
EVIDENCE = (
    "Factor T steered cells to the red-cell branch. Loss of T expanded the white-cell branch."
)


def document(type_id, *units):
    shape = SHAPES[OUTPUT_TYPES[type_id].shape]
    return shape.join(shape.preamble(type_id), units)


@pytest.mark.parametrize("case", EXAMPLES, ids=[case["name"] for case in EXAMPLES])
def test_split_and_join_match_the_shared_fixture(case):
    preamble, units = split_document(case["content"], case["shape"])
    assert (preamble, units) == (case["preamble"], case["sections"])
    assert join_document(preamble, units, case["shape"]) == case["joined"]


def test_the_cards_format_accepts_the_plan_and_enforces_the_limit():
    case = next(case for case in EXAMPLES if case["shape"] == "cards")
    shape = SHAPES[case["shape"]]
    assert shape.format_check(case["content"]) == []
    assert shape.exports == ("markdown", "pdf")
    assert OUTPUT_TYPES["flashcards"].direct_problems(document("flashcards", *([FLASH] * 41))) == [
        "The limit for Flashcards is 40 cards.",
    ]
    assert shape.check_text(FLASH) == [
        "What does factor T do? It steers cells toward the red-cell branch [1].",
    ]
    assert shape.check_text(QUIZ) == [
        "Which branch grows without factor T? Answer: White-cell. "
        "Loss of T expanded the white-cell branch [1].",
    ]


def test_direct_format_reports_the_expected_flashcards_header_when_missing():
    assert OUTPUT_TYPES["flashcards"].direct_problems(FLASH) == [
        "Start with <!-- cards: flashcards; design: v1 -->.",
    ]


def test_direct_format_reports_a_wrong_header_alongside_a_card_error():
    draft = document("flashcards", QUIZ.split("\nWhy:", 1)[0])
    assert OUTPUT_TYPES["quiz"].direct_problems(draft) == [
        "Start with <!-- cards: quiz; design: v1 -->.",
        'Card 1: Finish the quiz card with one "Why:" explanation after its options.',
    ]


@pytest.mark.parametrize("card,problem", [
    (QUIZ.replace("[x]", "[ ]"), 'exactly one quiz option with "[x]"'),
    (QUIZ.replace("[ ]", "[x]"), 'exactly one quiz option with "[x]"'),
    (QUIZ.split("\nWhy:")[0], 'one "Why:" explanation'),
    ("## What does this mean?", "non-empty answer"),
    ("## What does this mean?\n[1]", "non-empty answer"),
])
def test_the_format_reports_incomplete_cards(card, problem):
    shape = SHAPES["cards"]
    [message] = shape.format_check(document("quiz", card))
    assert problem in message
    assert shape.check_text(card) == []


def checks_in(request):
    prompt = request["messages"][-1]["content"]
    shown = prompt.split("Answer, by sentence:\n", 1)[1].split("\n\nSources:", 1)[0]
    return [(int(n), claim) for n, claim in re.findall(r"^\((\d+)\) (.+)$", shown, re.M)]


def studio_reply(draft, *, repair=None):
    def reply(request):
        task = studio_task(request)
        system = request["messages"][0]["content"]
        if "lead analyst" in task:
            return json.dumps({"title": "Cell fates", "answer": EVIDENCE})
        if "Fix these format problems" in task:
            assert repair is not None, task
            return repair
        if "Write the complete" in task:
            return draft
        if is_sentence_checking(request):
            claims = checks_in(request)
            assert claims and all("Glass cells" not in claim for _, claim in claims)
            return json.dumps({"sentences": [
                {"n": n, "verdict": "not" if "Answer: Blue-cell" in claim else "supported"}
                for n, claim in claims
            ]})
        if "Unsupported in part" in task:
            return task.split("Part 1:\n", 1)[1].split("\n\nUnsupported in part", 1)[0]
        if "as instructed, changing only what" in system:
            return "## What follows from losing factor T?\nThe white-cell branch expands [1]."
        if "You are the relevance" in system:
            return '{"relevant":[1]}'
        raise AssertionError(task or system)

    return reply


def conversation(client):
    client.app.state.knowledge_service.extractor = None
    base_id, _ = library(client, notes={"Lineage study": EVIDENCE})
    path = f"/api/knowledge-bases/{base_id}/artifact-conversations"
    cid = client.post(path, headers=workspace(client), json={}).raise_for_status().json()["id"]
    return base_id, cid


def build_cards(client, base_id, cid, type_id):
    return turn(
        client, base_id, cid, "", "build",
        build={"type": type_id, "focus": "Lineage results", "length": "short"},
    )


@pytest.mark.parametrize("type_id,card", [("flashcards", FLASH), ("quiz", QUIZ)])
def test_direct_builds_run_the_shared_stages_and_cite_the_source(tmp_path, type_id, card):
    markdown = document(type_id, card)
    fake = FakeProvider(studio_reply(markdown), write_lock_path=tmp_path / "gunther.sqlite")
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        events = build_cards(client, base_id, cid, type_id)
        artifact = done(events)["artifact"]
        assert (artifact["kind"], artifact["style"], artifact["format"]) == (
            "cards", type_id, "cards",
        )
        assert artifact["content"] == markdown
        [citation] = artifact["citations"]
        assert citation["quote"] == EVIDENCE and citation["ref"] == 1
        assert citation["blockId"] and citation["sourceRevisionId"]
        [section] = artifact["sections"]
        assert section["coverage"]["status"] == "complete" and section["issues"] == []
        [claim] = section["coverage"]["claims"]
        assert claim["text"] == SHAPES["cards"].check_text(card)[0]
        assert artifact["keyResults"]["state"] == "queued"
        completed = [
            (event["id"], event["label"])
            for name, event in events if name == "stage" and event["state"] == "done"
        ]
        assert completed == [
            ("read", "Reading the material"),
            ("memo", "Finding what is worth saying"),
            ("write", f"Writing {OUTPUT_TYPES[type_id].label.lower()}"),
            ("format", "Checking the card set format"),
            ("verify", "Checking every claim against its source"),
            ("assemble", "Assembling the card set"),
        ]
        assert sum("Write the complete" in studio_task(r) for r in fake.requests) == 1

        if type_id == "quiz":
            def distill(request):
                data = json.loads(request["messages"][-1]["content"])
                assert len(data["claims"]) == 1
                assert "Answer: White-cell." in data["claims"][0]
                assert "Glass cells" not in json.dumps(data)
                return json.dumps({"items": [{
                    "text": "Loss of T expanded the white-cell branch.", "passages": [1],
                }]})

            fake.replies[:] = [distill, '{"sentences":[{"n":1,"verdict":"supported"}]}']
            assert client.app.state.processing_worker.run_once(ai=True)
            kept = client.get(
                f"/api/knowledge-bases/{base_id}/key-results", headers=workspace(client),
            ).raise_for_status().json()
            assert [item["text"] for item in kept] == ["Loss of T expanded the white-cell branch."]
    assert not fake.write_lock_failures


def test_a_wrong_checked_option_is_flagged(tmp_path):
    wrong = QUIZ.replace("[x] White-cell", "[x] Blue-cell")
    fake = FakeProvider(studio_reply(document("quiz", wrong)))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        artifact = done(build_cards(client, base_id, cid, "quiz"))["artifact"]
        edited = client.post(
            route(base_id, artifact["id"], "/edits"), headers=workspace(client), json={
                "clientRequestId": "quiz_edit_01",
                "content": artifact["content"].replace("Loss of T", "Removing T"),
            },
        ).raise_for_status().json()
        assert not edited["sections"][0]["checked"]
        rechecked = client.post(
            route(base_id, edited["id"], "/check"), headers=workspace(client),
        ).raise_for_status().json()
    [section] = artifact["sections"]
    [issue] = section["issues"]
    assert issue["verdict"] == "unsupported" and "Answer: Blue-cell." in issue["claim"]
    assert "Glass cells" not in issue["claim"]
    assert artifact["keyResults"] is None
    [issue_again] = rechecked["sections"][0]["issues"]
    assert "Answer: Blue-cell." in issue_again["claim"]
    assert "Glass cells" not in issue_again["claim"]


@pytest.mark.parametrize("repaired", [False, True])
def test_direct_format_tries_one_repair(tmp_path, repaired):
    broken = document("quiz", QUIZ.split("\nWhy:")[0])
    fake = FakeProvider(studio_reply(broken, repair=document("quiz", QUIZ) if repaired else broken))
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        events = build_cards(client, base_id, cid, "quiz")
    assert sum("Fix these format problems" in studio_task(r) for r in fake.requests) == 1
    if repaired:
        assert done(events)["artifact"]["content"] == document("quiz", QUIZ)
    else:
        name, error = events[-1]
        assert name == "error" and '"Why:"' in error["detail"]
        assert any(name == "stage" and event["id"] == "format" and event["state"] == "failed"
                   for name, event in events)
        assert not any(is_sentence_checking(r) for r in fake.requests)


def test_refining_one_card_preserves_the_other_card_and_its_identity(tmp_path):
    second = "## What happens without T?\nThe white-cell branch expands [1]."
    reply = studio_reply(document("flashcards", FLASH, second))
    fake = FakeProvider(reply)
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        base_id, cid = conversation(client)
        before = done(build_cards(client, base_id, cid, "flashcards"))["artifact"]
        fake.replies[:] = [orchestrator_replies([
            {"actions": [{"do": "write", "parts": [1], "instruction": "Ask about loss of T."}]},
            {"actions": [{"do": "save", "text": "Updated the first card."}]},
        ], reply)]
        after = done(turn(
            client, base_id, cid, "Change the first card to ask about loss of T.",
            targetLineageId=before["lineageId"], focus={"section": 1},
        ))["artifact"]
    preamble, units = split_document(after["content"], after["kind"])
    assert preamble == SHAPES["cards"].preamble("flashcards")
    assert units == [
        "## What follows from losing factor T?\nThe white-cell branch expands [1].", second,
    ]
    assert [item["id"] for item in after["outline"]] == [item["id"] for item in before["outline"]]
    assert after["supersedesArtifactId"] == before["id"]
