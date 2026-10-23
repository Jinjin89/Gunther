import json
import re
from pathlib import Path

import pytest
from fake_models import FakeProvider, gateway, model
from test_studio_pipeline import DECK, pool, router

from gunther.outputs import Notes, Spec, _marked
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio.design import PLACEHOLDER
from gunther.studio.pipeline import StudioAgents

ROOT = Path(__file__).resolve().parents[3]
STYLES = Path(D.STYLES_DIR)
DECK_STYLES = ROOT / "apps" / "desktop" / "src" / "outputs" / "deck" / "styles"

STATS = (
    "<!-- layout: stats -->\n## The trial was large enough to trust the direction\n"
    "- **412** buildings in six districts [1]\n- **3** summers, 2021–2023 [1]\n"
    "Note: Size first. 99 words."
)
CHART = (
    "<!-- layout: chart; chart: column -->\n## Weather adjustment halves the saving\n"
    "| Measure | Saving (%) |\n| --- | --- |\n| Raw | 18 [2] |\n| **Adjusted** | 9 [2] |\n"
    "Plan with the adjusted figure [3]\nNote: The bars."
)
GOOD_STATS = (
    '<section class="slide"><h2>The trial was large enough to trust the direction</h2>'
    "<p><span>412</span> buildings in six districts [1]</p>"
    "<p>3 summers, 2021–2023 [1]</p></section>"
)
GOOD_CHART = (
    '<section class="slide"><h2>Weather adjustment halves the saving</h2>'
    "<gunther-chart></gunther-chart><p>Plan with the adjusted figure [3]</p></section>"
)


def build(route=router, **options):
    fake = FakeProvider(route)
    agents = StudioAgents(gateway(fake), use="talk", **options)
    built = agents.make(
        model(), "high", Spec("slides", "collaborator", brief="A 10-minute talk"), pool(), Notes()
    )
    return agents, built, fake


# The deck ---------------------------------------------------------------------------------


def test_a_deck_records_its_style_and_every_slide_carries_a_design_block():
    agents, built, fake = build()
    slides = fmt.split_slides(built.content)
    assert len(slides) == 3
    assert fmt.parse_slide(slides[0]).attrs["style"] == "light"
    assert fmt.parse_slide(slides[0]).attrs["design"] == "v2"
    for slide in slides:
        html = fmt.design_of(slide)
        assert html and html.startswith('<section class="slide') and html.endswith("</section>")
    assert any(e["step"] == "design" and e["state"] == "done" for e in agents.trail)
    detail = next(e["detail"] for e in agents.trail if e["step"] == "design")
    assert detail == "3 slides designed"
    designs = [r for r in fake.requests if "world-class" in str(r["messages"][0]["content"])]
    assert len(designs) == 3 and all(r.get("max_tokens", 16000) >= 16000 for r in designs)


def test_the_checks_and_parsers_ignore_the_design_block():
    _, built, _ = build()
    _, plain, _ = build(design=False)
    assert "style" not in fmt.parse_slide(fmt.split_slides(plain.content)[0]).attrs
    slides = fmt.split_slides(built.content)
    for slide in slides:
        bare = fmt.without_design(slide)
        assert ":::design" not in bare and "<section" not in bare
        assert fmt.parse_slide(slide) == fmt.parse_slide(bare)
        marked = _marked(slide)
        assert "<section" not in marked.body and marked.design == fmt.design_of(slide)
    assert fmt.check_deck(built.content) == fmt.check_deck(
        "\n\n---\n\n".join(fmt.without_design(s) for s in slides)
    )
    assert fmt.uncited_lines(fmt.parse_slide(slides[1]).body) == []
    # The notes do not swallow the design.
    assert "section" not in fmt.parse_slide(slides[2]).notes


def test_citations_inside_a_design_follow_the_text_renumbering():
    _, built, _ = build()
    slides = fmt.split_slides(built.content)
    # The title slide cites source 2 first, so it becomes [1]; source 1 becomes [2].
    for slide in slides:
        text, html = fmt.without_design(slide), fmt.design_of(slide)
        assert set(re.findall(r"\[(\d+)\]", html)) == set(re.findall(r"\[(\d+)\]", text))
    assert "[1]" in fmt.design_of(slides[0]) and "[2]" not in fmt.design_of(slides[0])
    assert "[2]" in fmt.design_of(slides[1])


def test_a_slide_that_fails_the_guard_twice_is_drawn_plainly_and_noted():
    def route(request):
        system = str(request["messages"][0]["content"])
        if "world-class" in system and 'Its content (layout hint "stats")' in system:
            return '<section class="slide"><h2>Big trial</h2><p>412 buildings</p></section>'
        return router(request)

    agents, built, fake = build(route)
    slides = fmt.split_slides(built.content)
    assert fmt.design_of(slides[1]) is None
    assert fmt.design_of(slides[0]) and fmt.design_of(slides[2])
    note = next(s for s in agents.skipped if s["step"] == "design")
    assert note["label"] == "Slide 2 drawn plainly" and "missing" in note["reason"]
    again = [r for r in fake.requests if "Your earlier design broke" in json.dumps(r)]
    assert len(again) == 1  # asked once more, with the findings
    assert next(e["detail"] for e in agents.trail if e["step"] == "design") == (
        "2 slides designed, 1 drawn plainly"
    )


def test_a_slide_fixed_on_the_second_try_keeps_the_better_design():
    seen = {"stats": 0}

    def route(request):
        system = str(request["messages"][0]["content"])
        if "world-class" in system and 'Its content (layout hint "stats")' in system:
            seen["stats"] += 1
            if seen["stats"] == 1:
                return '<section class="slide"><h2>x</h2></section>'
        return router(request)

    agents, built, _ = build(route)
    assert seen["stats"] == 2 and fmt.design_of(fmt.split_slides(built.content)[1])
    assert not [s for s in agents.skipped if s["step"] == "design"]


def test_a_failure_in_designing_never_fails_the_build():
    def route(request):
        if "world-class" in str(request["messages"][0]["content"]):
            raise RuntimeError("the designer fell over")
        return router(request)

    agents, built, _ = build(route)
    assert all(fmt.design_of(s) is None for s in fmt.split_slides(built.content))
    assert any(s["step"] == "design" for s in agents.skipped)


def test_an_invalid_style_is_light_and_a_good_pick_is_kept():
    def pick(style):
        def route(request):
            if "Choose the visual style" in str(request["messages"][0]["content"]):
                return json.dumps({"style": style})
            return router(request)

        agents = StudioAgents(gateway(FakeProvider(route)))
        return D.pick_style(agents, model(), title="T", subtitle="", brief="", audience="x")

    assert pick("Dark") == "dark" and pick("bold") == "bold"
    assert pick("neon") == "light"


# The fact guard ---------------------------------------------------------------------------


def test_a_faithful_design_passes_the_guard():
    assert D.check_design(STATS, GOOD_STATS) == []
    assert D.check_design(CHART, GOOD_CHART) == []  # the chart draws its own rows
    # Badges 1–9 and a split big number are fine; so is the SVG namespace.
    ok = GOOD_STATS.replace(
        "<p>3 summers", '<svg xmlns="http://www.w3.org/2000/svg"></svg><b>2</b><p>3 summers'
    )
    assert D.check_design(STATS, ok) == []


@pytest.mark.parametrize(
    ("html", "finding"),
    [
        (GOOD_STATS.replace("412", "400"), "missing: 412"),
        (
            GOOD_STATS.replace("[1]</p></section>", "[1]</p><p>up 77%</p></section>"),
            "not in the content (remove them): 77",
        ),
        (GOOD_STATS.replace(" [1]", ""), "citations are missing: [1]"),
        (GOOD_STATS.replace("2023 [1]", "2023 [1][2]"), "citations are not in the content: [2]"),
        (GOOD_STATS.replace("trust the direction", "trust the result"), "title"),
        (GOOD_STATS.replace("<h2>", "<script>x</script><h2>"), "<script>"),
        (GOOD_STATS.replace("<p>3", '<p onclick="x()">3'), "event attribute"),
        (GOOD_STATS.replace("<p>3", '<a href="http://x.example">3</a><p>3'), "web address"),
        (GOOD_STATS.replace("<p>3", '<img src="#a"><p>3'), "forbidden element"),
        (GOOD_STATS.replace("<p>3", '<a href="javascript:alert(1)">3</a><p>3'), "javascript"),
        ("<div>412 buildings in six districts [1] 3 summers 2021 2023</div>", "<section"),
    ],
)
def test_the_guard_catches_what_a_design_must_not_do(html, finding):
    problems = " | ".join(D.check_design(STATS, html))
    assert finding in problems, problems


def test_the_guard_checks_the_chart_placeholder_and_its_citations():
    none = GOOD_CHART.replace("<gunther-chart></gunther-chart>", "")
    assert "exactly one" in " ".join(D.check_design(CHART, none))
    twice = GOOD_CHART.replace("<p>", "<gunther-chart></gunther-chart><p>")
    assert "exactly one" in " ".join(D.check_design(CHART, twice))
    assert "no chart table" in " ".join(
        D.check_design(STATS, GOOD_STATS.replace("</section>", PLACEHOLDER + "</section>"))
    )
    # The chart's own row citation [2] and values need no copy; the takeaway's [3] does.
    lost = GOOD_CHART.replace(" [3]", "")
    assert "[3]" in " ".join(D.check_design(CHART, lost))
    # A value that is also stated outside the table must still be on the slide.
    table_and_text = CHART.replace("Plan with", "Raw is 18 and plan with")
    assert "missing: 18" in " ".join(D.check_design(table_and_text, GOOD_CHART))


def test_a_design_keeps_the_questionable_mark():
    slide = STATS.replace("buildings in six districts [1]", "buildings in six districts [?]")
    kept = GOOD_STATS.replace("districts [1]", "districts [?]")
    assert D.check_design(slide, kept) == []
    assert "[?]" in " ".join(D.check_design(slide, kept.replace(" [?]", "")))


def test_a_reply_becomes_one_safe_section():
    reply = 'Here:\n```html\n<section class="slide">\n<p>a</p>\n---\n:::\n<p>b</p>\n</section>\n```'
    section = D.extract_section(reply)
    assert section.startswith("<section") and section.endswith("</section>")
    assert "---" not in section and ":::" not in section and "```" not in section
    assert D.extract_section("no markup") == ""
    slide = fmt.with_design(STATS, section)
    assert len(fmt.split_slides(slide)) == 1 and fmt.design_of(slide) == section


def test_the_design_block_is_added_replaced_and_removed():
    once = fmt.with_design(STATS, GOOD_STATS)
    twice = fmt.with_design(once, '<section class="slide"></section>')
    assert once.endswith("\n:::") and ":::design\n" in once
    assert (
        twice.count(":::design") == 1
        and fmt.design_of(twice) == '<section class="slide"></section>'
    )
    assert fmt.without_design(once) == STATS
    assert fmt.design_of(STATS) is None and fmt.without_design(STATS) == STATS


def test_a_slide_with_a_picture_is_left_to_the_template():
    figure = "<!-- layout: figure -->\n## A figure\n![Chart](gunther-asset:abc123)\nCaption [1]"
    assert not D.designable(figure) and D.designable(STATS)


# The styles -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", fmt.STYLES)
def test_a_style_guide_carries_the_same_css_as_the_app(name):
    guide = (STYLES / f"{name}.md").read_text(encoding="utf-8")
    block = re.search(r"```css\n(.*?)```", guide, re.S).group(1)
    assert block == (DECK_STYLES / f"{name}.css").read_text(encoding="utf-8")
    assert '"Inter"' in block and "PingFang SC" in block and "Noto Sans CJK SC" in block
    assert D.load_style(name)[0] == guide


def test_the_base_css_is_the_same_in_the_app_and_the_backend():
    assert (STYLES / "base.css").read_text() == (DECK_STYLES / "base.css").read_text()


def test_the_styles_are_shipped_with_the_backend():
    script = (ROOT / "scripts" / "build-backend-sidecar.mjs").read_text()
    assert '"studio", "styles")}:gunther/studio/styles' in script


def test_the_deck_story_asks_for_parts_in_long_decks():
    from gunther.studio import prompts as P

    assert '"section" slide' in P.DECK_STORY and "8 or more slides" in P.DECK_STORY
    section = "<!-- layout: section -->\n# Cooling demand is rising faster than supply"
    assert fmt.check_slide(fmt.parse_slide(section), first=False) == []
    assert DECK  # the shared fixture still loads


def test_a_plain_slide_the_look_rewrites_stays_on_the_template_path_and_designs_stay(tmp_path):
    from test_studio_pipeline import FakeRenderer, looking_router

    looks = looking_router()

    def route(request):
        system = str(request["messages"][0]["content"])
        if "world-class" in system and 'Its content (layout hint "stats")' in system:
            return "<section>no</section>"  # slide 2 fails the guard twice: it is drawn plainly
        return looks(request)

    agents = StudioAgents(gateway(FakeProvider(route)), use="talk", renderer=FakeRenderer(tmp_path))
    built = agents.make(
        model(), "high", Spec("slides", "collaborator", brief="A 10-minute talk"), pool(), Notes()
    )
    slides = fmt.split_slides(built.content)
    assert built.skill["look"]["status"] == "fixed" and built.skill["look"]["designed"] == []
    assert fmt.parse_slide(slides[1]).layout == "points" and fmt.design_of(slides[1]) is None
    assert fmt.design_of(slides[0]) and fmt.design_of(slides[2])
    assert fmt.parse_slide(slides[0]).attrs["style"] == "light"
