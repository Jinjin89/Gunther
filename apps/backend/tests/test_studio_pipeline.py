import json
import re
from dataclasses import replace

import pytest
from fake_models import FakeProvider, gateway, model

from gunther.answer_runs import Stopped
from gunther.citations import citation_numbers
from gunther.evidence import Evidence, Tool
from gunther.llm import ModelError
from gunther.output_shapes import lend_refs
from gunther.output_types import output_type_for
from gunther.outputs import FirstWrite, Issue, Notes, Pool, Spec, split_document
from gunther.schemas import ConversationCitationOut, OutputScope, PlanOptionsOut
from gunther.studio import format as fmt
from gunther.studio.pipeline import StudioAgents
from gunther.studio.state import Run

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
REPORT_SUMMARY = "## The trial has an outcome\nThe trial establishes the outcome [1]."


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
        return json.dumps({
            "title": "Findings", "summary_heading": "The trial has an outcome",
            "summary_job": "Lead with the outcome", "summary_refs": [1],
            "sections": [{"heading": "Results", "refs": [1]}],
        })
    if "Write the report" in system:
        marker = "Use exactly these approved section headings in order."
        rows, _ = json.JSONDecoder().raw_decode(system.split(marker, 1)[1].lstrip())
        return "# Findings\n\n" + "\n\n".join(
            f"## {row['heading']}\nThe trial establishes the outcome [1]." for row in rows
        )
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
        slides = fmt.split_slides(deck)
        return json.dumps(
            {
                "title": fmt.parse_slide(slides[0]).title,
                "subtitle": "s",
                "slides": [
                    {
                        "title": fmt.parse_slide(slide).title,
                        "layout": "title" if i == 0 else None,
                        "refs": list(dict.fromkeys(citation_numbers(slide, bare_only=True))),
                    }
                    for i, slide in enumerate(slides)
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


def build_studio(
    agents, writer, effort, spec, material, notes, events=None, *, resume=None, checkpoint=None,
):
    """Exercise the same stages as the creation route with an explicit test approval."""

    def discard(_event):
        pass

    emit = events or discard
    keep = checkpoint or discard
    options = PlanOptionsOut(
        type=output_type_for(spec.kind, spec.output_type).id, scope=OutputScope(),
        use=agents.use if spec.kind == "slides" else None, language=spec.language,
    )
    prepared = agents.prepare(
        writer, effort, spec, material, notes, manifest={}, resume=resume, emit=emit,
        checkpoint=keep,
    )
    plan = agents.propose(
        prepared, model=writer, effort=effort, options=options, previous=None, feedback="",
        fixed_rows=(), order_fixed=False, emit=emit, checkpoint=keep,
    )
    permit = FirstWrite("w", "lib", "conv", "plan", "p1", "approve", "boot", "attempt")
    return agents.generate(
        prepared, model=writer, effort=effort, options=options, plan=plan, revision=permit.revision,
        authority=permit, emit=emit, checkpoint=keep,
    )


def test_a_deck_is_made_in_the_new_format_and_an_unstated_chart_value_becomes_a_table():
    fake = FakeProvider(lambda request: router(request, UNSUPPORTED_DECK))
    agents = StudioAgents(gateway(fake), use="talk")
    events = []
    built = build_studio(
        agents,
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
            f"# Findings\n\n{REPORT_SUMMARY}\n\n"
            "## Results\n\n:::exhibit chart=column\nCooling savings by estimate\n"
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
    with app_for(tmp_path, FakeProvider(reply)) as client:
        base, _ = library(client, notes={"Field trial": evidence})
        version = built(build(client, base, body(kind=kind, use="talk")))

    assert row in version["content"]
    drawn = "chart" if verdict == "supported" else "table"
    if kind == "slides":
        assert fmt.parse_slide(fmt.split_slides(version["content"])[2]).layout == drawn
        assert ("<gunther-chart>" in version["content"]) == (drawn == "chart")
        if verdict != "uncited":
            claims = version["sections"][2]["coverage"]["claims"]
            rows = {tuple(claim["tableRow"]): claim for claim in claims if "tableRow" in claim}
            assert rows[(0, 2)]["verdict"] == "supported" and rows[(0, 2)]["refs"] == [1]
            assert rows[(0, 3)]["verdict"] == verdict
            assert rows[(0, 3)]["refs"] == [1]
    else:
        assert (":::exhibit chart=column" in version["content"]) == (drawn == "chart")
    assert seen and all("Table header | Measure | Saving (%) | Coverage (%) |" in s for s in seen)
    if drawn == "table":
        skipped = version["provenance"]["skill"]["skipped"]
        assert any(item["label"].endswith("drawn as a table") for item in skipped)


def test_direct_build_records_optional_failures_and_keeps_the_checked_draft(tmp_path):
    from test_outputs import app_for, body, build, built, library, route, workspace

    def reply(request):
        task = request_text(request)
        if "A fact-checker compared" in task:
            raise ModelError("Claim repair disconnected.")
        if "A deck's subtitle is its core message" in task:
            raise ModelError("Subtitle alignment disconnected.")
        return router(request, UNSUPPORTED_DECK.replace("[2]", "[1]"))

    with app_for(tmp_path, FakeProvider(reply)) as client:
        base, _ = library(client, notes={"Field trial": " ".join(PASSAGES)})
        version = built(build(client, base, body(kind="slides", use="talk")))
        reopened = client.get(route(base, version["id"]), headers=workspace(client)).json()

    first, _, last = fmt.split_slides(reopened["content"])
    assert "The 18% saving is 9% after weather adjustment [1]" in first
    assert fmt.parse_slide(last).layout == "table" and "| **Adjusted** | 7 [1] |" in last
    assert any(issue["verdict"] == "unsupported" for issue in reopened["sections"][2]["issues"])
    status = {item["label"]: item["reason"] for item in reopened["provenance"]["skill"]["skipped"]}
    assert status["Claim repair unavailable"] == "Claim repair disconnected."
    assert status["Subtitle alignment unavailable"] == "Subtitle alignment disconnected."


@pytest.mark.parametrize("recovers", [True, False])
def test_direct_build_reports_format_failures_after_both_repair_rounds(tmp_path, recovers):
    from test_outputs import app_for, body, build, built, library, route, workspace

    complete = DECK.replace("[2]", "[1]")
    written = re.sub(r"^Note: .*\n?", "", complete, flags=re.M)
    repaired = fmt.split_slides(complete)
    attempts = {2: 0, 3: 0}

    def reply(request):
        task = request_text(request)
        target = re.search(r"^Slide (\d+) as written:$", task, re.M)
        if target:
            slide = int(target[1])
            attempts[slide] += 1
            if slide == 2 and (attempts[slide] == 1 or not recovers):
                raise ModelError(f"Format repair disconnected on attempt {attempts[slide]}.")
            return repaired[slide - 1]
        return router(request, written)

    with app_for(tmp_path, FakeProvider(reply)) as client:
        base, _ = library(client, notes={"Field trial": " ".join(PASSAGES)})
        version = built(build(client, base, body(kind="slides", use="talk")))
        reopened = client.get(route(base, version["id"]), headers=workspace(client)).json()

    assert attempts == {2: 2, 3: 1}
    left = fmt.check_deck(reopened["content"])
    skipped = [
        item for item in reopened["provenance"]["skill"]["skipped"] if item["step"] == "format"
    ]
    if recovers:
        assert left == [] and skipped == []
    else:
        assert [slide for slide, _ in left] == [2]
        [failure] = skipped
        assert failure["label"] == "Slide 2 format repair unavailable"
        assert failure["reason"] == f"Format repair disconnected on attempt 2. {left[0][1][0]}"


def test_styled_deck_reopens_with_the_cover_format_repair_error(tmp_path):
    from test_outputs import app_for, body, build, built, library, route, workspace

    written = DECK.replace("# Cool roofs work, but at half the headline saving", "# Roofs")
    written = written.replace("[2]", "[1]")
    attempts = 0

    def reply(request):
        nonlocal attempts
        if "Slide 1 as written:" in request_text(request):
            attempts += 1
            raise ModelError(f"Cover repair disconnected on attempt {attempts}.")
        return router(request, written)

    with app_for(tmp_path, FakeProvider(reply)) as client:
        base, _ = library(client, notes={"Field trial": " ".join(PASSAGES)})
        version = built(build(client, base, body(kind="slides", use="talk")))
        response = client.get(route(base, version["id"]), headers=workspace(client))
        assert response.status_code == 200
        reopened = response.json()

    assert attempts == 2
    cover = fmt.parse_slide(fmt.split_slides(reopened["content"])[0])
    assert cover.attrs["style"] == "light"
    assert fmt.check_deck(reopened["content"]) == [(1, ["title is missing or too short."])]
    [failure] = [
        item for item in reopened["provenance"]["skill"]["skipped"] if item["step"] == "format"
    ]
    assert failure["label"] == "Slide 1 format repair unavailable"
    assert failure["reason"] == (
        "Cover repair disconnected on attempt 2. title is missing or too short."
    )


@pytest.mark.parametrize("attribution", ["参照資料：", "来源："])
def test_direct_report_keeps_the_supported_exhibit_when_its_rows_are_repeated(
    tmp_path, attribution,
):
    from test_outputs import app_for, body, build, built, library

    exhibit = (
        ":::exhibit chart=column\nSavings by estimate\n| Measure | Saving (%) |\n"
        f"| --- | --- |\n| Raw | 18 |\n| Adjusted | 9 [1] |\n{attribution}Trial [1]\n:::"
    )
    section = "## Results\n\n" + exhibit + "\n\n" + exhibit.replace("(%)", "(points)")

    def unsupported(request):
        prompt = request["messages"][-1]["content"]
        return [int(n) for n in re.findall(r"^\((\d+)\) \| Raw .+Saving \(points\)", prompt, re.M)]

    def reply(request):
        if "Write the report" in request_text(request):
            return f"# Findings\n\n{REPORT_SUMMARY}\n\n{section}"
        if "checking step" in request_text(request):
            flagged = unsupported(request)
            return json.dumps({"sentences": [
                {"n": int(n), "verdict": "not" if int(n) in flagged else "supported"}
                for n in re.findall(r"^\((\d+)\) ", request_text(request), re.M)
            ]})
        return router(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Trial": "The raw saving was 18%, adjusted saving 9%."})
        version = built(build(client, base, body()))

    exhibits = list(fmt.EXHIBIT.finditer(version["content"]))
    assert [block["attrs"].strip() for block in exhibits] == ["chart=column", ""]
    assert all("| Raw | 18 [1] |" in block["body"] for block in exhibits)


def test_report_verification_repairs_prose_tables_and_preserves_code(monkeypatch):
    agents = StudioAgents(gateway(FakeProvider("unused")))
    monkeypatch.setattr(agents, "_claims", lambda *args, **kwargs: {})
    code = "```text\n| name | arr[7] |\n| value | x[3] |\n```"
    inline = "Example `lines\n| name | literal |\n| value | literal |`."
    prefix = f"## Results\n\n{code}\n\n{inline}\n\n"
    source = prefix + "| Name | Status |\n| Trial | Done |"
    run = Run(Spec("report", ""), "")
    [draft] = agents.verify(agents.gateway.models[0], run, [source], Pool(10), lambda _: None)
    assert draft.text == prefix + "| Name | Status |\n| --- | --- |\n| Trial | Done |"


@pytest.mark.parametrize("attribution", ["参照資料：", "来源："])
def test_only_structured_exhibit_source_metadata_is_exempt_from_numeric_findings(
    monkeypatch, attribution,
):
    agents = StudioAgents(gateway(FakeProvider("unused")))
    monkeypatch.setattr("gunther.studio.verification.check_part", lambda *args, **kwargs: {})
    evidence = {1: Evidence("library", "Thermal survey 2024", "The survey has three locations.")}
    text = (
        "## Findings\nSource: The average was 90%.\n\n"
        ":::exhibit\nResults by district\n| District | Finding |\n| --- | --- |\n"
        f"| Central | Lower use |\n{attribution}Thermal survey 2024\n:::\n\n"
        ":::exhibit\nOther results\n| District | Finding |\n| --- | --- |\n"
        "| North | Similar use |\nSource: The average was 80%.\n:::"
    )
    checked = agents._claims(model(), text, evidence)
    assert {row["issue"]["claim"] for row in checked.values()} == {
        "Source: The average was 90%.", "Source: The average was 80%.",
    }


@pytest.mark.parametrize(("heading", "needs_format_repair"), [
    ("Costs", True), ("熱損失が止まる", False),
])
def test_a_direct_deck_rebuild_keeps_approved_headings_through_repairs(
    tmp_path, heading, needs_format_repair
):
    from test_outputs import app_for, body, build, built, library

    base_slides = fmt.split_slides(DECK)
    draft = UNSUPPORTED_DECK.replace(
        "## The trial was large enough to trust the direction", f"## {heading}"
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
        base, _ = library(client, notes={f"Source {n}": text for n, text in enumerate(PASSAGES, 1)})
        first = built(build(client, base, body(kind="slides", use="talk")))
        approved = first["outline"]
        approved[1]["heading"] = heading
        approved[2]["layout"] = None
        fake.replies[:] = [repair]
        rebuilt = built(build(client, base, body(
            kind="slides", use="talk", outline=approved, supersedesArtifactId=first["id"],
        )))
        slides = [fmt.parse_slide(text) for text in fmt.split_slides(rebuilt["content"])]
        assert attempted == ({"format", "claim"} if needs_format_repair else {"claim"})
        assert [slide.title for slide in slides] == [row["heading"] for row in approved]
        assert slides[2].layout == "table"


def test_report_repair_cannot_add_a_section_or_displace_the_existing_work(monkeypatch):
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.checks import finding, verdicts_of
    from gunther.llm import ModelError
    from gunther.studio.work import load_built

    agents = StudioAgents(gateway(FakeProvider(router)))
    monkeypatch.setattr(
        agents, "_claims", lambda *a, **k: verdicts_of(finding(Issue("Trial", "unsupported").out()))
    )
    monkeypatch.setattr(
        agents, "_fix_claims", lambda *a, **k: ["## Results\nA repair.\n\n## Added\nExtra."]
    )
    made = pool()
    built = build_studio(agents, model(), "high", Spec("report", "scientist"), made, Notes())
    assert len(split_document(built.content, "report")[1]) == 2
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
        build_studio(agents, model(), "low", Spec("slides", "Everyone", brief="Two pages"),
                     pool(), Notes())


@pytest.mark.parametrize("drops_part", [False, True], ids=["keeps-limitation", "wrong-count"])
def test_storyline_repair_keeps_the_requested_count_and_evidence_context(drops_part):
    slides = [
        {"title": "The trial measured time", "layout": "title"},
        {"title": "Quality was not measured", "layout": "points", "refs": [2]},
        {"title": "The trial measured time", "layout": "points", "refs": [1]},
    ]
    changes = []

    def reply(request):
        task = request_text(request)
        if "presentation strategist" in task:
            if "Your storyline:" not in task:
                return json.dumps({"title": "Pilot", "requested_count": 3,
                                   "requested_minutes": 8, "slides": slides})
            assert "Quality was not measured" in task and "Repeats the cover" in task
            changes.append(task)
            amended = [*slides[:2], {**slides[2], "title": "The duration sets the scope"}]
            return json.dumps({"title": "Pilot", "requested_count": 2, "requested_minutes": 3,
                               "slides": amended[::2] if drops_part else amended})
        if "senior partner reviewing a storyline" in task:
            if changes:
                return '{"verdict":"ready"}'
            return json.dumps({"verdict": "revise", "problems": [
                {"where": "Slide 3", "problem": "Repeats the cover",
                 "fix": "Explain the handoff from the limit to the scope."},
            ]})
        raise AssertionError(task)

    agents = StudioAgents(gateway(FakeProvider(reply)))
    run = Run(Spec("slides", "Everyone"), "talk")
    evidence = "[1] The trial ran for three summers.\n[2] Quality was not measured."
    if drops_part:
        with pytest.raises(ModelError, match="outline has 2 slides. The brief asks for 3"):
            agents.storyline(model(), "low", run, evidence, lambda _event: None)
    else:
        story = agents.storyline(model(), "low", run, evidence, lambda _event: None)
        assert (story.requested_count, story.requested_minutes) == (3, 8)
        assert story.slides[1].title == "Quality was not measured" and story.slides[1].refs == [2]
        assert story.slides[2].title == "The duration sets the scope"
    assert len(changes) == 1


def test_planning_a_deck_waits_for_approval_and_keeps_its_target_minutes(tmp_path):
    from fake_models import orchestrator_replies
    from test_artifact_agent import conversations, done, versions_of
    from test_outputs import app_for, approve_output, library, output_turn, workspace

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
            "actions": [
                {"do": "understand"},
                {"do": "plan", "change": "whole"},
            ],
        },
    ], inner))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Size": PASSAGES[0], "Trial": PASSAGES[1]})
        client.post(conversations(base), headers=workspace(client), json={
            "id": "planned-deck",
        }).raise_for_status()
        planned = done(output_turn(client, base, "planned-deck", {
            "kind": "plan", "clientRequestId": "plan_timed_deck", "planId": "timed_deck",
            "setup": {"type": "slides", "focus": "3 slides for an 8-minute talk",
                      "scope": {"mode": "library"}},
        }))
        assert planned["outcome"] == "planned" and versions_of(client) == []
        assert planned["plan"]["proposal"]["targetMinutes"] == 8
        assert sum("lead analyst" in request_text(r) for r in fake.requests) == 1
        assert not any("presentation writer" in request_text(r) for r in fake.requests)
        result = done(approve_output(
            client, base, "planned-deck", "timed_deck", "approve_timed_deck", planned,
        ))
        assert result["outcome"] == "saved"
        content = result["artifact"]["content"]
        assert len(split_document(content, "slides")[1]) == 3
        assert len(versions_of(client)) == 1


def test_a_conversation_can_rebuild_a_saved_deck_and_rewrite_one_slide(tmp_path):
    from fake_models import orchestrator_replies
    from test_artifact_agent import done, kept, turn
    from test_outputs import app_for, library, plan_and_approve, route, workspace


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

    fake = FakeProvider(inner)
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client, notes=notes)
        client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations",
            headers=workspace(client),
            json={"id": "talk", "title": "Trial"},
        )
        result = done(plan_and_approve(client, base_id, {
            "type": "slides", "focus": "A 10-minute deck on the trial", "use": "talk",
        }, conversation_id="talk"))
        assert result["outcome"] == "saved", result
        content = result["artifact"]["content"]
        assert "design: v2" in content and "## The trial was large" in content
        held, _ = kept(client, "talk")
        work = held.works[0]
        assert work.approach["method"] == "studio" and len(work.outline) == 3
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
        assert any("presentation writer" in task for task in tasks)
        assert not any("presentation strategist" in task for task in tasks)
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


def test_a_failed_approved_build_retries_without_writing_an_extra_version(tmp_path):
    from test_artifact_agent import done, kept, versions_of
    from test_outputs import app_for, approve_output, library, send_plan

    fail_writer = True

    def inner(request):
        system = request_text(request)
        if fail_writer and "presentation writer" in system:
            raise ModelError("The connection broke")
        if "knowledge assertions" in system:
            return json.dumps({"assertions": []})
        return router(request)

    fake = FakeProvider(inner)
    with app_for(tmp_path, fake) as client:
        notes = {f"Source {n}": text for n, text in enumerate(PASSAGES, 1)}
        base_id, _ = library(client, notes=notes)
        plan_id, events = send_plan(client, base_id, "talk", "trial_deck", {
            "type": "slides", "focus": "A 10-minute deck on the trial", "use": "talk",
            "scope": {"mode": "library"},
        })
        planned = done(events)
        failed = approve_output(client, base_id, "talk", plan_id, "approval_failed", planned)[-1]
        assert failed[0] == "error" and "The connection broke" in failed[1]["detail"]
        assert sum("presentation writer" in request_text(r) for r in fake.requests) == 1
        progress = kept(client, "talk")[0].works[0].studio_progress
        assert progress["memo"] and progress["storyline"] and versions_of(client) == []

        fail_writer = False
        before = len(fake.requests)
        resumed = done(approve_output(
            client, base_id, "talk", plan_id, "approval_retried", planned,
        ))
        assert resumed["outcome"] == "saved" and resumed["artifact"]["versionNumber"] == 1
        tasks = [request_text(request) for request in fake.requests[before:]]
        assert any("presentation writer" in task for task in tasks)
        assert not any(
            "lead analyst" in task or "presentation strategist" in task for task in tasks
        )


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
    with pytest.raises(ModelError, match="The connection broke"):
        build_studio(agents, model(), "high", spec, pool(), Notes(), checkpoint=saved.update)
    assert saved["memo"] and saved["storyline"]
    assert all(saved.get(key) for key in ("material_key", "prepare_key", "story_key", "write_key"))
    assert "request_key" not in saved
    before = len(fake.requests)
    built = build_studio(
        StudioAgents(gateway(fake), use="talk"), model(), "high", spec, pool(), Notes(),
        resume=dict(saved),
    )
    again = [request_text(r) for r in fake.requests[before:]]
    assert not any("lead analyst" in s or "presentation strategist" in s for s in again)
    assert "design: v2" in built.content


@pytest.mark.parametrize("changed", ["nothing", "brief", "citation_revision"])
def test_stopping_at_story_completion_keeps_only_a_matching_checkpoint(changed):
    fake = FakeProvider(router)
    spec = Spec("slides", "collaborator", brief="A 10-minute talk")
    evidence = pool()
    checkpoint = {}

    def stop(event):
        if (event.get("type"), event.get("id"), event.get("state")) == ("stage", "story", "done"):
            assert checkpoint["storyline"]
            raise Stopped

    with pytest.raises(Stopped):
        build_studio(
            StudioAgents(gateway(fake), use="talk"), model(), "high", spec, evidence, Notes(),
            stop, checkpoint=checkpoint.update,
        )
    assert not any("presentation writer" in request_text(r) for r in fake.requests)
    if changed == "brief":
        spec = replace(spec, brief="A revised 10-minute talk")
    elif changed == "citation_revision":
        first, *others = evidence.items
        citation = first.payload[0].model_copy(update={
            "source_revision_id": "republished", "block_id": "republished_block",
        })
        evidence = Pool(50)
        evidence.restore([replace(first, payload=(citation, None)), *others])

    before = len(fake.requests)
    build_studio(
        StudioAgents(gateway(fake), use="talk"), model(), "high", spec, evidence, Notes(),
        resume=checkpoint,
    )
    tasks = [request_text(r) for r in fake.requests[before:]]
    assert any("presentation writer" in task for task in tasks)
    assert any("lead analyst" in task for task in tasks) == (changed != "nothing")
    assert any("presentation strategist" in task for task in tasks) == (changed != "nothing")


def test_report_rebuild_fingerprints_style_intent_and_replays_legacy(tmp_path, monkeypatch):
    from test_outputs import app_for, body, build, built, library

    from gunther.models import Artifact
    from gunther.schemas import BuildOutputInput

    queries = []
    with app_for(tmp_path, FakeProvider(router)) as client:
        service = client.app.state.knowledge_service
        web = Tool("search_web", "Public web", lambda query: queries.append(query) or [], "web")
        monkeypatch.setattr(service, "_web_tool", lambda wanted: (web if wanted else None, None))
        base, _ = library(client)
        first = built(build(client, base, body(style="decision_brief")))
        summary, result = first["outline"]
        result = {**result, "goal": "Explain the result"}
        outline = [summary, result]
        request = body(supersedesArtifactId=first["id"], outline=outline, brief="Budget", web=True)
        inherited = built(build(client, base, request))
        assert inherited["style"] == "briefing" and queries == ["Budget"]
        assert [(row["heading"], row["id"], row["goal"]) for row in inherited["outline"]] == [
            (summary["heading"], summary["id"], summary["goal"]),
            ("Results", result["id"], "Explain the result"),
        ]
        assert built(build(client, base, request))["id"] == inherited["id"]
        assert build(client, base, dict(request, style="auto"))[-1][1]["status"] == 409

        reset = body(style="auto", supersedesArtifactId=inherited["id"], outline=outline)
        assert build(client, base, reset)[-1][1]["code"] == "plan_changed"
        explicit = body(
            style="decision_brief", supersedesArtifactId=inherited["id"], outline=outline,
        )
        assert built(build(client, base, explicit))["style"] == "briefing"
        omitted = {key: value for key, value in explicit.items() if key != "style"}
        assert build(client, base, omitted)[-1][1]["status"] == 409
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
    built = build_studio(
        agents, models[0] if models else model(), "high",
        Spec("slides", "collaborator", brief="A 10-minute talk"), pool(), Notes(),
    )  # fmt: skip
    return agents, built


def test_a_slide_the_renderer_measures_as_overflowing_is_redrawn_once(tmp_path):
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


@pytest.mark.parametrize(("before", "after"), [
    ("buildings in six districts", "properties across six districts"),
    ("Size first.", "Lead with the sample."),
])
def test_plain_layout_only_repair_rejects_changed_checked_content(tmp_path, before, after):
    renderer = FakeRenderer(tmp_path)
    finding = {"slide": 2, "problem": "Use a clearer layout", "severity": "high",
               "action": "layout", "layout": "points"}
    rewrite = POINTS.replace(before, after)
    assert rewrite != POINTS
    assert fmt.numbers_in(rewrite) == fmt.numbers_in(POINTS)
    assert citation_numbers(rewrite) == citation_numbers(POINTS)
    fake = FakeProvider(looking_router(rewrite=rewrite, findings=[finding]))
    _, built = make_with_look(fake, renderer)
    old = fmt.split_slides(built.content)[1]
    assert fmt.parse_slide(old).layout == "stats" and before in old and after not in old
    assert built.skill["look"]["rejected"] == [
        {"slide": 2, "why": "changed checked wording, notes or citations"},
    ]


def test_plain_content_finding_uses_the_editorial_repair_path(tmp_path):
    renderer = FakeRenderer(tmp_path)
    finding = {"slide": 2, "problem": "The phrase is too long", "severity": "high",
               "action": "cut", "fix": "Shorten the sample description"}
    rewrite = POINTS.replace("buildings in six districts", "buildings across districts")
    fake = FakeProvider(looking_router(rewrite=rewrite, findings=[finding]))
    _, built = make_with_look(fake, renderer)
    assert "buildings across districts" in built.content
    assert built.skill["look"]["applied"][0]["slide"] == 2


def test_with_no_model_that_sees_the_step_says_why_and_the_deck_is_unchanged(tmp_path):
    renderer = FakeRenderer(tmp_path)
    blind = model("deepseek-v4-pro")
    assert not blind.vision
    agents, built = make_with_look(FakeProvider(looking_router()), renderer, blind)
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "stats"
    reasons = [s["reason"] for s in agents.skipped if s["step"] == "look"]
    assert reasons and "can see" in reasons[0]


def test_partial_image_review_keeps_measurements_and_successful_findings(tmp_path, monkeypatch):
    from gunther.studio import look

    monkeypatch.setattr(look, "IMAGES_PER_CALL", 1)
    successful = looking_router(findings=[{"slide": 1, "problem": "Crowded cover"}])

    def reply(request):
        task = request_text(request)
        if "demanding presentation designer" in task and "Image 1 = slide 2" in task:
            raise ModelError("Image review disconnected.")
        return successful(request)

    renderer = FakeRenderer(tmp_path, crowded=())
    _, built = make_with_look(FakeProvider(reply), renderer)
    record = built.skill["look"]
    assert record["status"] == "measured" and record["reviewFailures"] == 1
    assert len(record["before"]["measured"]) == 3
    assert [finding["slide"] for finding in record["findings"]] == [1]
    assert built.skill["renderCheck"]["id"] == "snap1"
    assert any("1 of 3" in note["reason"] for note in built.skill["skipped"])
    assert fmt.parse_slide(fmt.split_slides(built.content)[1]).layout == "stats"


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
    rest = (
        "## Weather adjustment halves the saving\n- 9% weather-adjusted saving [3][4]\n"
        "- 2.1 °C cooler [2]\n- Albedo fell [7]"
    )
    assert lend_refs("# Cool roofs: 2.1 °C comfort, 9% not 18%", rest).endswith("[3] [4] [2]")
    assert lend_refs("Comfort is real [1]", rest) == "Comfort is real [1]"
    assert lend_refs("A 40% gain", rest) == "A 40% gain"


def test_citation_lending_uses_the_whole_documents_inline_markup():
    evidence = "7 participants [2]"
    assert lend_refs("Read `arr[7]`", evidence) == "Read `arr[7]`"
    rest = "Example `a\narr[2]` and 7 participants."
    assert lend_refs("7 participants", rest) == "7 participants"
    assert lend_refs("7 participants", "Example `a\narr[7]` [2].") == "7 participants"
    text = "7 participants use `a\nb[3]`.\n7 observations"
    assert lend_refs(text, evidence) == text + " [2]"


def test_the_subtitle_is_rewritten_to_state_what_the_slide_titles_say():
    from types import SimpleNamespace

    from gunther.outputs import Draft
    from gunther.studio.pipeline import StudioAgents

    deck = [
        "<!-- layout: title; design: v2; theme: paper -->\n# Cool roofs\nA vague promise\n"
        "Committee · March 2026",
        "<!-- layout: points -->\n## The saving is 9%, not 18%\n- 9% after adjustment [2]",
    ]
    agent = SimpleNamespace(
        _text=lambda *a, **k: "Cool roofs save 9% [2], half the plan's figure",
        _claims=lambda model, text, by_ref, rest="", **kwargs: {},
    )
    pool = SimpleNamespace(by_ref=lambda: {2: object()})
    out = StudioAgents._cover(agent, None, [Draft(t, {}) for t in deck], pool, "material")
    assert "Cool roofs save 9% [2], half the plan's figure\nCommittee" in out[0].text
    assert out[1].text == deck[1]


def test_a_figure_with_no_source_brings_the_passages_that_state_it_to_the_fix():
    from types import SimpleNamespace

    from gunther.outputs import Issue
    from gunther.studio.verification import _stating

    by_ref = {
        1: SimpleNamespace(context="水泥产量下降9.5%，房地产开发投资下降10.6%", text=""),
        2: SimpleNamespace(context="GDP grew 5% in 2024", text=""),
    }
    flagged = [Issue("| 水泥产量 | -9.5% |", "unsourced", ""), Issue("Up 5%", "unsourced", "")]
    assert _stating(flagged, by_ref) == {1}
