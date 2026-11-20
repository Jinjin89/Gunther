import contextlib
import json
import re
from dataclasses import replace

import pytest
from fake_models import FakeProvider, gateway, model, output_replies

from gunther.agent import Evidence, Tool
from gunther.llm import ModelError
from gunther.outputs import Issue, Notes, Planned, Pool, Spec, split_document
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
| **Adjusted** | 9 [2] |
Plan with the adjusted figure [2]
Note: The bars.
"""
UNSUPPORTED_DECK = DECK.replace("9 [2] |", "7 [2] |")


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


def designed(system: str) -> str:
    """What a careful designer sends back: the slide's own words and citations in a section."""

    found = re.search(
        r'Its content \(layout hint "(\w+)"\):\n(.*)\n\nReply with the HTML only', system, re.S
    )
    assert found, system[-300:]
    layout, content = found.groups()
    lines = []
    for line in content.split("\n"):
        if re.fullmatch(r"\|?[\s:|-]+\|?", line):
            continue
        if line.startswith("|"):
            if layout == "chart":
                continue
            line = line.replace("|", "  ")
        tag = "h1" if line.startswith("# ") else "h2" if line.startswith("## ") else "p"
        text = re.sub(r"\*\*|^[-*>#\s]+|^\d+[.)]\s+", "", line)
        if text.strip():
            lines.append(f"<{tag}>{text.strip()}</{tag}>")
    if layout == "chart":
        lines.append("<gunther-chart></gunther-chart>")
    dark = " dark" if layout in ("title", "section") else ""
    return f'<section class="slide{dark}">\n' + "\n".join(lines) + "\n</section>"


def request_text(request):
    """Text from both roles: studio tasks follow the shared material now."""

    texts = []
    for message in request["messages"]:
        content = message["content"]
        texts.append(
            content
            if isinstance(content, str)
            else "\n".join(part["text"] for part in content if part["type"] == "text")
        )
    return "\n\n".join(texts)


def router(request, deck=DECK):
    system = request_text(request)
    if "knowledge assertions" in system:
        return '{"assertions": []}'
    if "planning a written report" in system:
        return json.dumps({"title": "Findings", "sections": [{"heading": "Results"}]})
    if "Write the report" in system:
        return "# Findings\n\n## Results\nThe trial establishes the outcome [1]."
    if "You own the presentation purpose decision" in system:
        return json.dumps({"purpose": "general", "reason": "A general briefing"})
    if "Choose the visual style" in system:
        return json.dumps({"style": "light", "why": "A calm research brief"})
    if "world-class presentation designer" in system:
        return designed(system)
    if "lead analyst" in system:
        return json.dumps(
            {
                "language": "English",
                "answer": "Cool roofs save 9% after adjustment [2]",
                "insights": [{"claim": "Saving halves", "so_what": "Budget", "refs": [2]}],
                "facts": [],
                "tensions": [],
                "questions": [],
                "title": "Cool roofs",
            }
        )
    if "presentation strategist" in system:
        return json.dumps(
            {
                "title": "Cool roofs work, but at half the headline saving",
                "subtitle": "s",
                "slides": [
                    {"title": "Cool roofs work", "layout": "title", "job": "", "refs": []},
                    {"title": "The trial was large", "layout": "stats", "job": "", "refs": [1]},
                    {
                        "title": "Weather adjustment halves the saving",
                        "layout": "chart",
                        "job": "",
                        "refs": [2],
                    },
                ],
            }
        )
    if "senior partner" in system:
        return json.dumps({"understood": "Saving halves", "verdict": "ready", "problems": []})
    if "presentation writer" in system:
        return deck
    if "core message" in system:
        return re.search(r"^Subtitle: (.*)$", system, re.M).group(1)
    if "fact-checker" in system:
        return fmt.split_slides(deck)[2]  # could not fix it
    if "checking step" in system:
        sentences = re.findall(r"^\((\d+)\) (.+)$", request["messages"][-1]["content"], re.M)
        return json.dumps({"sentences": [
            {"n": int(n), "verdict": "not" if "| **Adjusted** | 7 " in line else "supported"}
            for n, line in sentences
        ]})
    raise AssertionError(system[:200])


def test_a_deck_is_made_in_the_new_format_and_an_unstated_chart_value_becomes_a_table():
    fake = FakeProvider(lambda request: router(request, UNSUPPORTED_DECK))
    agents = StudioAgents(gateway(fake), use="talk")
    events = []
    built = agents.make(
        model(),
        "high",
        Spec("slides", "collaborator", brief="A 10-minute talk"),
        pool(),
        Notes(),
        events.append,
    )
    slides = fmt.split_slides(built.content)
    assert slides[0].startswith("<!-- layout: title; design: v2; theme: paper; style: light -->")
    # The Checker rejects the adjusted value, so the chart is drawn as a table.
    assert fmt.parse_slide(slides[2]).layout == "table"
    assert any(item["step"] == "verify" for item in agents.skipped)
    # Citations are renumbered in reading order: 2, 1 → 1, 2.
    assert "[1]" in slides[0] and len(built.citations) == 2
    assert built.approach["method"] == "studio"
    assert {e["id"] for e in events if e.get("type") == "stage"} >= {"memo", "story", "write"}
    # Few calls: memo, storyline, critic, draft, then checks.
    asked = [
        request_text(r)[:40]
        for r in fake.requests
        if "designer" not in request_text(r) and "Choose the visual" not in request_text(r)
    ]
    writers = [r for r in fake.requests if "presentation writer" in request_text(r)]
    assert len(writers) == 1
    assert len(asked) <= 11


@pytest.mark.parametrize(
    ("kind", "verdict"),
    [("slides", value) for value in ("not", "unchecked", "uncited", "supported")]
    + [("report", "not"), ("report", "supported")],
)
def test_direct_build_draws_a_chart_only_when_each_cited_row_is_supported(tmp_path, kind, verdict):
    from test_outputs import app_for, body, build, built, library

    adjusted = 18 if verdict == "not" else 9
    coverage_ref = "" if verdict == "uncited" else " [1]"
    label = "Weather adjusted" if verdict == "not" else "Weather adj. estimate"
    row = f"| {label} | {adjusted} [1] | 30{coverage_ref} |"
    table = (
        "| Measure | Saving (%) | Coverage (%) |\n| --- | --- | --- |\n"
        "| Raw | 18 [1] | 30 [1] |"
    )
    if kind == "slides":
        opening = DECK.split("| Measure", 1)[0].replace("[2]", "[1]")
        proposed = f"{opening}{table}\n{row}\nNote: Rates."
    else:
        proposed = (
            "# Findings\n\n## Results\n\n:::exhibit chart=column\nCooling savings by estimate\n"
            f"{table}\n{row}\nSource: Field trial [1]\n:::"
        )
    seen = []

    def reply(request):
        task = request_text(request)
        if "presentation writer" in task or "Write the report" in task:
            return proposed
        if "A fact-checker compared" in task:
            return task.split("Part 1:\n", 1)[1].split("\n\nUnsupported in part", 1)[0]
        if "checking step" in task:
            sentences = []
            for n, line in re.findall(r"^\((\d+)\) (.+)$", task, re.M):
                decision = "supported"
                if line.startswith(row):
                    seen.append(line)
                    if verdict == "unchecked":
                        continue
                    if verdict == "not":
                        decision = "not"
                sentences.append({"n": int(n), "verdict": decision})
            return json.dumps({"sentences": sentences})
        return router(request)

    adjusted_in_source = "nine percent" if verdict == "supported" else "9%"
    evidence = f"Raw savings were 18%, adjusted savings {adjusted_in_source}, both at 30% coverage."
    with app_for(tmp_path, FakeProvider(reply), skills=True) as client:
        base, _ = library(client, notes={"Field trial": evidence})
        version = built(build(client, base, body(kind=kind, use="talk")))

    assert row in version["content"]
    drawn = "chart" if verdict == "supported" else "table"
    if kind == "slides":
        assert fmt.parse_slide(fmt.split_slides(version["content"])[2]).layout == drawn
        assert ("<gunther-chart>" in version["content"]) == (drawn == "chart")
    else:
        assert (":::exhibit chart=column" in version["content"]) == (drawn == "chart")
    assert seen and all("Table header | Measure | Saving (%) | Coverage (%) |" in s for s in seen)
    if drawn == "table":
        skipped = version["provenance"]["skill"]["skipped"]
        assert any(item["label"].endswith("drawn as a table") for item in skipped)


def test_direct_report_keeps_the_supported_exhibit_when_its_rows_are_repeated(tmp_path):
    from test_outputs import app_for, body, build, built, library

    exhibit = (
        ":::exhibit chart=column\nSavings by estimate\n| Measure | Saving (%) |\n"
        "| --- | --- |\n| Raw | 18 [1] |\n| Adjusted | 9 [1] |\nSource: Trial [1]\n:::"
    )
    section = "## Results\n\n" + exhibit + "\n\n" + exhibit.replace("(%)", "(points)")

    def unsupported(request):
        prompt = request["messages"][-1]["content"]
        return [int(n) for n in re.findall(r"^\((\d+)\) \| Raw .+Saving \(points\)", prompt, re.M)]

    outline = {"title": "Findings", "sections": [{"heading": "Results"}]}
    fake = FakeProvider(output_replies(outline, [section], unsupported=unsupported))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Trial": "The raw saving was 18%, adjusted saving 9%."})
        version = built(build(client, base, body()))

    exhibits = list(fmt.EXHIBIT.finditer(version["content"]))
    assert [block["attrs"].strip() for block in exhibits] == ["chart=column", ""]
    assert all("| Raw | 18 [1] |" in block["body"] for block in exhibits)


def test_a_direct_deck_rebuild_keeps_approved_headings_through_repairs(tmp_path):
    from test_outputs import app_for, body, build, built, library

    base_slides = fmt.split_slides(DECK)
    draft = UNSUPPORTED_DECK.replace(
        "## The trial was large enough to trust the direction", "## Costs"
    )
    attempted = set()

    def repair(request):
        task = request_text(request)
        if "Slide 2 as written:" in task:
            attempted.add("format")
            return base_slides[1]
        if "fact-checker" in task:
            attempted.add("claim")
            return base_slides[2].replace(
                "## Weather adjustment halves the saving", "## The adjusted estimate is lower"
            )
        return draft if "presentation writer" in task else router(request)

    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        client.app.state.knowledge_service.output_method = "studio"
        base, _ = library(client, notes={f"Source {n}": text for n, text in enumerate(PASSAGES, 1)})
        first = built(build(client, base, body(kind="slides", use="talk")))
        approved = first["outline"]
        approved[1]["heading"] = "Costs"
        fake.replies[:] = [repair]
        rebuilt = built(build(client, base, body(
            kind="slides", use="talk", outline=approved, supersedesArtifactId=first["id"],
        )))
        slides = [fmt.parse_slide(text) for text in fmt.split_slides(rebuilt["content"])]
        assert attempted == {"format", "claim"}
        assert [slide.title for slide in slides] == [row["heading"] for row in approved]
        assert slides[2].layout == "table"


def test_report_repair_cannot_add_a_section_or_displace_the_existing_work(monkeypatch):
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.llm import ModelError
    from gunther.studio.work import load_built

    agents = StudioAgents(gateway(FakeProvider(router)))
    monkeypatch.setattr(
        agents, "_claims", lambda *a, **k: ([Issue("Trial", "unsupported", "")], None)
    )
    monkeypatch.setattr(
        agents, "_fix_claims", lambda *a, **k: ["## Results\nA repair.\n\n## Added\nExtra."]
    )
    made = pool()
    built = agents.make(
        model(), "high", Spec("report", "scientist"), made, Notes(),
        fixed_outline=[Planned("Results", id="results")],
    )
    assert len(split_document(built.content, "report")[1]) == 1
    assert "The trial establishes the outcome" in built.content

    work = Work(key="work", spec=WorkSpec(kind="report"))
    brief, sources = ConversationBrief(), Pool(50)
    load_built(work, built, made, sources, brief)
    before, prior_refs = work.model_dump(), sources.by_ref()
    with pytest.raises(ModelError, match="does not match its outline"):
        load_built(
            work, replace(built, content=built.content + "\n\n## Added\nExtra."),
            made, sources, brief,
        )
    assert work.model_dump() == before
    assert sources.by_ref() == prior_refs


def test_studio_build_checks_the_written_count_against_the_storyline():
    def wrong_count(request):
        reply = router(request)
        if "presentation strategist" in request_text(request):
            story = json.loads(reply)
            story.update(requested_count=2, slides=story["slides"][:2])
            return json.dumps(story)
        return reply

    agents = StudioAgents(gateway(FakeProvider(wrong_count)), use="talk")
    with pytest.raises(ModelError, match="written deck has 3 slides. The brief asks for 2"):
        agents.make(model(), "low", Spec("slides", "Everyone", brief="Two pages"), pool(), Notes())


def test_making_a_draft_after_replan_still_requires_saving(tmp_path):
    from fake_models import orchestrator_replies
    from test_artifact_agent import conversations, done, turn, versions_of
    from test_outputs import app_for, library, workspace

    def inner(request):
        prompt = request_text(request)
        reply = router(request)
        if "lead analyst" in prompt:
            memo = json.loads(reply)
            memo["insights"][0]["refs"] = [1, 2]
            return json.dumps(memo)
        if "presentation strategist" in prompt:
            story = json.loads(reply)
            story.update(requested_count=3, requested_minutes=8)
            for page, text in zip(story["slides"], fmt.split_slides(DECK), strict=True):
                page["title"] = fmt.parse_slide(text).title
            return json.dumps(story)
        if "presentation writer" in prompt:
            assert "speaker notes for a talk lasting 8 minutes" in prompt
        return reply

    fake = FakeProvider(orchestrator_replies([
        {
            "brief": [{
                "field": "length", "text": "3 slides for an 8-minute talk",
                "quote": "3 slides for an 8-minute talk",
            }],
            "actions": [
                {"do": "select", "artifact": "new:slides"},
                {"do": "understand"},
                {"do": "plan", "change": "whole"},
                {"do": "make"},
                {"do": "reply", "text": "The deck is ready."},
            ],
        },
        {"actions": [{"do": "save", "text": "Saved the deck."}]},
    ], inner))
    with app_for(tmp_path, fake) as client:
        client.app.state.knowledge_service.output_method = "studio"
        base, _ = library(client, notes={"Size": PASSAGES[0], "Trial": PASSAGES[1]})
        client.post(conversations(base), headers=workspace(client), json={
            "id": "planned-deck",
        }).raise_for_status()
        events = turn(
            client, base, "planned-deck", "Plan, then write 3 slides for an 8-minute talk.",
        )
        assert any(
            event == "action" and data["state"] == "failed"
            and "changed in this turn and is not saved" in data["note"]
            for event, data in events
        )
        result = done(events)
        assert result["outcome"] == "saved" and result["reply"] == "Saved the deck."
        content = result["artifact"]["content"]
        assert len(split_document(content, "slides")[1]) == 3
        assert len(versions_of(client)) == 1


def test_a_conversation_makes_a_studio_deck_in_one_action_and_can_rewrite_one_slide(tmp_path):
    from fake_models import orchestrator_replies
    from test_artifact_agent import done, kept, turn
    from test_outputs import app_for, library, route, workspace

    from gunther.studio.work import is_studio

    notes = {f"Source {n}": text for n, text in enumerate(PASSAGES, 1)}
    rewritten = (
        "<!-- layout: stats -->\n## Three summers and 412 buildings back the result\n"
        "- **412** buildings in six districts [1]\n- **3** summers, 2021–2023 [1]\nNote: Size."
    )

    def inner(request):
        system = request_text(request)
        if "working on one slide" in system:
            return rewritten
        if "knowledge assertions" in system:
            return json.dumps({"assertions": []})
        return router(request)

    decisions = [
        {
            "actions": [
                {"do": "select", "artifact": "new:slides"},
                {"do": "make"},
                {"do": "save", "text": "Made the deck."},
            ]
        },
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
        held, _ = kept(client, "talk")
        work = held.works[0]
        assert is_studio(work) and len(work.outline) == 3
        deck = result["artifact"]
        themed = deck["content"].replace("theme: paper; style: light", "theme: ink; style: dark")
        assert themed != deck["content"]
        themed = themed.replace("Note: Size first.", "Note: State the sample limits.")
        updated = client.post(route(base_id, deck["id"], "/edits"), headers=workspace(client),
                              json={"clientRequestId": "edit_deck_style", "content": themed})
        assert updated.status_code in (200, 201), updated.text
        before = len(fake.requests)
        goal = "Focus on the budget evidence"
        fake.replies[:] = [orchestrator_replies([{
            "brief": [{"field": "goal", "text": goal, "quote": goal}],
            "actions": [{"do": "make"}, {"do": "save", "text": "Refocused the deck."}],
        }], inner)]
        replanned = done(turn(client, base_id, "talk", goal))
        assert replanned["outcome"] == "saved", replanned["reply"]
        tasks = [request_text(r) for r in fake.requests[before:]]
        assert any("presentation strategist" in task for task in tasks)
        assert not any("Choose the visual style" in task for task in tasks)
        cover = fmt.parse_slide(fmt.split_slides(replanned["artifact"]["content"])[0])
        assert cover.attrs["theme"] == "ink" and cover.attrs["style"] == "dark"
        fake.replies[:] = [
            orchestrator_replies(
                [
                    {
                        "actions": [
                            {"do": "write", "parts": [2], "instruction": "Lead with the size"},
                            {"do": "save", "text": "Rewrote slide 2."},
                        ]
                    }
                ],
                inner,
            )
        ]
        again = done(turn(client, base_id, "talk", "Make slide 2 lead with the size"))
        assert again["outcome"] == "saved", again
        assert "Three summers and 412 buildings" in again["artifact"]["content"]


def test_a_failed_build_ends_the_turn_instead_of_building_or_saving_again(tmp_path):
    from fake_models import is_orchestrating, orchestrator_replies
    from test_artifact_agent import done, turn
    from test_outputs import app_for, library, workspace

    from gunther.llm import ModelError

    def inner(request):
        system = request_text(request)
        if "presentation writer" in system:
            raise ModelError("The connection broke")
        if "knowledge assertions" in system:
            return json.dumps({"assertions": []})
        return router(request)

    decisions = [
        {
            "actions": [
                {"do": "select", "artifact": "new:slides"},
                {"do": "make"},
                {"do": "save", "text": "Made the deck."},
            ]
        },
        {"actions": [{"do": "make"}]},
    ]
    fake = FakeProvider(orchestrator_replies(decisions, inner))
    with app_for(tmp_path, fake) as client:
        client.app.state.knowledge_service.output_method = "studio"
        base_id, _ = library(client, notes={"Source 1": PASSAGES[0]})
        client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations",
            headers=workspace(client),
            json={"id": "talk", "title": "Trial"},
        )
        events = turn(client, base_id, "talk", "A 10-minute deck on the trial")
        result = done(events)
    assert result["outcome"] == "paused", result
    assert "Making it stopped: The connection broke." in result["reply"]
    assert "continue" in result["reply"]
    actions = [body for kind, body in events if kind == "action"]
    assert any(a["state"] == "skipped" and "turn ended" in a["note"] for a in actions)
    # One decision: the failed build is not made again, and nothing unmade is saved.
    assert sum(is_orchestrating(r) for r in fake.requests) == 1


def test_a_failed_build_resumes_after_its_memo_and_storyline():
    from gunther.llm import ModelError

    calls = {"writer": 0}

    def failing(request):
        system = request_text(request)
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
    again = [request_text(r) for r in fake.requests[before:]]
    assert not any("lead analyst" in s or "presentation strategist" in s for s in again)
    assert "design: v2" in built.content


def test_report_rebuild_fingerprints_style_intent_and_replays_legacy(tmp_path, monkeypatch):
    from test_outputs import app_for, body, build, built, library

    from gunther.models import Artifact
    from gunther.schemas import BuildOutputInput

    queries = []
    with app_for(tmp_path, FakeProvider(router)) as client:
        service = client.app.state.knowledge_service
        service.output_method = "studio"
        web = Tool("search_web", "Public web", lambda query: queries.append(query) or [], "web")
        monkeypatch.setattr(service, "_web_tool", lambda wanted: (web if wanted else None, None))
        base, _ = library(client)
        outline = [{"heading": "Results", "id": "results", "goal": "Explain the result",
                    "layout": "points", "layoutLocked": True, "requires": ["older"]}]
        first = built(build(client, base, body(style="decision_brief", outline=outline)))
        request = body(supersedesArtifactId=first["id"], outline=outline, brief="Budget", web=True)
        inherited = built(build(client, base, request))
        assert inherited["style"] == "decision_brief" and queries == ["Budget"]
        assert inherited["outline"] == [{"heading": "Results", "id": "results",
                                         "goal": "Explain the result"}]
        assert built(build(client, base, request))["id"] == inherited["id"]
        assert build(client, base, dict(request, style="auto"))[-1][1]["status"] == 409

        reset = body(style="auto", supersedesArtifactId=inherited["id"], outline=outline)
        assert built(build(client, base, reset))["style"] == "auto"
        assert build(client, base, {k: v for k, v in reset.items() if k != "style"})[-1][1][
            "status"
        ] == 409
        with service.sessions() as session:
            row = session.get(Artifact, inherited["id"])
            row.request_fingerprint = service._output_fingerprint(
                row.workspace_id, base, "build",
                service._output_request_fields(BuildOutputInput.model_validate(request)),
            )
            session.commit()
        assert built(build(client, base, request))["id"] == inherited["id"]
        assert queries == ["Budget"]


# Render and look ------------------------------------------------------------------------


class FakeRenderer:
    """Draws nothing; says a slide overflows while it is in a layout from ``crowded``."""

    def __init__(self, folder, crowded=("stats",), fail=None):
        self.folder, self.crowded, self.fail, self.captures = folder, crowded, fail, 0
        (folder / "slide.png").write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
            )
        )

    def capture(self, built, use="talk"):
        if self.fail:
            raise self.fail
        self.captures += 1
        slides = fmt.split_slides(built.content)
        return {
            "id": f"snap{self.captures}",
            "candidateHash": "x",
            "slides": [
                {
                    "index": n,
                    "image": "slide.png",
                    "overflow": fmt.parse_slide(text).layout in self.crowded,
                    "overflowWhy": "li spills",
                    "smallText": [],
                }
                for n, text in enumerate(slides)
            ],
        }

    def file(self, _snapshot, name):
        return self.folder / name


POINTS = (
    "<!-- layout: points -->\n## The trial was large enough to trust the direction\n"
    "- **412** buildings in six districts [1]\n- **3** summers, 2021–2023 [1]\nNote: Size first."
)


def looking_router(rewrite=POINTS, findings=None):
    def route(request):
        system = request_text(request)
        if "demanding presentation designer" in system:
            return json.dumps({"findings": findings or []})
        if "A designer looked at these slides" in system:
            return rewrite
        return router(request)

    return route


def make_with_look(fake, renderer, *models, design=False):
    # Plain (template) slides by default: this is the look step's older path.
    agents = StudioAgents(gateway(fake, *models), use="talk", renderer=renderer, design=design)
    built = agents.make(
        models[0] if models else model(), "high",
        Spec("slides", "collaborator", brief="A 10-minute talk"), pool(), Notes(),
    )  # fmt: skip
    return agents, built


def test_a_slide_the_renderer_measures_as_overflowing_is_redrawn_once_and_checked(tmp_path):
    renderer = FakeRenderer(tmp_path)
    fake = FakeProvider(looking_router())
    agents, built = make_with_look(fake, renderer)
    slides = fmt.split_slides(built.content)
    assert fmt.parse_slide(slides[1]).layout == "points"
    assert fmt.parse_slide(slides[0]).layout == "title"
    record = built.skill["look"]
    assert record["status"] == "fixed" and record["applied"][0]["slide"] == 2
    assert record["applied"][0]["before"] > record["applied"][0]["after"]
    assert built.skill["renderCheck"]["id"] == f"snap{renderer.captures}"
    assert renderer.captures == 2  # before and after
    assert any(t["step"] == "look" and t["state"] == "done" for t in agents.trail)


def test_a_fix_that_adds_a_number_is_dropped(tmp_path):
    renderer = FakeRenderer(tmp_path)
    greedy = POINTS.replace("six districts", "six districts, up 99%")
    agents, built = make_with_look(FakeProvider(looking_router(rewrite=greedy)), renderer)
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "stats"
    assert built.skill["look"]["rejected"][0]["why"] == "changed a fact"


def test_a_fix_that_draws_worse_goes_back_to_the_old_slide(tmp_path):
    renderer = FakeRenderer(tmp_path, crowded=("stats",))
    original_capture = renderer.capture

    def capture(built, use="talk"):
        snapshot = original_capture(built, use)
        if renderer.captures > 1:  # the redrawn deck: the new layout has tiny type everywhere
            snapshot["slides"][1]["smallText"] = [{"text": "x", "size": 12, "minimum": 18}] * 20
        return snapshot

    renderer.capture = capture
    agents, built = make_with_look(FakeProvider(looking_router()), renderer)
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "stats"
    assert built.skill["look"]["rejected"][0]["why"] == "drew worse"


def test_at_most_the_budgeted_slides_are_changed(tmp_path, monkeypatch):
    from gunther.studio import look

    monkeypatch.setattr(look, "MAX_FIXED", 1)
    renderer = FakeRenderer(tmp_path, crowded=("stats", "chart", "table"))
    agents, built = make_with_look(FakeProvider(looking_router()), renderer)
    assert len(built.skill["look"]["targets"]) == 1
    layouts = [fmt.parse_slide(s).layout for s in fmt.split_slides(built.content)]
    assert layouts.count("points") <= 1 and len(layouts) == 3


def test_what_the_eye_finds_is_fixed_by_the_layout_it_names(tmp_path):
    renderer = FakeRenderer(tmp_path, crowded=())
    finding = {"slide": 2, "problem": "Two numbers drawn as a list", "severity": "high",
               "action": "layout", "layout": "points", "fix": "use points"}  # fmt: skip
    agents, built = make_with_look(FakeProvider(looking_router(findings=[finding])), renderer)
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "points"
    assert built.skill["look"]["findings"][0]["slide"] == 2


def test_with_no_model_that_sees_the_step_says_why_and_the_deck_is_unchanged(tmp_path):
    renderer = FakeRenderer(tmp_path)
    blind = model("deepseek-v4-pro")
    assert not blind.vision
    agents, built = make_with_look(FakeProvider(looking_router()), renderer, blind)
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "stats"
    reasons = [s["reason"] for s in agents.skipped if s["step"] == "look"]
    assert reasons and "can see" in reasons[0]


def test_when_the_slides_cannot_be_drawn_the_deck_is_still_saved(tmp_path):
    from gunther.slide_render import RenderError

    renderer = FakeRenderer(tmp_path, fail=RenderError("no browser"))
    agents, built = make_with_look(FakeProvider(looking_router()), renderer)
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "stats"
    reasons = [s["reason"] for s in agents.skipped if s["step"] == "look"]
    assert reasons and "no browser" in reasons[0]
    assert "renderCheck" not in built.skill


def test_without_a_renderer_the_step_is_skipped():
    agents, built = make_with_look(FakeProvider(looking_router()), None)
    assert [s for s in agents.skipped if s["step"] == "look"]
    assert built.content.startswith("<!-- layout: title")


def test_the_title_slide_borrows_the_citations_other_slides_give_for_its_figures():
    from gunther.studio.pipeline import _lend_refs

    rest = (
        "## Weather adjustment halves the saving\n- 9% weather-adjusted saving [3][4]\n"
        "- 2.1 °C cooler [2]\n- Albedo fell [7]"
    )
    assert _lend_refs("# Cool roofs: 2.1 °C comfort, 9% not 18%", rest).endswith("[3] [4] [2]")
    assert _lend_refs("Comfort is real [1]", rest) == "Comfort is real [1]"
    assert _lend_refs("A 40% gain", rest) == "A 40% gain"


def test_the_subtitle_is_rewritten_to_state_what_the_slide_titles_say():
    from types import SimpleNamespace

    from gunther.studio.pipeline import Draft, StudioAgents

    deck = [
        "<!-- layout: title; design: v2; theme: paper -->\n# Cool roofs\nA vague promise\n"
        "Committee · March 2026",
        "<!-- layout: points -->\n## The saving is 9%, not 18%\n- 9% after adjustment [2]",
    ]
    agent = SimpleNamespace(
        _text=lambda *a, **k: "Cool roofs save 9% [2], half the plan's figure",
        _claims=lambda model, text, by_ref, rest="", **kwargs: ([], {}),
    )
    pool = SimpleNamespace(by_ref=lambda: {2: object()})
    out = StudioAgents._cover(agent, None, [Draft(t, [], {}) for t in deck], pool, "material")
    assert "Cool roofs save 9% [2], half the plan's figure\nCommittee" in out[0].text
    assert out[1].text == deck[1]


def test_a_figure_with_no_source_brings_the_passages_that_state_it_to_the_fix():
    from types import SimpleNamespace

    from gunther.outputs import Issue
    from gunther.studio.pipeline import _stating

    by_ref = {
        1: SimpleNamespace(context="水泥产量下降9.5%，房地产开发投资下降10.6%", text=""),
        2: SimpleNamespace(context="GDP grew 5% in 2024", text=""),
    }
    flagged = [Issue("| 水泥产量 | -9.5% |", "unsourced", ""), Issue("Up 5%", "unsourced", "")]
    assert _stating(flagged, by_ref) == {1}
