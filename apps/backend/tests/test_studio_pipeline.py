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


def router(request):
    system = str(request["messages"][0]["content"])
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
        return DECK
    if "core message" in system:
        return re.search(r"^Subtitle: (.*)$", system, re.M).group(1)
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
        model(),
        "high",
        Spec("slides", "collaborator", brief="A 10-minute talk"),
        pool(),
        Notes(),
        events.append,
    )
    slides = fmt.split_slides(built.content)
    assert slides[0].startswith("<!-- layout: title; design: v2; theme: paper; style: light -->")
    # 7% is not in passage 2, so the chart is drawn as a table.
    assert fmt.parse_slide(slides[2]).layout == "table"
    assert any(item["step"] == "verify" for item in agents.skipped)
    # Citations are renumbered in reading order: 2, 1 → 1, 2.
    assert "[1]" in slides[0] and len(built.citations) == 2
    assert built.approach["method"] == "studio"
    assert {e["id"] for e in events if e.get("type") == "stage"} >= {"memo", "story", "write"}
    # Few calls: memo, storyline, critic, draft, then checks.
    asked = [
        str(r["messages"][0]["content"])[:40]
        for r in fake.requests
        if "designer" not in str(r["messages"][0]["content"])
        and "Choose the visual" not in str(r["messages"][0]["content"])
    ]
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
        workspace, _ = kept(client, "talk")
        work = workspace.works[0]
        assert is_studio(work) and len(work.outline) == 3
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
        system = str(request["messages"][0]["content"])
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
        _claims=lambda model, text, by_ref, rest="": ([], {}),
        _chart_issues=lambda text, by_ref: [],
    )
    pool = SimpleNamespace(by_ref=lambda: {2: object()})
    out = StudioAgents._cover(agent, None, [Draft(t, [], {}) for t in deck], pool)
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
