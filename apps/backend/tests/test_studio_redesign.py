"""Designed slides, phase P3: restyle a deck, design a slide again after an edit or a request
about its look, and the look step on designed slides. Fake models only."""

import contextlib
import json
import re

from fake_models import FakeProvider, gateway, model, orchestrator_replies
from test_studio_pipeline import PASSAGES, FakeRenderer, looking_router, pool, router

from gunther.outputs import Notes, Spec
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio.pipeline import StudioAgents

EXTRA = "Request: "


def dressed(request):
    """The fake designer, marking the style it was shown and any request about the look."""

    system = str(request["messages"][0]["content"])
    if "world-class presentation designer" in system:
        # The fake designer reads the content between its markers: take the request off first.
        plain = {"messages": [{"content": system.split("\n\nChange to make in this design")[0]
                               + "\n\nReply with the HTML only"}]}  # fmt: skip
        html = router(plain)
        style = re.search(r"# Style: (\w+)", system).group(1).lower()
        extra = re.search(r"Change to make in this design: (.*)", system)
        html = html.replace('class="slide', f'data-style="{style}" class="slide', 1)
        if extra:
            html = html.replace("</section>", f'<p class="look">{EXTRA}</p></section>')
            html = html.replace(EXTRA, "")  # the guard sees no new words
            html = html.replace('class="look"', f'class="look" data-asked="{extra.group(1)[:40]}"')
        return html
    return router(request)


def deck(route=dressed, **options):
    fake = FakeProvider(route)
    agents = StudioAgents(gateway(fake), use="talk", **options)
    built = agents.make(
        model(), "high", Spec("slides", "collaborator", brief="A 10-minute talk"), pool(), Notes()
    )
    return agents, built, fake


def words(slide: str):
    parsed = fmt.parse_slide(slide)
    return parsed.layout, parsed.title, parsed.body, parsed.notes, D.refs(fmt.without_design(slide))


# Restyle ----------------------------------------------------------------------------------


def test_a_restyle_keeps_every_word_number_citation_and_note_and_changes_style_and_designs():
    agents, built, _ = deck()
    slides = fmt.split_slides(built.content)
    assert fmt.parse_slide(slides[0]).attrs["style"] == "light"
    again = StudioAgents(gateway(FakeProvider(dressed)), use="talk")
    restyled, count, notes = D.restyle(again, model(), slides, "bold", workers=2)
    assert count == 3 and notes == []
    assert fmt.parse_slide(restyled[0]).attrs["style"] == "bold"
    for old, new in zip(slides, restyled, strict=True):
        assert words(old) == words(new)
        assert 'data-style="light"' in fmt.design_of(old)
        assert 'data-style="bold"' in fmt.design_of(new)
    assert [fmt.parse_slide(s).attrs.get("design") for s in restyled][0] == "v2"


def test_the_designer_sees_the_stored_slide_as_it_is_with_its_own_citation_numbers():
    _, built, _ = deck()
    slides = fmt.split_slides(built.content)
    fake = FakeProvider(dressed)
    D.restyle(StudioAgents(gateway(fake)), model(), slides, "dark", workers=1)
    asked = [str(r["messages"][0]["content"]) for r in fake.requests]
    stored = fmt.visible_content if hasattr(fmt, "visible_content") else D.visible_content
    assert all("# Style: Dark" in system for system in asked)
    for slide in slides:
        assert any(stored(slide) in system for system in asked)
    assert not any("<section" in system.split("Deck outline")[1] for system in asked)


def test_a_restyle_leaves_a_slide_that_fails_the_guard_plain():
    def route(request):
        system = str(request["messages"][0]["content"])
        if "world-class" in system and 'Its content (layout hint "stats")' in system:
            return '<section class="slide"><h2>Big trial</h2></section>'
        return dressed(request)

    _, built, _ = deck()
    slides = fmt.split_slides(built.content)
    assert fmt.design_of(slides[1])
    agents = StudioAgents(gateway(FakeProvider(route)))
    restyled, count, notes = D.restyle(agents, model(), slides, "dark", workers=2)
    assert count == 2 and fmt.design_of(restyled[1]) is None
    assert fmt.design_of(restyled[0]) and fmt.design_of(restyled[2])
    assert restyled[1] == fmt.without_design(slides[1])
    assert notes[0]["label"] == "Slide 2 drawn plainly"


def test_a_restyle_of_a_plain_deck_designs_it_and_an_unknown_style_is_refused():
    agents, built, _ = deck(design=False)
    slides = fmt.split_slides(built.content)
    assert all(fmt.design_of(s) is None for s in slides)
    restyled, count, _ = D.restyle(agents, model(), slides, "light", workers=2)
    assert count == 3 and fmt.parse_slide(restyled[0]).attrs["style"] == "light"
    try:
        D.restyle(agents, model(), slides, "neon", workers=1)
    except ValueError as error:
        assert "not a style" in str(error)
    else:
        raise AssertionError("an unknown style was accepted")


# The conversation: a studio deck, an edit, a request about the look ---------------------------


@contextlib.contextmanager
def studio_deck(tmp_path, inner):
    from test_artifact_agent import done, turn
    from test_outputs import app_for, library, workspace

    notes = {f"Source {n}": text for n, text in enumerate(PASSAGES, 1)}
    first = [
        {
            "actions": [
                {"do": "select", "artifact": "new:slides"},
                {"do": "make"},
                {"do": "save", "text": "Made the deck."},
            ]
        }
    ]
    fake = FakeProvider(orchestrator_replies(first, inner))
    with app_for(tmp_path, fake) as client:
        client.app.state.knowledge_service.output_method = "studio"
        base_id, _ = library(client, notes=notes)
        client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations",
            headers=workspace(client),
            json={"id": "talk", "title": "Trial"},
        )
        made = done(turn(client, base_id, "talk", "A 10-minute deck on the trial"))
        assert made["outcome"] == "saved", made

        def go(decisions, text):
            fake.replies[:] = [orchestrator_replies(decisions, inner)]
            before = len(fake.requests)
            result = done(turn(client, base_id, "talk", text))
            assert result["outcome"] == "saved", result
            designs = [
                str(r["messages"][0]["content"])
                for r in fake.requests[before:]
                if "world-class presentation designer" in str(r["messages"][0]["content"])
            ]
            return result["artifact"], designs

        yield client, base_id, made["artifact"], go


REWRITTEN = (
    "<!-- layout: stats -->\n## Three summers and 412 buildings back the result\n"
    "- **412** buildings in six districts [1]\n- **3** summers, 2021–2023 [1]\nNote: Size."
)


def studio_inner(request):
    system = str(request["messages"][0]["content"])
    if "working on one slide" in system:
        return REWRITTEN
    if "changing one passage" in system:
        return json.dumps({"text": "Size first, then trust."})
    if "knowledge assertions" in system:
        return json.dumps({"assertions": []})
    return dressed(request)


def test_an_edit_that_changes_a_slides_words_designs_that_slide_again_and_only_that_one(tmp_path):
    with studio_deck(tmp_path, studio_inner) as (client, base_id, first, go):
        slides = fmt.split_slides(first["content"])
        saved, designs = go(
            [
                {
                    "actions": [
                        {"do": "write", "parts": [2], "instruction": "Lead with the size"},
                        {"do": "save", "text": "Rewrote slide 2."},
                    ]
                }
            ],
            "Make slide 2 lead with the size",
        )
        after = fmt.split_slides(saved["content"])
        assert len(designs) == 1 and "Three summers and 412 buildings" in designs[0]
        assert "# Style: Light" in designs[0]  # the deck's own style
        assert "Three summers and 412 buildings" in fmt.design_of(after[1])
        assert fmt.design_of(after[0]) == fmt.design_of(slides[0])
        assert fmt.design_of(after[2]) == fmt.design_of(slides[2])
        assert fmt.parse_slide(after[0]).attrs["style"] == "light"


def test_an_edit_of_the_notes_alone_keeps_the_design(tmp_path):
    with studio_deck(tmp_path, studio_inner) as (client, base_id, first, go):
        slides = fmt.split_slides(first["content"])
        saved, designs = go(
            [
                {
                    "actions": [
                        {
                            "do": "edit",
                            "item": 2,
                            "passage": "Size first.",
                            "instruction": "Say more in the notes",
                        },
                        {"do": "save", "text": "Changed the notes."},
                    ]
                }
            ],
            "Say more in the notes of slide 2",
        )
        after = fmt.split_slides(saved["content"])
        assert "Size first, then trust." in after[1] and designs == []
        assert fmt.design_of(after[1]) == fmt.design_of(slides[1])


def test_a_request_about_the_look_designs_the_named_slides_and_leaves_their_words(tmp_path):
    with studio_deck(tmp_path, studio_inner) as (client, base_id, first, go):
        slides = fmt.split_slides(first["content"])
        saved, designs = go(
            [
                {
                    "actions": [
                        {"do": "redesign", "parts": [2, 3], "instruction": "make it a diagram"},
                        {"do": "save", "text": "Redrew slides 2 and 3."},
                    ]
                }
            ],
            "Make slides 2 and 3 more visual",
        )
        after = fmt.split_slides(saved["content"])
        assert len(designs) == 2 and all("make it a diagram" in d for d in designs)
        assert all("The slide's current design" in d and "<section" in d for d in designs)
        for n in (1, 2):
            assert words(after[n]) == words(slides[n])
            assert fmt.design_of(after[n]) != fmt.design_of(slides[n])
            assert 'data-asked="make it a diagram"' in fmt.design_of(after[n])
        assert after[0] == slides[0]
        # The words did not change, so the Checker's findings still stand.
        assert [s["checked"] for s in saved["sections"]] == [
            s["checked"] for s in first["sections"]
        ]


def test_a_request_about_the_look_is_refused_on_a_deck_with_no_style_and_on_a_report(tmp_path):
    from gunther.artifact_agent import Act, Gate, validate
    from gunther.artifact_workspace import PartRec

    with studio_deck(tmp_path, studio_inner) as (client, base_id, first, go):
        from test_artifact_agent import kept

        workspace, brief = kept(client, "talk")
        work = workspace.works[0]
        ids = [item.id for item in work.outline]
        gate = Gate(workspace, brief, ids, "message", False, "", 3, None)
        act = Act(do="redesign", parts=[2], instruction="a diagram")
        assert validate(act, gate) is None
        assert "Say what" in validate(Act(do="redesign", parts=[2]), gate)
        assert "There is no" in validate(Act(do="redesign", parts=[9], instruction="x"), gate)
        title = work.parts[ids[0]]
        work.parts[ids[0]] = PartRec(
            text=fmt.with_marker(title.text, "title", design="v2"), inputs=title.inputs
        )
        assert "no designed style" in validate(act, gate)


# The look step ---------------------------------------------------------------------------------


def lookable(findings=None, inner=None):
    """The look step's models (it finds what ``findings`` says) with the dressed-up designer."""

    looks = looking_router(findings=findings)
    designer = inner or dressed

    def route(request):
        system = str(request["messages"][0]["content"])
        if "demanding presentation designer" in system or "A designer looked at" in system:
            return looks(request)
        return designer(request)

    return route


class DesignedRenderer(FakeRenderer):
    """Reports a designed slide's own measurements: what spills, and type under the floor."""

    def capture(self, built, use="talk"):
        snapshot = super().capture(built, use)
        for slide, text in zip(snapshot["slides"], fmt.split_slides(built.content), strict=True):
            if not fmt.design_of(text):
                continue
            slide["drawn"] = "designed"
            small = [{"text": "412 buildings", "size": 15, "minimum": 18}]
            slide["smallText"] = small if slide["overflow"] else []
            slide["designed"] = {
                "style": "light",
                "minimumFont": 18,
                "smallest": small[0] if slide["overflow"] else None,
                "overflow": [{"kind": "spill", "element": "li.row", "text": "3 summers"}]
                if slide["overflow"]
                else [],
            }
        return snapshot


def test_an_overflowing_designed_slide_is_designed_again_with_the_problems_not_rewritten(tmp_path):
    _, plain, _ = deck(lookable())  # no renderer: the deck as designed
    renderer = DesignedRenderer(tmp_path, crowded=("stats",))
    _, built, fake = deck(lookable(), renderer=renderer)
    record = built.skill["look"]
    slides, before = fmt.split_slides(built.content), fmt.split_slides(plain.content)
    assert 2 in record["designed"] and record["status"] == "fixed"
    assert fmt.parse_slide(slides[1]).layout == "stats"  # redrawn, not rewritten as points
    assert fmt.design_of(slides[1])
    assert words(slides[1]) == words(before[1])
    assert not any(
        "A designer looked at these slides" in str(r["messages"][0]["content"])
        for r in fake.requests
    )
    assert renderer.captures == 2  # before and after


def test_the_look_asks_the_designer_with_what_was_measured(tmp_path):
    asked = []

    def route(request):
        system = str(request["messages"][0]["content"])
        if "Change to make in this design" in system:
            asked.append(system)
        return lookable()(request)

    renderer = DesignedRenderer(tmp_path, crowded=("stats",))
    deck(route, renderer=renderer)
    assert asked, "the designer was not asked again"
    request = asked[0]
    assert "text spills out of its box: li.row '3 summers' spills out of its box" in request
    assert "text under 18 px" in request and "412 buildings" in request
    assert "never by cutting or rewording" in request


def test_template_slides_keep_the_old_path_beside_designed_ones(tmp_path):
    def route(request):
        system = str(request["messages"][0]["content"])
        # Slide 2 cannot be designed (fails the guard twice): the template draws it.
        if "world-class" in system and 'Its content (layout hint "stats")' in system:
            return "<section>no</section>"
        return lookable()(request)

    renderer = DesignedRenderer(tmp_path, crowded=("stats", "table"))
    agents, built, _ = deck(route, renderer=renderer)
    slides = fmt.split_slides(built.content)
    record = built.skill["look"]
    assert record["designed"] == [3]  # the chart slide: designed, overflowing
    assert fmt.parse_slide(slides[1]).layout == "points"  # the plain stats slide: rewritten
    assert fmt.design_of(slides[1]) is None
    assert fmt.design_of(slides[2]) and fmt.design_of(slides[0])


def test_what_the_eye_finds_on_a_designed_slide_leads_to_a_design_not_a_rewrite(tmp_path):
    asked = []
    finding = {
        "slide": 2,
        "problem": "A wall of text with no visual",
        "severity": "high",
        "action": "cut",
        "fix": "show the two numbers as big tiles",
    }

    def route(request):
        system = str(request["messages"][0]["content"])
        if "Change to make in this design" in system:
            asked.append(system)
        return lookable([finding])(request)

    renderer = DesignedRenderer(tmp_path, crowded=())
    # Nothing overflows and nothing is small: only the eye finds a problem.
    agents, built, fake = deck(route, renderer=renderer)
    assert any("A wall of text with no visual" in a for a in asked)
    assert any("show the two numbers as big tiles" in a for a in asked)
    slides = fmt.split_slides(built.content)
    assert fmt.parse_slide(slides[1]).layout == "stats" and fmt.design_of(slides[1])
    assert not any(
        "A designer looked at these slides" in str(r["messages"][0]["content"])
        for r in fake.requests
    )


# The restyle route -------------------------------------------------------------------------


def test_the_restyle_route_saves_a_new_version_with_the_words_and_checks_unchanged(tmp_path):
    from test_outputs import built as saved_version
    from test_outputs import read_events, workspace

    with studio_deck(tmp_path, studio_inner) as (client, base_id, first, go):
        headers = workspace(client)
        path = f"/api/knowledge-bases/{base_id}/artifacts/{first['id']}/restyle/stream"
        payload = {"clientRequestId": "restyle-once", "style": "bold"}
        events = read_events(client.post(path, json=payload, headers=headers))
        stages = [data for name, data in events if name == "stage"]
        assert [s["state"] for s in stages if s["id"] == "design"] == ["running", "done"]
        assert next(s["detail"] for s in stages if s["state"] == "done") == "3 slides designed"
        restyled = saved_version(events)
        assert restyled["versionNumber"] == first["versionNumber"] + 1
        old, new = fmt.split_slides(first["content"]), fmt.split_slides(restyled["content"])
        assert fmt.parse_slide(new[0]).attrs["style"] == "bold"
        for before, after in zip(old, new, strict=True):
            assert words(before) == words(after)
            assert 'data-style="bold"' in fmt.design_of(after)
        assert restyled["citations"] == first["citations"]
        assert any(s["checked"] for s in first["sections"])
        assert [s["checked"] for s in restyled["sections"]] == [
            s["checked"] for s in first["sections"]
        ]
        again = saved_version(read_events(client.post(path, json=payload, headers=headers)))
        assert again["id"] == restyled["id"]  # the same request is not run twice
        refused = client.post(
            path, json={"clientRequestId": "restyle-bad", "style": "neon"}, headers=headers
        )
        assert refused.status_code == 422
