import json
from copy import deepcopy
from dataclasses import replace

import pytest
from fake_models import FakeProvider, gateway, model
from pydantic import BaseModel
from test_studio_pipeline import DECK, pool, request_text, router

from gunther.llm import ModelError
from gunther.output_types import OUTPUT_TYPES
from gunther.outputs import FirstWrite, Notes, ParentWrite, Spec, heading_of, split_document
from gunther.schemas import OutputScope, PlanCountOut, PlanLineInput, PlanOptionsOut, PlanPartOut
from gunther.studio import format as fmt
from gunther.studio import material as M
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.pipeline import StudioAgents
from gunther.studio.state import Run
from gunther.studio.work import StudioWorkAgents

PREFERENCES = {"emphasize": ["emphasis sentinel"], "avoid": ["avoidance sentinel"]}
MANIFEST = {
    "workspace_id": "w", "library_id": "library",
    "scope": {"mode": "selection", "source_ids": ["src1", "src2", "src3"]},
    "searched": [], "key_results_used": ["included-result"],
}
REPORT_ROWS = [
    {
        "heading": "Conditions", "job": "Set the context",
        "notes": "Keep the time range", "refs": [1],
    },
    {"heading": "Findings", "job": "Explain the outcome", "refs": [2]},
]
SUMMARY = {"summary_heading": "One finding", "summary_job": "Lead with the adjusted saving",
           "summary_notes": "Include the uncertainty", "summary_refs": [2],
           "summary_required_refs": [2]}
DIRECT_UNITS = {
    "flashcards": ["## What was tested?\nRoof coatings [1].", "## What changed?\nSavings [2]."],
    "quiz": [
        "## (quiz) What was tested?\n- [x] Roofs\n- [ ] Walls\nWhy: The trial studied roofs [1].",
        "## (quiz) What changed?\n- [x] Cooling use\n- [ ] Water use\nWhy: Use fell [2].",
    ],
    "mind_map": ["- Roof coatings", "  - Adjusted savings [2]"],
    "audio_overview": ["A: What changed?", "B: Cooling electricity fell [2]."],
}


def no_event(_value):
    pass


def authority(revision="p1", attempt="attempt"):
    return FirstWrite("w", "library", "conversation", "plan", revision, "request", "boot", attempt)


def options_for(type_id):
    return PlanOptionsOut(type=type_id, scope=OutputScope(), audience="scientist", length="short")


def spec_for(type_id):
    return Spec(
        OUTPUT_TYPES[type_id].shape, "scientist", output_type=type_id, length="short",
        emphasize=tuple(PREFERENCES["emphasize"]), avoid=tuple(PREFERENCES["avoid"]),
    )


def direct_document(type_id, units):
    shape = OUTPUT_TYPES[type_id].capabilities
    return shape.join(shape.preamble(type_id), units)


def report_document(headings):
    summary, *body = headings
    return "\n\n".join([
        "# Trial findings", f"## {summary}\nThe saving was 9% after adjustment [2].",
        *(f"## {heading}\nThe trial followed 412 buildings [1]." for heading in body),
    ])


def task_of(request):
    return request_text(request).split(P.TASK_BOUNDARY)[-1]


def has_preferences(task):
    following = task.split("Output preferences:\n")
    assert len(following) == 2
    data, _ = json.JSONDecoder().raw_decode(following[1])
    return data == PREFERENCES


def dispatch(type_id, *, direct_draft=None, repair=None, report_rows=REPORT_ROWS, report=None):
    def reply(request):
        task = task_of(request)
        if task.startswith("Propose the direction"):
            return json.dumps({
                "title": "Trial findings", "summary": "An overview of the trial",
                "angle": "How the adjustment changes the finding",
                "coverage": [{"text": "Comparison"}, {"text": "Conditions"}],
            })
        if "planning a written report" in task:
            return json.dumps({
                "title": "Trial findings", **SUMMARY,
                "sections": report_rows, "angle": "Compare cautiously",
            })
        if "presentation strategist" in task:
            return json.dumps({
                "title": "Cool roofs", "requested_minutes": 8,
                "slides": [
                    {"title": fmt.parse_slide(slide).title, "layout": fmt.parse_slide(slide).layout,
                     "notes": "Keep the comparison" if i == 2 else "", "refs": [min(i + 1, 3)]}
                    for i, slide in enumerate(fmt.split_slides(DECK))
                ],
            })
        if task.startswith("Fix these format problems") or "Fix these format problems" in task:
            assert repair is not None
            return repair
        if "Your report breaks Gunther's report format" in task and repair is not None:
            return repair
        if task.startswith("Write the complete"):
            return direct_draft or direct_document(type_id, DIRECT_UNITS[type_id])
        if "Write the report" in task:
            marker = "Use exactly these approved section headings in order."
            rows, _ = json.JSONDecoder().raw_decode(task.split(marker, 1)[1].lstrip())
            return report or report_document([row["heading"] for row in rows])
        return router(request)
    return reply


def prepare(agents, type_id, *, resume=None, manifest=MANIFEST, spec=None, checkpoint=no_event):
    return agents.prepare(
        model(), "low", spec or spec_for(type_id), pool(), Notes(),
        manifest=manifest, resume=resume, emit=no_event, checkpoint=checkpoint,
    )


def propose(agents, prepared, options, *, previous=None, feedback="", fixed_rows=(), **locks):
    return agents.propose(
        prepared, model=model(), effort="low", options=options, previous=previous,
        feedback=feedback, fixed_rows=fixed_rows, order_fixed=locks.pop("order_fixed", False),
        emit=no_event, checkpoint=no_event, **locks,
    )


def generate(agents, prepared, options, plan, revision="p1", *, permit=None, checkpoint=no_event):
    return agents.generate(
        prepared, model=model(), effort="low", options=options, plan=plan, revision=revision,
        authority=permit or authority(revision), emit=no_event, checkpoint=checkpoint,
    )


@pytest.mark.parametrize("type_id", ["report", "briefing", "slides", *DIRECT_UNITS])
def test_stages_checkpoint_the_plan_before_any_writer_and_approve_without_replanning(type_id):
    fake = FakeProvider(dispatch(type_id))
    agents = StudioWorkAgents(gateway(fake), spec_for(type_id).kind)
    agents.design = False
    options = options_for(type_id)
    snapshots = []
    prepared = prepare(agents, type_id, checkpoint=snapshots.append)

    def hear(event):
        if event.get("id") == "story" and event.get("state") == "done":
            assert snapshots[-1]["plan_draft"]["proposal"]
            assert snapshots[-1]["story_key"]

    plan = agents.propose(
        prepared, model=model(), effort="low", options=options, previous=None, feedback="",
        fixed_rows=(), order_fixed=False, emit=hear, checkpoint=snapshots.append,
    )
    calls = [task_of(r) for r in fake.requests]
    def writer(task):
        return task.startswith("Write the complete") or any(
            phrase in task for phrase in ("Write the report", "presentation writer")
        )

    assert not any(writer(task) for task in calls)
    if type_id in DIRECT_UNITS:
        assert plan.outline is None and plan.proposal.count.mode == "auto"
        assert sum(task.startswith("Propose the direction") for task in calls) == 1
    else:
        assert plan.proposal.count.mode == "exact"
        assert plan.proposal.count.value == len(plan.outline)
        critic = "You are a demanding senior partner reviewing"
        assert sum(task.startswith(critic) for task in calls) == 1
    assert all(has_preferences(task) for task in calls)
    assert "write_key" not in snapshots[-1]

    # Use a new Studio instance and a JSON round trip as the sidecar does on a later turn.
    saved = json.loads(json.dumps(snapshots[-1]))
    fake.requests.clear()
    agents = StudioAgents(gateway(fake), design=False)
    prepared = prepare(agents, type_id, resume=saved)
    assert fake.requests == []
    built = generate(agents, prepared, options, plan)
    assert prepared.progress["manifest"] == MANIFEST
    assert prepared.progress["pool_refs"] == [1, 2, 3]
    assert prepared.progress["manifest"]["key_results_used"] == ["included-result"]
    assert "key_results_used" not in prepared.progress and "searched" not in prepared.progress
    assert prepared.progress["story_key"] == saved["story_key"]
    assert prepared.progress["write_key"]
    calls = [task_of(r) for r in fake.requests]
    assert sum(writer(task) for task in calls) == 1
    planners = (
        "lead analyst", "strategist", "planning a written report",
        "senior partner reviewing a storyline", "Propose the direction",
    )
    assert not any(phrase in task for task in calls for phrase in planners)
    for request in fake.requests:
        task = task_of(request)
        if "checking step" in task:
            assert "Output preferences:" not in task
        else:
            assert has_preferences(task)
    if plan.outline:
        shape = OUTPUT_TYPES[type_id].capabilities
        preamble, parts = shape.split(built.content)
        assert [row["id"] for row in built.outline] == [row.id for row in plan.outline]
        assert [heading_of(part) for part in parts] == [row.heading for row in plan.outline]
        if type_id in {"report", "briefing"}:
            assert preamble == "<!-- report: v2 -->\n# Trial findings"
            assert "adjustment [1]" in parts[0].text and fmt.SUMMARY_MARK in parts[0].text
            assert built.content.count(fmt.SUMMARY_MARK) == 1
            assert built.outline[0]["notes"] == SUMMARY["summary_notes"]
            [write] = [task for task in calls if writer(task)]
            assert SUMMARY["summary_heading"] in write and SUMMARY["summary_job"] in write
            assert SUMMARY["summary_notes"] in write
        else:
            assert [row["layout"] for row in built.outline] == [row.layout for row in plan.outline]
            assert built.outline[2]["notes"] == "Keep the comparison"


def test_invalid_writer_authority_fails_before_checkpoint_or_provider_calls():
    fake = FakeProvider(dispatch("report"))
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    plan = propose(agents, prepared, options)
    before = deepcopy(prepared.progress)
    fake.requests.clear()
    args = dict(model=model(), effort="low", options=options, plan=plan, revision="p1",
                emit=no_event, checkpoint=no_event)
    for bad in (None, {"revision": "p1"}, object()):
        with pytest.raises(RuntimeError, match="write authority"):
            agents.generate(prepared, authority=bad, **args)
    with pytest.raises(ValueError, match="approved revision"):
        agents.generate(prepared, authority=authority("other"), **args)
    with pytest.raises(TypeError, match="authority"):
        agents.generate(prepared, **args)
    for keywords in ({}, {"authority": None}):
        with pytest.raises(RuntimeError, match="write authority"):
            agents.make(model(), "low", spec_for("report"), pool(), Notes(), **keywords)
        assert fake.requests == []
    assert prepared.progress == before
    assert fake.requests == []


@pytest.mark.parametrize("type_id", ["report", "briefing"])
def test_summary_is_the_first_rendered_and_sealed_row(tmp_path, type_id):
    from test_artifact_agent import done
    from test_outputs import app_for, approve_output, library, send_plan

    fake = FakeProvider(dispatch(type_id))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Sample": "The trial followed 412 buildings.",
                                        "Finding": "The saving was 9% after adjustment."})
        cid = "summary_plan"
        plan_id, events = send_plan(client, base, cid, "report_plan", {
            "type": type_id, "scope": {"mode": "library"},
        })
        planned = done(events)
        shown = planned["plan"]
        assert shown["proposal"]["count"] == {"mode": "exact", "value": 3}
        row = shown["outline"][0]
        assert row["id"] and (row["heading"], row["goal"], row["notes"]) == (
            SUMMARY["summary_heading"], SUMMARY["summary_job"], SUMMARY["summary_notes"],
        )
        artifact = done(approve_output(client, base, cid, plan_id, "report_approve", planned))[
            "artifact"
        ]
    sealed = artifact["provenance"]["creationPlan"]
    assert sealed["outline"] == shown["outline"]
    preamble, sections = OUTPUT_TYPES[type_id].capabilities.split(artifact["content"])
    assert len(sections) == len(sealed["outline"]) == len(artifact["sections"]) == 3
    assert heading_of(sections[0]) == row["heading"]
    assert fmt.SUMMARY_MARK in sections[0].text and "adjustment [1]" in sections[0].text
    assert ":::callout" not in preamble


def test_a_preamble_callout_cannot_stand_in_for_the_approved_summary():
    wrong = (
        "# Trial findings\n\n:::callout Overview\nThe saving was 9% [2].\n:::\n\n"
        "## One finding\n\n## Conditions\nThe trial followed 412 buildings [1].\n\n"
        "## Findings\nThe saving was 9% [2]."
    )
    fake = FakeProvider(dispatch("report", report=wrong))
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    plan = propose(agents, prepared, options)
    with pytest.raises(ModelError, match="Write the executive summary under the first"):
        generate(agents, prepared, options, plan)
    assert "draft" not in prepared.progress


@pytest.mark.parametrize("type_id", ["report", "briefing"])
@pytest.mark.parametrize("stage", ["draft", "assembly"])
@pytest.mark.parametrize("definition", [
    pytest.param("[material]: /appendix", id="link"),
    pytest.param("[^material]: Supporting notes for the appendix.", id="unused-footnote"),
])
def test_approval_rejects_a_summary_with_only_a_definition(
    tmp_path, monkeypatch, type_id, stage, definition,
):
    from test_artifact_agent import done, kept, versions_of
    from test_outputs import app_for, approve_output, library, send_plan

    report = report_document([SUMMARY["summary_heading"], *(row["heading"] for row in REPORT_ROWS)])
    if stage == "draft":
        report = report.replace("The saving was 9% after adjustment [2].", definition, 1)
    else:
        verify = StudioAgents.verify

        def with_empty_summary(self, *args, **kwargs):
            summary, *sections = verify(self, *args, **kwargs)
            heading = summary.text.partition("\n")[0]
            return [replace(summary, text=f"{heading}\n{definition}"), *sections]

        monkeypatch.setattr(StudioAgents, "verify", with_empty_summary)
    fake = FakeProvider(dispatch(type_id, report=report))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Sample": "The trial followed 412 buildings.",
                                        "Finding": "The saving was 9% after adjustment."})
        cid = "summary_plan"
        plan_id, events = send_plan(client, base, cid, "report_plan", {
            "type": type_id, "scope": {"mode": "library"},
        })
        planned = done(events)
        name, result = approve_output(client, base, cid, plan_id, "report_approve", planned)[-1]
        assert name == "error" and "executive summary" in result["detail"]
        assert versions_of(client) == []
        [work] = kept(client, cid)[0].works
    assert ("draft" in work.studio_progress) == (stage == "assembly")


@pytest.mark.parametrize("planned", [False, True])
def test_planned_exhibits_drive_report_repair(planned):
    exhibit = (
        ":::exhibit\nRates by group\n| Group | Rate |\n| --- | --- |\n| A | 10% |\n"
        "| B | 20% |\n| C | 30% |\n参照資料：Trial [1]\n:::"
    )
    first, second = REPORT_ROWS
    rows = [{**first, "exhibits": ["Rates by group"] if planned else []}, second]
    expected = report_document([SUMMARY["summary_heading"], first["heading"], second["heading"]])
    expected = expected.replace(
        "The trial followed 412 buildings [1].", "The rates were 10%, 20% and 30% [1].", 1,
    ).replace("## Findings", f"{exhibit}\n\n## Findings", 1)
    draft = expected.replace(exhibit, f"```markdown\n{exhibit}\n```", 1) if planned else (
        expected.replace(f"{exhibit}\n\n", "", 1)
    )
    fake = FakeProvider(dispatch("report", report_rows=rows, report=draft, repair=expected))
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    plan = propose(agents, prepared, options)
    built = generate(agents, prepared, options, plan)

    repairs = [task_of(request) for request in fake.requests
               if "Your report breaks Gunther's report format" in task_of(request)]
    if planned:
        [repair] = repairs
        assert "Section 2 plans 1 exhibit(s) and has 0" in repair
        assert "| A | 10% [2] |" in built.content
    else:
        assert not repairs and ":::exhibit" not in built.content


def test_approval_after_a_language_edit_repairs_a_missing_planned_exhibit(tmp_path):
    from test_artifact_agent import conversations, done
    from test_outputs import app_for, approve_output, library, send_plan, workspace

    caption = "Trial enrollment"
    exhibit = (
        f":::exhibit\n{caption}\n| Group | Buildings |\n| --- | --- |\n"
        "| All | 412 |\nSource: Sample [1]\n:::"
    )
    first, second = REPORT_ROWS
    rows = [{**first, "exhibits": [caption]}, second]
    draft = report_document([SUMMARY["summary_heading"], first["heading"], second["heading"]])
    repaired = draft.replace("## Findings", f"{exhibit}\n\n## Findings", 1)
    fake = FakeProvider(dispatch("report", report_rows=rows, report=draft, repair=repaired))

    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Sample": "The trial followed 412 buildings.",
                                        "Finding": "The saving was 9% after adjustment."})
        cid = "edited_report"
        plan_id, events = send_plan(client, base, cid, "report_plan", {
            "type": "report", "scope": {"mode": "library"}, "language": "English",
        })
        planned = done(events)
        edited = client.patch(
            conversations(base, f"/{cid}/works/{plan_id}"), headers=workspace(client),
            json={"baseEditVersion": planned["editVersion"], "options": {"language": "French"}},
        ).raise_for_status().json()["works"][0]["plan"]
        assert edited["planStatus"] == "ready" and edited["outline"] == planned["plan"]["outline"]

        fake.requests.clear()
        approved = approve_output(client, base, cid, plan_id, "report_approve", {"plan": edited})
        artifact = done(approved)["artifact"]

    tasks = [task_of(request) for request in fake.requests]
    assert any("lead analyst" in task for task in tasks)
    assert not any("planning a written report" in task for task in tasks)
    [repair] = [task for task in tasks if "Your report breaks Gunther's report format" in task]
    assert "Section 2 plans 1 exhibit(s) and has 0" in repair
    assert artifact["provenance"]["language"] == "French"
    assert fmt.check_report(artifact["content"], exhibit_counts=(0, 1, 0)) == []


@pytest.mark.parametrize(
    "case", ["fenced-repair", "empty-grid-repair", "valid-repair", "fenced-assembly"]
)
def test_approval_preserves_planned_exhibits_through_claim_repair(tmp_path, monkeypatch, case):
    from test_artifact_agent import done, versions_of
    from test_outputs import app_for, approve_output, library, send_plan

    exhibit = (
        ":::exhibit\nTrial enrollment\n| Group | Buildings |\n| --- | --- |\n"
        "| All | 412 [1] |\nSource: Sample [1]\n:::"
    )
    empty_grid_exhibit = (
        ":::exhibit\nTrial enrollment\n| Group | Buildings |\n| - | - |\n"
        "Source: Sample [1]\n:::"
    )
    first, second = REPORT_ROWS
    rows = [{**first, "exhibits": ["Trial enrollment"]}, second]
    report = report_document([SUMMARY["summary_heading"], first["heading"], second["heading"]])
    original = "The trial followed 412 buildings."
    corrected = "The trial enrolled 412 buildings"
    report = report.replace("The trial followed 412 buildings [1].", original, 1)
    report = report.replace("## Findings", f"{exhibit}\n\n## Findings", 1)
    if case == "fenced-repair":
        body = f"```markdown\n{exhibit}\n```"
    elif case == "empty-grid-repair":
        body = empty_grid_exhibit
    else:
        body = exhibit
    repaired = f"## {first['heading']}\n{corrected} [1].\n\n{body}"
    base_reply = dispatch("report", report_rows=rows, report=report)

    def reply(request):
        return repaired if "fact-checker" in task_of(request) else base_reply(request)

    if case == "fenced-assembly":
        fallback = StudioAgents._chart_fallback

        def with_fenced_exhibit(self, draft, index):
            checked = fallback(self, draft, index)
            if index == 1:
                prose, marker, exhibit_body = checked.text.partition(":::exhibit")
                assert marker and corrected in prose
                return replace(checked, text=f"{prose}```markdown\n{marker}{exhibit_body}\n```")
            return checked

        monkeypatch.setattr(StudioAgents, "_chart_fallback", with_fenced_exhibit)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Sample": "The trial followed 412 buildings.",
                                        "Finding": "The saving was 9% after adjustment."})
        cid = "planned_exhibit"
        plan_id, events = send_plan(client, base, cid, "report_plan", {
            "type": "report", "scope": {"mode": "library"},
        })
        planned = done(events)
        events = approve_output(client, base, cid, plan_id, "report_approve", planned)
        repairs = [task_of(request) for request in fake.requests
                   if "fact-checker" in task_of(request)]
        [repair] = repairs
        assert original in repair and exhibit in repair
        if case == "fenced-assembly":
            name, result = events[-1]
            assert name == "error" and "Section 2 plans 1 exhibit(s) and has 0" in result["detail"]
            assert versions_of(client) == []
            return
        artifact = done(events)["artifact"]

    _, sections = split_document(artifact["content"], "report")
    assert fmt.check_report(artifact["content"], exhibit_counts=(0, 1, 0)) == []
    assert "```" not in sections[1]
    skipped = artifact["provenance"]["skill"]["skipped"]
    if case in ("fenced-repair", "empty-grid-repair"):
        assert original in sections[1] and corrected not in sections[1]
        assert "| All | 412 [" in sections[1]
        assert any(issue["verdict"] == "unsourced" for issue in artifact["sections"][1]["issues"])
        assert any(note["label"] == "Section 2 claim repair rejected" for note in skipped)
    else:
        assert corrected in sections[1] and original not in sections[1]
        assert not skipped


def test_proposed_rows_carry_only_verified_pool_refs_and_required_evidence():
    from gunther.evidence import Evidence

    requested = [
        {"heading": "Web evidence", "refs": [4, 4, 90], "required_refs": [4, 90]},
        {"heading": "Local context", "refs": [1], "required_refs": [2]},
    ]
    fake = FakeProvider(dispatch("report", report_rows=requested))
    agents = StudioAgents(gateway(fake))
    evidence = pool()
    evidence.adopt(Evidence(kind="web", title="Web evidence", text="Additional measurements."))
    prepared = agents.prepare(
        model(), "low", spec_for("report"), evidence, Notes(), manifest=MANIFEST,
        resume=None, emit=no_event, checkpoint=no_event,
    )
    plan = propose(agents, prepared, options_for("report"))
    assert [(row.refs, row.required_refs) for row in plan.outline] == [
        ([2], [2]), ([4], [4]), ([1, 2], [2]),
    ]
    assert [row["refs"] for row in prepared.progress["plan_draft"]["outline"]] == [
        [2], [4], [1, 2],
    ]


def test_understand_checkpoints_preparation_that_propose_reuses():
    fake = FakeProvider(dispatch("report"))
    agents = StudioWorkAgents(gateway(fake), "report")
    snapshots = []

    def hear(event):
        if event.get("id") == "memo" and event.get("state") == "done":
            assert snapshots and snapshots[-1]["memo"]["answer"]

    approach = agents.understand_work(
        model(), "low", spec_for("report"), pool(), Notes(), hear, None,
        manifest=MANIFEST, resume=None, checkpoint=snapshots.append,
    )
    assert approach["memo"] == snapshots[-1]["memo"]
    prepared = prepare(agents, "report", resume=snapshots[-1])
    assert propose(agents, prepared, options_for("report")).outline
    assert sum("lead analyst" in task_of(r) for r in fake.requests) == 1


@pytest.mark.parametrize("changed", [None, "selection", "inputs", "web", "language"])
def test_pre_upgrade_checkpoint_reuses_only_matching_preparation(changed):
    selected = ["src3", "src1", "src2"]
    selection = OutputScope(mode="selection", source_ids=selected).model_dump()
    inputs = {"sources": [
        {"id": source, "title": f"Source {source[-1]}", "revisionId": f"rev{source[-1]}"}
        for source in selected
    ], "units": [], "sessions": []}
    spec = Spec("slides", "scientist", "slides", "Explain the trial", theme="ink",
                design_version=1, language="Dutch")
    original = {
        # Captured from the pre-upgrade Studio preparation at 038be4c, with the original
        # service's scope envelope and Web enabled.
        "material_key": "3c706aba6b287204b56132257451a4f54cabd1d207116b77bb271df8838b75f2",
        "request_key": "5d05d2459e63a9874b95072af209bcc3910acd37a21d9f5e657f41e8f7bbbc71",
        "pool": "28e6b1d710d0b213",
        "purpose": {"purpose": "general", "reason": "A general briefing"},
        "memo": {"language": "English", "answer": "Cool roofs save 9% after adjustment [2]"},
    }
    manifest = MANIFEST | {
        "web_pass": "off" if changed == "web" else "complete", "legacy_web": changed != "web",
        "legacy_scope": {"selection": selection, "inputs": inputs},
    }
    if changed == "selection":
        manifest["legacy_scope"]["selection"] = selection | {"source_ids": sorted(selected)}
    elif changed == "inputs":
        inputs["sources"][0]["revisionId"] = "newRevision"
    elif changed == "language":
        spec = replace(spec, language="English")
    fake = FakeProvider(router)
    agents = StudioAgents(gateway(fake), use="read")
    saved = deepcopy(original)
    prepared = prepare(agents, "slides", spec=spec, manifest=manifest, resume=saved)
    assert (not fake.requests) == (changed is None)
    if changed is None:
        assert prepared.run.memo.answer == original["memo"]["answer"]
        assert prepared.run.spec.theme == "ink" and prepared.run.spec.language == "Dutch"
    assert prepared.progress["manifest"] == {
        key: value for key, value in manifest.items() if key not in {"legacy_scope", "legacy_web"}
    }
    assert "request_key" not in prepared.progress and saved == original


@pytest.mark.parametrize("type_id", list(DIRECT_UNITS))
def test_exact_count_uses_each_shape_decoder_and_repairs_once(type_id):
    first = direct_document(type_id, DIRECT_UNITS[type_id])
    more = {
        "flashcards": "## How long was the trial?\nThree summers [1].",
        "quiz": "## (quiz) Which measure fell?\n- [x] Cooling\n- [ ] Heating\nWhy: It fell [2].",
        "mind_map": "    - Raw and adjusted rates differ [2]",
        "audio_overview": "A: The summers were compared [1].",
    }
    fixed = direct_document(type_id, [*DIRECT_UNITS[type_id], more[type_id]])
    fake = FakeProvider(dispatch(type_id, direct_draft=first, repair=fixed))
    agents = StudioAgents(gateway(fake), design=False)
    options = options_for(type_id)
    prepared = prepare(agents, type_id)
    plan = propose(agents, prepared, options)
    plan.proposal.count = PlanCountOut(mode="exact", value=3)
    plan.proposal.coverage = [PlanLineInput(id="newTopic", text="Edited coverage sentinel")]
    final = generate(agents, prepared, options, plan)
    shape = OUTPUT_TYPES[type_id].capabilities
    assert len(shape.split(final.content)[1]) == 3
    [repair] = [task_of(r) for r in fake.requests if "Fix these format problems" in task_of(r)]
    assert "The draft has 2" in repair and "Edited coverage sentinel" in repair
    assert '"count": {"mode": "exact", "value": 3}' in repair
    assert has_preferences(repair)


def test_exact_count_still_wrong_after_repair_fails():
    first = direct_document("flashcards", DIRECT_UNITS["flashcards"])
    fake = FakeProvider(dispatch("flashcards", direct_draft=first, repair=first))
    agents = StudioAgents(gateway(fake))
    options = options_for("flashcards")
    prepared = prepare(agents, "flashcards")
    plan = propose(agents, prepared, options)
    plan.proposal.count = PlanCountOut(mode="exact", value=3)
    with pytest.raises(ModelError, match="Expected exactly 3 cards"):
        generate(agents, prepared, options, plan)
    assert "direct_text" not in prepared.progress


def test_writer_resume_survives_an_attempt_change_for_the_same_revision():
    fake = FakeProvider(dispatch("flashcards"))
    agents = StudioAgents(gateway(fake))
    options = options_for("flashcards")
    prepared = prepare(agents, "flashcards")
    plan = propose(agents, prepared, options)
    generate(agents, prepared, options, plan)
    saved = deepcopy(prepared.progress)
    fake.requests.clear()
    reopened = prepare(agents, "flashcards", resume=saved)
    generate(agents, reopened, options, plan, permit=authority(attempt="retry"))
    assert reopened.progress["write_key"] == saved["write_key"]
    assert not any(task_of(request).startswith("Write the complete") for request in fake.requests)


def test_edited_outline_reuses_preparation_and_rebinds_metadata_by_id():
    fake = FakeProvider(dispatch("report"))
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    plan = propose(agents, prepared, options)
    generate(agents, prepared, options, plan)
    original = deepcopy(prepared.progress)
    summary, conditions, findings = plan.outline
    new = PlanPartOut(id="newPart", heading="Implications", notes="Ask what follows")
    plan.outline = [
        summary, findings, new, conditions.model_copy(update={
            "goal": "Use different evidence", "refs": [], "required_refs": [],
        }),
    ]
    plan.proposal.count = PlanCountOut(mode="exact", value=4)
    fake.replies[:] = [
        dispatch("report", report=report_document([r.heading for r in plan.outline])),
    ]
    fake.requests.clear()
    reopened = prepare(agents, "report", resume=original)
    built = generate(agents, reopened, options, plan, "p2")
    assert [row["id"] for row in built.outline] == [summary.id, findings.id, new.id, conditions.id]
    assert built.outline[2]["notes"] == "Ask what follows"
    assert [s["refs"] for s in reopened.progress["storyline"]["sections"]] == [[2], [], []]
    assert all(reopened.progress[k] == original[k] for k in ("material_key", "prepare_key"))
    assert all(reopened.progress[k] != original[k] for k in ("story_key", "write_key"))
    assert not any(
        "lead analyst" in task_of(r) or "planning a written report" in task_of(r)
        for r in fake.requests
    )


@pytest.mark.parametrize("changed", ["language", "material", "row"])
def test_resumed_preparation_limits_annotations_to_the_same_material_and_row(changed):
    first, second = REPORT_ROWS
    rows = [{**first, "exhibits": ["Trial enrollment"]}, second]
    fake = FakeProvider(dispatch("report", report_rows=rows))
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    plan = propose(agents, prepared, options)

    options = options.model_copy(update={"language": "French"})
    spec = replace(spec_for("report"), language=options.language)
    snapshots = []
    prepare(agents, "report", spec=spec, resume=prepared.progress, checkpoint=snapshots.append)
    # Reopen the first preparation checkpoint, before the new memo is saved.
    saved = json.loads(json.dumps(snapshots[0]))
    manifest = MANIFEST
    if changed == "material":
        manifest = MANIFEST | {"scope": {"mode": "library"}}
    if changed == "row":
        plan.outline[1] = plan.outline[1].model_copy(update={"id": "replacement"})
    agents = StudioAgents(gateway(fake))
    reopened = prepare(agents, "report", spec=spec, resume=saved, manifest=manifest)

    if changed == "language":
        with pytest.raises(ModelError, match=r"Section 2 plans 1 exhibit\(s\) and has 0"):
            generate(agents, reopened, options, plan)
    else:
        assert ":::exhibit" not in generate(agents, reopened, options, plan).content


def test_replan_preserves_locked_row_fields_and_membership():
    fake = FakeProvider(dispatch("report"))
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    old = propose(agents, prepared, options)
    summary, first, second = old.outline
    reordered = (
        summary.model_copy(update={"notes": "Keep this qualifier", "edited_fields": ["notes"]}),
        second.model_copy(update={"notes": "User notes", "edited_fields": ["notes"]}),
        first.model_copy(update={"heading": "User heading", "edited_fields": ["heading"]}),
    )
    fake.replies[:] = [dispatch("report", report_rows=[
        {"id": first.id, "heading": "Assistant title", "job": "New goal"},
        {"heading": "A suggested addition"},
    ])]
    updated = propose(
        agents, prepared, options, previous=old, feedback="Use the revised order.",
        fixed_rows=reordered, order_fixed=True,
    )
    assert [(r.id, r.heading, r.goal, r.notes) for r in updated.outline] == [
        (summary.id, summary.heading, summary.goal, "Keep this qualifier"),
        (second.id, second.heading, second.goal, "User notes"),
        (first.id, "User heading", "New goal", first.notes),
    ]


def test_replan_keeps_the_notes_only_cover_edit_when_the_proposal_omits_ids():
    fake = FakeProvider(dispatch("slides"))
    agents = StudioAgents(gateway(fake))
    options = options_for("slides")
    prepared = prepare(agents, "slides")
    old = propose(agents, prepared, options)
    cover = old.outline[0]
    edited = cover.model_copy(update={"notes": "Pause for questions", "edited_fields": ["notes"]})
    old.outline[0] = edited

    # The presentation strategist in dispatch returns slides without IDs.
    updated = propose(agents, prepared, options, previous=old, fixed_rows=(edited,))

    [title_row] = [row for row in updated.outline if row.layout == "title"]
    assert len(updated.outline) == len(old.outline)
    assert title_row == updated.outline[0]
    assert (title_row.id, title_row.notes, title_row.edited_fields) == (
        cover.id, edited.notes, ["notes"],
    )


def test_replan_keeps_authored_coverage_and_count():
    fake = FakeProvider(dispatch("audio_overview"))
    agents = StudioAgents(gateway(fake))
    options = options_for("audio_overview")
    prepared = prepare(agents, "audio_overview")
    plan = propose(agents, prepared, options)
    plan.proposal.count = PlanCountOut(mode="target", value=7)
    plan.proposal.coverage = [PlanLineInput(id="authored", text="Authored coverage")]
    updated = propose(agents, prepared, options, previous=plan, coverage_fixed=True)
    assert updated.proposal.coverage == plan.proposal.coverage
    assert updated.proposal.count == plan.proposal.count


@pytest.mark.parametrize("changed", ["voices", "preferences", "focus", "material"])
def test_preparation_invalidation_by_input(changed):
    type_id = "audio_overview"
    fake = FakeProvider(dispatch(type_id))
    agents = StudioAgents(gateway(fake))
    options = options_for(type_id)
    prepared = prepare(agents, type_id)
    plan = propose(agents, prepared, options)
    generate(agents, prepared, options, plan)
    before = deepcopy(prepared.progress)
    manifest, spec = MANIFEST, spec_for(type_id)
    if changed == "voices":
        options = options.model_copy(update={"voices": ("voice-b", "voice-a")})
    elif changed == "preferences":
        spec = replace(spec, emphasize=("another preference",))
    elif changed == "focus":
        spec = replace(spec, brief="Focus on comparison")
    else:
        manifest = MANIFEST | {"scope": {"mode": "library"}}
    fake.requests.clear()
    resumed = prepare(agents, type_id, spec=spec, manifest=manifest, resume=before)
    if changed == "voices":
        assert fake.requests == []
        generate(agents, resumed, options, plan, "p2")
        assert resumed.progress["story_key"] == before["story_key"]
        assert resumed.progress["write_key"] != before["write_key"]
    else:
        assert sum("lead analyst" in task_of(r) for r in fake.requests) == 1
        assert "story_key" not in resumed.progress and "write_key" not in resumed.progress
        assert resumed.progress["prepare_key"] != before["prepare_key"]
    assert (resumed.progress["material_key"] == before["material_key"]) == (changed != "material")


def test_corrupt_preparation_falls_back_at_its_boundary_and_long_read_stays_on_a_hit(monkeypatch):
    monkeypatch.setattr(M, "DIRECT_TOKENS", 1)

    def reply(request):
        if task_of(request).startswith("You are a research analyst"):
            return '{"facts":[{"text":"A field trial","refs":[1]}]}'
        return dispatch("report")(request)

    fake = FakeProvider(reply)
    agents = StudioAgents(gateway(fake))
    options = options_for("report")
    prepared = prepare(agents, "report")
    assert prepared.run.read_digest and "Notes from the material" in prepared.material
    assert has_preferences(task_of(fake.requests[0]))
    plan = propose(agents, prepared, options)
    saved = deepcopy(prepared.progress)
    fake.requests.clear()
    reopened = prepare(agents, "report", resume=saved)
    assert reopened.material == prepared.material and not fake.requests
    parent = ParentWrite("w", "library", None, "lineage", "v1")
    generate(agents, reopened, options, plan, permit=parent)
    checks = [task for r in fake.requests if "checking step" in (task := request_text(r))]
    assert checks and all("Notes from the material" not in task for task in checks)
    saved["memo"] = {"answer": ["invalid"]}
    fake.requests.clear()
    repaired = prepare(agents, "report", resume=saved)
    assert repaired.progress["memo"]["answer"] and "story_key" not in repaired.progress
    assert len(fake.requests) == 1 and "lead analyst" in task_of(fake.requests[0])


def test_scientific_revision_uses_validated_targets_instead_of_display_text():
    with pytest.raises(ValueError, match="slide numbers"):
        S.ScientificProblem(problem="Unclear", fix="Clarify", route="slides")
    with pytest.raises(ValueError):
        S.ScientificProblem(problem="Unclear", fix="Clarify", route="slides", slides=["2"])
    problem = S.ScientificProblem(
        where="slide 1", problem="Define the measure", fix="State its unit",
        route="slides", slides=[2],
    )
    slides = fmt.split_slides(DECK)
    fake = FakeProvider("<!-- replaces slide 2 -->\n" + slides[1])
    agents = StudioAgents(gateway(fake))
    run = Run(spec_for("slides"), "read")
    agents._revise_science(model(), "low", run, DECK, pool(), "evidence", [problem])
    task = task_of(fake.requests[0])
    assert "These are deck slide numbers 2." in task
    assert "<!-- replaces slide 1 -->" not in task
    assert has_preferences(task)


def test_part_subtree_passage_and_review_share_the_preference_block():
    class Replacement(BaseModel):
        text: str

    fake = FakeProvider(
        "- Adjusted savings [2]\n  - Context [1]",
        '{"text": "A replacement"}',
        '{"changes":[{"part":2,"instruction":"Clarify the comparison"}]}',
    )
    agents = StudioWorkAgents(gateway(fake), "map")
    spec = spec_for("mind_map")
    parts = DIRECT_UNITS["mind_map"]
    subtree = agents.write_part(
        model(), "low", spec, parts, ["Roof coatings", "Adjusted savings"],
        1, pool(), instruction="Clarify the comparison",
    )
    assert subtree.startswith("  - Adjusted savings") and "    - Context" in subtree
    replacement = agents.rewrite_passage(
        model(), "low", spec, Replacement, system="Rewrite one passage", prompt="The material",
    )
    assert replacement.text == "A replacement"
    assert [item.part for item in agents.review_parts(model(), "low", spec, parts)] == [2]
    assert len(fake.requests) == 3 and all(has_preferences(task_of(r)) for r in fake.requests)


def test_scientific_preparation_is_reused_and_pixel_reading_stays_literal(tmp_path):
    from test_studio_science import DECK as STUDY_DECK
    from test_studio_science import pixels, science_pool, science_router

    story = science_router()

    def reply(request):
        result = story(request)
        if task_of(request).startswith("Plan a scientific explanation deck"):
            response = json.loads(result)
            for row, slide in zip(response["slides"], fmt.split_slides(STUDY_DECK), strict=True):
                row["title"] = heading_of(slide)
            return json.dumps(response)
        return result

    fake = FakeProvider(reply)
    agents = StudioAgents(gateway(fake), use="read", asset_reader=pixels(tmp_path), design=False)
    options = options_for("slides").model_copy(update={"use": "read"})
    spec = spec_for("slides")
    evidence = science_pool()

    def read(resume=None, spec=spec):
        return agents.prepare(
            model(), "low", spec, evidence, Notes(), manifest=MANIFEST,
            resume=resume, emit=no_event, checkpoint=no_event,
        )

    prepared = read()
    plan = propose(agents, prepared, options)
    assert [row.heading for row in plan.outline] == [
        heading_of(slide) for slide in fmt.split_slides(STUDY_DECK)
    ]
    saved = json.loads(json.dumps(prepared.progress))
    assert saved["study_map"] and saved["coverage_plan"] and saved["figure_readings"]
    for request in fake.requests:
        task = task_of(request)
        if task.startswith("Read the attached original source figure pixels"):
            assert all(line not in task for values in PREFERENCES.values() for line in values)
        else:
            assert has_preferences(task)
    fake.requests.clear()
    reopened = read(saved)
    assert not fake.requests
    generate(agents, reopened, options, plan)
    tasks = [task_of(request) for request in fake.requests]
    assert any(task.startswith("You are an independent learner") for task in tasks)
    assert all("Output preferences:" not in task for task in tasks if "checking step" in task)
    assert all(has_preferences(task) for task in tasks if "checking step" not in task)

    fake.requests.clear()
    updated = read(saved, replace(spec, avoid=("A changed preference",)))
    assert updated.progress["material_key"] == saved["material_key"]
    assert updated.progress["prepare_key"] != saved["prepare_key"]
    tasks = [task_of(request) for request in fake.requests]
    assert any(task.startswith("Build the study map") for task in tasks)
    assert not any(task.startswith("Read the attached original source figure") for task in tasks)
