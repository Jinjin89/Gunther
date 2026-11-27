import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from gunther.artifact_plans import (
    PlanAvailability,
    PlanConflictError,
    PlanValidationError,
    ReviewedMaterial,
    apply_work_plan_patch,
    can_approve_work,
    normalize_plan_outline,
    plan_revision,
    project_saved_outline,
    project_studio_work_summary,
    project_work_plan,
    work_spec_for_options,
)
from gunther.artifact_workspace import (
    BriefUpdate,
    CardRec,
    ConversationBrief,
    ItemRec,
    Pending,
    PlanAttemptRecord,
    PlanProposalRecord,
    PlanRecord,
    Work,
    apply_brief_updates,
    digest_approach_inputs,
    digest_outline_inputs,
    digest_part_inputs,
    dump_workspace,
    edit_preferences,
    keep_edits,
    load_brief,
    load_workspace,
    spec_of,
)
from gunther.schemas import (
    ApprovePlanTurnInput,
    ArtifactOut,
    ArtifactTurnInput,
    InitialPlanTurnInput,
    OutlineItem,
    OutputVoicesOut,
    PlanLineInput,
    PlanOptionsOut,
    PlanPartEditInput,
    SavedArtifactTurnInput,
    WorkPlanPatchInput,
)

REVISION = "p3-" + "a" * 64
TURN = {"clientRequestId": "request_123", "planId": "draft_123"}
SCOPE = {"mode": "selection", "sourceIds": ["source_a", "source_b"]}
MATERIAL = ReviewedMaterial("local-key", frozenset({1}))
AVAILABLE = PlanAvailability(
    models={"available/model": ("low", "high"), "available/other": ("high",)},
    default_model=("available/model", "low"),
    voices=OutputVoicesOut(
        choices=[{"value": voice, "label": voice} for voice in ("voice-a", "voice-b")],
        defaults=("voice-a", "voice-b"),
    ),
)


def row(identity, heading="A heading", layout=None, **fields):
    return {"id": identity, "heading": heading, "layout": layout, "goal": "", "notes": ""} | fields


def ready_work(type_id="report", **settings):
    options = PlanOptionsOut.model_validate(
        {
            "type": type_id,
            "scope": SCOPE,
            "model": "available/model",
            "effort": "high",
        }
        | settings
    )
    return Work(
        key="draft_123",
        title="A useful plan",
        spec=work_spec_for_options(options),
        plan=PlanRecord(
            id="draft_123",
            epoch=3,
            reviewed_material_key=MATERIAL.key,
            proposal=PlanProposalRecord(
                summary="Explain the findings",
                angle="Review the comparisons",
                coverage=[PlanLineInput(id="topic_a", text="Expected results")],
            ),
        ),
        outline=[ItemRec(id="row_a", heading="Main results", refs=[1])]
        if type_id == "report"
        else [],
    )


def patch(brief, work, *, version=3, material=MATERIAL, pending=None, **fields):
    return apply_work_plan_patch(
        brief,
        work,
        WorkPlanPatchInput.model_validate({"baseEditVersion": version} | fields),
        edit_version=version,
        material=material,
        availability=AVAILABLE,
        pending=pending,
    )


def test_turn_envelopes_dispatch_and_preserve_saved_requests():
    saved = ArtifactTurnInput(client_request_id="request_123", kind="answer", text=" Yes ")
    assert isinstance(saved.root, SavedArtifactTurnInput)
    assert saved.text == "Yes" and saved.request_fields() == {}
    initial = ArtifactTurnInput.model_validate(
        TURN
        | {
            "kind": "plan",
            "setup": {"type": "slides", "scope": SCOPE},
            "initialOutline": [row("cover", "  Opening  ", "title")],
        }
    )
    assert isinstance(initial.root, InitialPlanTurnInput)
    assert initial.root.initial_outline[0].heading == "Opening"
    assert initial.model_dump(by_alias=True)["planId"] == TURN["planId"]
    approval = ArtifactTurnInput.model_validate(
        TURN | {"kind": "approve", "planRevision": REVISION}
    )
    assert isinstance(approval.root, ApprovePlanTurnInput)
    planned_answer = ArtifactTurnInput.model_validate(
        TURN
        | {
            "kind": "answer",
            "planRevision": REVISION,
            "questionMessageId": "message_1",
            "answer": {"type": "choice", "index": 0},
        }
    )
    assert planned_answer.root.answer.index == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "approve", "planRevision": REVISION, "build": {}},
        {"kind": "approve", "planRevision": REVISION, "options": {"web": True}},
        {"kind": "approve", "planRevision": REVISION, "initialOutline": []},
        {"kind": "plan", "planRevision": REVISION, "setup": {"type": "report", "scope": SCOPE}},
        {"kind": "plan", "setup": {"type": "report", "scope": SCOPE, "extra": 1}},
        {"kind": "plan", "setup": {"type": "report", "scope": {"mode": "selection"}}},
        {"kind": "plan", "setup": {"type": "report", "scope": SCOPE | {"sourceIds": ["a", " a "]}}},
        {
            "kind": "plan",
            "setup": {"type": "report", "scope": SCOPE},
            "initialOutline": [row("a"), row("a")],
        },
        {"kind": "plan", "setup": {"type": "quiz", "scope": SCOPE}, "initialOutline": [row("a")]},
        {
            "kind": "answer",
            "planRevision": REVISION,
            "questionMessageId": "message_1",
            "answer": {"type": "use_settings", "text": "Choose all"},
        },
        {
            "kind": "answer",
            "planRevision": REVISION,
            "questionMessageId": "message_1",
            "answer": {"type": "choice", "index": True},
        },
    ],
)
def test_turns_reject_mixed_and_unchecked_fields(fields):
    with pytest.raises(ValidationError):
        ArtifactTurnInput.model_validate(TURN | fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"baseEditVersion": True},
        {"editVersion": 3},
        {"outline": None},
        {"count": {"mode": "auto", "value": None}},
        {"count": {"mode": "target"}},
        {"count": {"mode": "exact", "value": 2.2}},
        {"options": {"type": "report"}},
        {"options": {"scope": None}},
        {"options": {"web": "false"}},
        {"outline": [row("a", "\nA heading")]},
        {"outline": [row("a", " ")]},
        {"outline": [row("a", "a" * 161)]},
        {"outline": [row("a", notes="n" * 401)]},
        {"outline": [row("a", goal="g" * 401)]},
        {"outline": [row("a" * 65)]},
        {"emphasize": [{"id": "", "text": "a"}]},
        {"emphasize": [{"id": "a", "text": "a"}], "avoid": [{"id": "a", "text": "b"}]},
        {"avoid": [{"id": "a", "text": "a" * 301}]},
        {"coverage": [{"id": "a", "text": " "}]},
        {"coverage": [{"id": f"topic_{index}", "text": "topic"} for index in range(9)]},
    ],
)
def test_patch_rejects_malformed_fields_before_mutating(fields):
    with pytest.raises(ValidationError):
        WorkPlanPatchInput.model_validate({"baseEditVersion": 3} | fields)


def test_reviewed_outlined_payload_is_directly_approvable_after_legal_edits():
    brief, work = ConversationBrief(), ready_work()
    revision = plan_revision(work, brief)
    edit = patch(
        brief,
        work,
        outline=[
            row("row_a", "Corrected heading"),
            row("row_b", "Limitations", notes="Compare to the original estimate."),
        ],
        options={"length": "long", "title": "New title", "focus": "Longer focus " * 50},
        emphasize=[{"id": "pref_a", "text": "  Quantify the uncertainty  "}],
    )
    assert edit.changed and edit.edit_version == 4 and not edit.needs_material
    plan = project_work_plan(edit.work, edit.brief, material=MATERIAL, availability=AVAILABLE)
    assert plan.plan_status == "ready" and plan.basis == "user_edited" and plan.blocker is None
    assert plan.plan_revision.startswith("p4-")
    assert plan.proposal.title == "New title" and plan.proposal.count.value == 2
    assert [(part.id, part.notes) for part in plan.outline] == [
        ("row_a", ""),
        ("row_b", "Compare to the original estimate."),
    ]
    assert plan.outline[0].edited_fields == ["heading"] and not edit.work.outline[0].refs
    assert plan.model_dump(by_alias=True)["outline"][0]["layout"] is None
    assert plan.preferences.emphasize[0].mark == "edited"
    spec = spec_of(edit.brief, edit.work)
    assert plan.options.focus in spec.brief and len(plan.options.focus) > 300
    assert spec.emphasize == ("Quantify the uncertainty",)
    assert can_approve_work(edit.work, edit.brief, revision=plan.plan_revision, material=MATERIAL)
    assert not can_approve_work(edit.work, edit.brief, revision=revision, material=MATERIAL)
    assert work.outline[0].heading == "Main results" and not brief.emphasize


@pytest.mark.parametrize("type_id", ["report", "briefing", "slides"])
def test_outline_edit_keeps_the_catalog_first_part_identity(type_id):
    work = ready_work(type_id)
    cover = "title" if type_id == "slides" else None
    work.outline = [
        ItemRec(id="first", heading="Opening", layout=cover or ""),
        ItemRec(id="body", heading="Evidence"),
    ]
    with pytest.raises(PlanValidationError) as error:
        patch(ConversationBrief(), work, outline=[row("body", layout=cover), row("first")])
    assert error.value.detail[0]["loc"] == ["outline", 0, "id"]
    assert error.value.detail[0]["rowId"] == "first"


def test_ready_direct_payload_keeps_exact_count_until_reset_to_auto():
    brief, work = ConversationBrief(), ready_work("flashcards")
    assert project_work_plan(work, brief, material=MATERIAL).proposal.count.value == 20
    edit = patch(
        brief,
        work,
        count={"mode": "exact", "value": 12},
        coverage=[{"id": "topic_b", "text": "Tradeoffs"}, {"id": "topic_a", "text": "Results"}],
    )
    edit = patch(edit.brief, edit.work, options={"length": "long"}, version=4)
    plan = project_work_plan(edit.work, edit.brief, material=MATERIAL)
    assert plan.outline is None and plan.plan_status == "ready"
    assert plan.proposal.count.model_dump() == {"mode": "exact", "value": 12}
    assert [topic.id for topic in plan.proposal.coverage] == ["topic_b", "topic_a"]
    assert edit.work.plan.coverage_edited
    reset = patch(edit.brief, edit.work, count={"mode": "auto"}, version=5)
    assert reset.work.plan.proposal.count.model_dump() == {"mode": "auto"}
    plan = project_work_plan(reset.work, reset.brief, material=MATERIAL)
    assert plan.proposal.count.model_dump() == {"mode": "auto", "value": 40}


def test_scope_sets_equal_fields_and_attempt_updates_leave_revision_unchanged():
    brief, work = ConversationBrief(), ready_work()
    start = plan_revision(work, brief)
    work.studio_progress = {"story_key": "abc"}
    work.plan.updated_at = "newer"
    work.outline[0].design_attempted = True
    work.spec.scope.source_ids.reverse()
    assert plan_revision(work, brief) == start
    noop = patch(brief, work, options={"scope": SCOPE}, outline=[row("row_a", "Main results")])
    assert noop.work is work and noop.edit_version == 3 and not noop.changed
    edit = patch(brief, work, options={"title": "A different title"})
    revert = patch(edit.brief, edit.work, options={"title": None}, version=4)
    assert plan_revision(revert.work, revert.brief) == "p5-" + start.split("-", 1)[1]
    with pytest.raises(PlanConflictError) as conflict:
        apply_work_plan_patch(brief, work, WorkPlanPatchInput(base_edit_version=2), edit_version=3)
    assert conflict.value.code == "edit_conflict"


def test_scope_and_web_enable_require_new_material_but_a_zero_hit_web_pass_counts():
    brief, work = ConversationBrief(), ready_work()
    scope_edit = patch(brief, work, options={"scope": SCOPE | {"sourceIds": ["source_c"]}})
    assert scope_edit.needs_material and scope_edit.work.plan.reviewed_material_key is None
    assert project_work_plan(scope_edit.work, brief, material=MATERIAL).plan_status == "planning"
    web_edit = patch(brief, work, options={"web": True})
    assert web_edit.needs_material
    work = web_edit.work
    work.plan.reviewed_material_key = "web-key"
    unresolved = ReviewedMaterial("web-key", frozenset({1}))
    assert project_work_plan(work, brief, material=unresolved).plan_status == "planning"
    resolved = ReviewedMaterial("web-key", frozenset({1}), web_completed=True)
    assert project_work_plan(work, brief, material=resolved).plan_status == "ready"
    assert (
        project_work_plan(work, brief, material=ReviewedMaterial("changed-key")).plan_status
        == "planning"
    )


@pytest.mark.parametrize("explicit_web_ref", [False, True])
def test_web_off_retains_local_snapshot_and_reports_unresolved_bindings(explicit_web_ref):
    brief, work = ConversationBrief(), ready_work(web=True)
    work.plan.reviewed_material_key = "web-key"
    work.cards = [CardRec(id="card_a", claim="Evidence", refs=[1, 2])]
    work.approach = {"refs": [1, 2]}
    work.outline[0].refs = [2] if explicit_web_ref else [1]
    local = ReviewedMaterial("local-key", frozenset({1}), progress={"pool_refs": [1]})
    edit = apply_work_plan_patch(
        brief,
        work,
        WorkPlanPatchInput(base_edit_version=3, options={"web": False}),
        edit_version=3,
        local_material=local,
    )
    plan = project_work_plan(edit.work, edit.brief, material=local)
    assert edit.work.studio_progress == {"pool_refs": [1]}
    assert edit.work.plan.reviewed_material_key == "local-key"
    assert edit.work.approach == {"refs": [1]} and edit.work.cards[0].refs == [1]
    if explicit_web_ref:
        assert plan.plan_status == "planning" and "row_a" in plan.blocker
    else:
        assert plan.plan_status == "ready" and plan.blocker is None


def test_structural_and_provider_errors_have_field_locations():
    brief, book = ConversationBrief(), ready_work()
    book.outline.append(ItemRec(id="row_b", heading="Evidence", requires=["row_a"]))
    with pytest.raises(PlanValidationError) as missing:
        patch(brief, book, outline=[row("row_b", "Evidence")])
    assert missing.value.detail[0]["loc"] == ["outline", 0, "requires"]
    assert missing.value.detail[0]["rowId"] == "row_b"
    slides = ready_work("slides")
    with pytest.raises(PlanValidationError) as invalid_layout:
        patch(brief, slides, outline=[row("row_a", layout="points"), row("cover", layout="title")])
    assert invalid_layout.value.detail[1]["rowId"] == "cover"
    with pytest.raises(PlanValidationError) as audio_minimum:
        patch(brief, ready_work("audio_overview"), count={"mode": "exact", "value": 1})
    assert audio_minimum.value.detail[0]["loc"] == ["count", "value"]
    with pytest.raises(PlanValidationError) as wrong_model:
        patch(brief, book, options={"model": "missing/model"})
    assert wrong_model.value.detail[0]["loc"] == ["options", "model"]
    with pytest.raises(PlanValidationError) as wrong_voices:
        patch(brief, ready_work("audio_overview"), options={"voices": ["voice-a", "missing"]})
    assert wrong_voices.value.detail[0]["loc"] == ["options", "voices"]


def test_status_precedence_and_failed_approval_eligibility():
    brief, work = ConversationBrief(), ready_work()
    question = Pending(question="Who is this for?", options=["Readers"], message_id="msg_1")
    work.plan.attempt = PlanAttemptRecord(
        request_id="request_123",
        server_boot_id="boot_1",
        attempt_id="attempt_1",
        phase="generate",
        state="running",
    )
    plan = project_work_plan(work, brief, pending=question, material=MATERIAL, attempt_is_live=True)
    assert plan.plan_status == "generating"
    blocker = project_work_plan(
        work, brief, pending=question, material=MATERIAL, attempt_is_live=False,
    )
    assert blocker.plan_status == "needs_answer" and blocker.pending.message_id == "msg_1"
    assert blocker.attempt.state == "interrupted" and work.plan.attempt.state == "running"
    with pytest.raises(PlanConflictError) as active:
        apply_work_plan_patch(
            brief,
            work,
            WorkPlanPatchInput(base_edit_version=3, avoid=[{"id": "x", "text": "Example"}]),
            edit_version=3,
            material=MATERIAL,
            attempt_is_live=True,
        )
    assert active.value.code == "run_active"
    work.plan.attempt.state = "failed"
    status = project_studio_work_summary(work, brief, updated_at="today", material=MATERIAL)
    assert status.status == "failed"
    assert can_approve_work(work, brief, revision=status.plan_revision, material=MATERIAL)
    assert not can_approve_work(work, brief, revision=status.plan_revision, material=None)
    work.key, work.version_id = "lineage_1", "version_1"
    status = project_studio_work_summary(work, brief, updated_at="today", pending=question)
    assert (
        status.status == "saved"
        and status.plan_id == "draft_123"
        and status.lineage_id == "lineage_1"
    )


def test_preference_merge_protects_concurrent_order_moves_deletions_and_clear():
    base = edit_preferences(
        ConversationBrief(),
        emphasize=[
            PlanLineInput(id="pref_a", text="Quantify evidence"),
            PlanLineInput(id="pref_b", text="Compare models"),
            PlanLineInput(id="pref_c", text="List outliers"),
        ],
        avoid=[PlanLineInput(id="pref_d", text="Acronyms")],
    )
    mine = apply_brief_updates(
        base,
        None,
        [
            BriefUpdate(field="emphasize", text="Quantify evidence"),
            BriefUpdate(field="emphasize", text="Use plain labels", quote="Use plain labels"),
            BriefUpdate(field="avoid", text="High-level discussion"),
        ],
        "Use plain labels",
    ).brief
    theirs = edit_preferences(base, emphasize=[base.emphasize[2], base.emphasize[1]], avoid=[])
    merged = keep_edits(base, mine, theirs)
    assert [line.id for line in merged.emphasize[:2]] == ["pref_c", "pref_b"]
    assert [line.mark for line in merged.emphasize] == ["edited", "edited", "you"]
    assert not merged.avoid
    moved = edit_preferences(base, emphasize=base.emphasize[1:], avoid=[base.emphasize[0]])
    merged = keep_edits(base, mine, moved)
    assert [line.id for line in merged.avoid] == ["pref_a", mine.avoid[-1].id]
    assert "pref_a" not in [line.id for line in merged.emphasize]


def test_legacy_state_is_converted_without_rewriting_its_input():
    raw = {
        "active": "lineage_1",
        "pending": {"question": "Which?", "options": [], "message_id": "msg_1"},
        "works": [
            {
                "key": "lineage_1",
                "plan": {"id": "draft_1"},
                "spec": {"kind": "report"},
                "outline": [{"id": "row_1", "heading": "Opening", "refs": [1]}],
                "approach": {
                    "summary": "Existing work",
                    "studio_progress": {"material_key": "old"},
                },
            }
        ],
    }
    original = deepcopy(raw)
    workspace = load_workspace(json.dumps(raw))
    assert (
        workspace.edit_version == 0 and workspace.pending_by_target["draft_1"].message_id == "msg_1"
    )
    assert workspace.works[0].studio_progress == {"material_key": "old"}
    assert workspace.works[0].outline[0].required_refs == []
    encoded = json.loads(dump_workspace(workspace))
    assert "pending" not in encoded and "studio_progress" not in encoded["works"][0]["approach"]
    assert encoded["works"][0]["outline"][0]["refs"] == [1]
    assert raw == original and load_brief('{"goal":{"text":"existing"}}').avoid == []
    workspace.pending = None
    assert workspace.pending_by_target == {}


def test_saved_rows_have_version_scoped_source_ids_and_keep_their_layout():
    version = ArtifactOut.model_construct(
        id="version_1",
        kind="slides",
        content="# Title\n\n---\n\n## Body\nText",
        outline=[
            OutlineItem(heading="Title", layout="title"),
            OutlineItem(id="row_saved", heading="Body", layout="takeaways", notes="A note"),
        ],
    )
    rows = project_saved_outline(version)
    assert rows == project_saved_outline(version)
    assert rows[0].id != project_saved_outline(version.model_copy(update={"id": "version_2"}))[0].id
    assert rows[1].id == "row_saved" and rows[1].notes == "A note"
    work = ready_work("slides")
    work.version_id = version.id
    work.outline = [ItemRec(id=part.id, heading=part.heading, layout=part.layout) for part in rows]
    edits = [
        PlanPartEditInput.model_validate(row(part.id, part.heading, part.layout)) for part in rows
    ]
    assert normalize_plan_outline(work, edits)[1].layout == "takeaways"


def test_preference_language_and_writer_notes_invalidate_conditioned_stages():
    brief, work = ConversationBrief(), ready_work()
    item = work.outline[0]
    initial = (
        digest_approach_inputs(brief, work),
        digest_outline_inputs(work, brief),
        digest_part_inputs(brief, work, item),
    )
    edited = edit_preferences(brief, avoid=[PlanLineInput(id="pref_a", text="Shorthand")])
    current = (
        digest_approach_inputs(edited, work),
        digest_outline_inputs(work, edited),
        digest_part_inputs(edited, work, item),
    )
    assert all(a != b for a, b in zip(initial, current, strict=True))
    work.spec.language = "Norwegian"
    assert initial[0] != digest_approach_inputs(brief, work)
    assert initial[1] != digest_outline_inputs(work, brief)
    part_before = digest_part_inputs(brief, work, item)
    item.notes = "Mention both ranges."
    assert part_before != digest_part_inputs(brief, work, item)
