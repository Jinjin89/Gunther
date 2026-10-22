import contextlib
import json
import re

from fake_models import FakeProvider, gateway, model

from gunther.agent import Evidence
from gunther.outputs import Notes, Pool, Spec
from gunther.schemas import ConversationCitationOut
from gunther.studio import format as fmt
from gunther.studio.pipeline import StudioAgents

PASSAGES = [
    "The trial followed 412 buildings in six districts over the summers 2021 to 2023.",
    "Raw cooling electricity fell 18%; adjusted for summer heat the saving was 9%.",
    "The coating's albedo fell from 0.82 to 0.55 within 18 months.",
]

DECK = """<!-- layout: title; design: v2; theme: paper -->
# Cool roofs work, but at half the headline saving
The 18% saving is 9% after weather adjustment [2]

---

<!-- layout: stats -->
## The trial was large enough to trust the direction
- **412** buildings in six districts [1]
- **3** summers, 2021–2023 [1]
Note: Size first.

---

<!-- layout: chart; chart: column -->
## Weather adjustment halves the saving
| Measure | Saving (%) |
| --- | --- |
| Raw | 18 [2] |
| **Adjusted** | 7 [2] |
Plan with the adjusted figure [2]
Note: The bars.
"""


def pool() -> Pool:
    out = Pool(50)
    for number, text in enumerate(PASSAGES, 1):
        citation = ConversationCitationOut(
            id=f"cit{number}",
            source_id=f"src{number}",
            source_title=f"Source {number}",
            quote=text,
            locator="p. 1",
            status="provisional",
            confidence=1.0,
            source_revision_id=f"rev{number}",
            block_id=f"blk{number}",
        )
        out.adopt(
            Evidence(kind="library", title=f"Source {number}", text=text, payload=(citation, None))
        )
    return out


def router(request):
    system = str(request["messages"][0]["content"])
    if "lead analyst" in system:
        return json.dumps(
            {"language": "English", "answer": "Cool roofs save 9% after adjustment [2]",
             "insights": [{"claim": "Saving halves", "so_what": "Budget", "refs": [2]}],
             "facts": [], "tensions": [], "questions": [], "title": "Cool roofs"}
        )
    if "presentation strategist" in system:
        return json.dumps(
            {"title": "Cool roofs work, but at half the headline saving", "subtitle": "s",
             "slides": [
                 {"title": "Cool roofs work", "layout": "title", "job": "", "refs": []},
                 {"title": "The trial was large", "layout": "stats", "job": "", "refs": [1]},
                 {"title": "Weather adjustment halves the saving", "layout": "chart",
                  "job": "", "refs": [2]},
             ]}
        )
    if "senior partner" in system:
        return json.dumps({"understood": "Saving halves", "verdict": "ready", "problems": []})
    if "presentation writer" in system:
        return DECK
    if "fact-checker" in system:
        return fmt.split_slides(DECK)[2]  # could not fix it
    if "checking step" in system:
        sentences = re.findall(r"^\((\d+)\)", request["messages"][-1]["content"], re.M)
        return json.dumps({"sentences": [{"n": int(n), "verdict": "supported"} for n in sentences]})
    raise AssertionError(system[:200])


def test_a_deck_is_made_in_the_new_format_and_an_unstated_chart_value_becomes_a_table():
    fake = FakeProvider(router)
    agents = StudioAgents(gateway(fake), use="talk")
    events = []
    built = agents.make(
        model(), "high", Spec("slides", "collaborator", brief="A 10-minute talk"),
        pool(), Notes(), events.append,
    )
    slides = fmt.split_slides(built.content)
    assert slides[0].startswith("<!-- layout: title; design: v2; theme: paper -->")
    # 7% is not in passage 2, so the chart is drawn as a table.
    assert fmt.parse_slide(slides[2]).layout == "table"
    assert any(item["step"] == "verify" for item in agents.skipped)
    # Citations are renumbered in reading order: 2, 1 → 1, 2.
    assert "[1]" in slides[0] and len(built.citations) == 2
    assert built.approach["method"] == "studio"
    assert {e["id"] for e in events if e.get("type") == "stage"} >= {"memo", "story", "write"}
    # Few calls: memo, storyline, critic, draft, then checks.
    asked = [str(r["messages"][0]["content"])[:40] for r in fake.requests]
    writers = [
        r for r in fake.requests if "presentation writer" in str(r["messages"][0]["content"])
    ]
    assert len(writers) == 1
    assert len(asked) <= 10


def test_a_conversation_makes_a_studio_deck_in_one_action_and_can_rewrite_one_slide(tmp_path):
    from fake_models import orchestrator_replies
    from test_artifact_agent import done, kept, turn
    from test_outputs import app_for, library, workspace

    from gunther.studio.work import is_studio

    notes = {f"Source {n}": text for n, text in enumerate(PASSAGES, 1)}
    rewritten = (
        "<!-- layout: stats -->\n## Three summers and 412 buildings back the result\n"
        "- **412** buildings in six districts [1]\n- **3** summers, 2021–2023 [1]\nNote: Size."
    )

    def inner(request):
        system = str(request["messages"][0]["content"])
        if "working on one slide" in system:
            return rewritten
        if "knowledge assertions" in system:
            return json.dumps({"assertions": []})
        return router(request)

    decisions = [
        {"actions": [{"do": "select", "artifact": "new:slides"}, {"do": "make"},
                     {"do": "save", "text": "Made the deck."}]},
    ]
    fake = FakeProvider(orchestrator_replies(decisions, inner))
    with app_for(tmp_path, fake) as client:
        client.app.state.knowledge_service.output_method = "studio"
        base_id, _ = library(client, notes=notes)
        client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations",
            headers=workspace(client),
            json={"id": "talk", "title": "Trial"},
        )
        result = done(turn(client, base_id, "talk", "A 10-minute deck on the trial"))
        assert result["outcome"] == "saved", result
        content = result["artifact"]["content"]
        assert "design: v2" in content and "## The trial was large" in content
        workspace, _ = kept(client, "talk")
        work = workspace.works[0]
        assert is_studio(work) and len(work.outline) == 3
        fake.replies[:] = [orchestrator_replies(
            [{"actions": [{"do": "write", "parts": [2], "instruction": "Lead with the size"},
                          {"do": "save", "text": "Rewrote slide 2."}]}],
            inner,
        )]
        again = done(turn(client, base_id, "talk", "Make slide 2 lead with the size"))
        assert again["outcome"] == "saved", again
        assert "Three summers and 412 buildings" in again["artifact"]["content"]


def test_a_failed_build_resumes_after_its_memo_and_storyline():
    from gunther.llm import ModelError

    calls = {"writer": 0}

    def failing(request):
        system = str(request["messages"][0]["content"])
        if "presentation writer" in system and calls["writer"] == 0:
            calls["writer"] += 1
            raise ModelError("The connection broke")
        return router(request)

    fake = FakeProvider(failing)
    saved = {}
    spec = Spec("slides", "collaborator", brief="A 10-minute talk")
    agents = StudioAgents(gateway(fake), use="talk")
    with contextlib.suppress(ModelError):
        agents.make(model(), "high", spec, pool(), Notes(), checkpoint=saved.update)
    assert saved["memo"] and saved["storyline"]
    before = len(fake.requests)
    built = StudioAgents(gateway(fake), use="talk").make(
        model(), "high", spec, pool(), Notes(), resume=dict(saved)
    )
    again = [str(r["messages"][0]["content"]) for r in fake.requests[before:]]
    assert not any("lead analyst" in s or "presentation strategist" in s for s in again)
    assert "design: v2" in built.content
