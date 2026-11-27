"""The default Report retains the planning, writing and review text from before the catalog."""

import json

from gunther.output_types import output_type_for
from gunther.outputs import Spec
from gunther.studio import prompts
from gunther.studio.requests import material_request

ORIGINAL_REPORT_STORY = """\
You are the editor of a top consulting firm's research practice, planning a written report \
for: {audience}. Brief: {brief}

Default length: {pages}.

How great reports are planned:
- Answer first. The executive summary states the answer and the 3–5 findings that support \
it, each with its key number.
- Sections follow the argument, not the material: each section heading is a claim (a full \
sentence the reader could repeat), and the headings read in order are the argument.
- Each section proves its claim: strongest evidence first, one concrete case, the limits \
that matter, what it means for the answer.
- Plan exhibits (a chart or a table) wherever numbers are compared: at least one in any \
section that rests on numbers. An exhibit has a caption that states what it shows.
- End with implications or recommendations that follow from the sections, and open \
questions if they matter.

The analyst's memo:
{memo}

Reply with a JSON object:
{{"title": "report title", "summary_heading": "the answer as a heading",
 "requested_count": null, "requested_minutes": null,
 "summary_id": null, "summary_job": "what the summary must deliver",
 "summary_points": ["finding with number"],
 "sections": [{{"heading": "claim", "id": null, "job": "what it proves and with which evidence", \
"exhibits": ["what each exhibit shows"], "refs": [n]}}]}}"""

ORIGINAL_REPORT_WRITE = """\
You are the best writer at a top consulting firm's research practice. Write the report \
below in Gunther's report format. The plan is agreed: keep its sections, order and \
headings (you may sharpen wording).

Brief: {brief}
Audience: {audience}.

Craft:
- Executive summary first: one short paragraph with the answer, then the findings as \
bullets "**finding**: detail [n]", each with its key number.
- Each section: the first sentence states the section's claim and links it to the one \
before. Then the strongest evidence, with numbers and what they are compared to; one \
concrete case (who, where, what happened); the limit that matters; what it means for the \
answer. One idea per paragraph; the paragraph's first sentence is its point.
- Exhibits: every comparison of numbers goes in an exhibit (chart or table) with a \
caption that states what it shows and a Source line; the text then interprets it, not \
repeats it. Number exhibits in order. A typical report has 2–4 exhibits; a report that \
compares numbers without any exhibit is not finished.
- Headings: one claim each, ideally 6–12 words. The summary bullets' bold leads are the \
findings themselves.
- Use a callout only for the one thing a skimming reader must not miss in a section; at \
most one per section, often none.
- End with implications or recommendations that follow from the sections; no repeated \
summary.
- {length}

{language}

{format}

The analyst's memo:
{memo}

The plan:
{story}

Write the whole report now, starting with the line <!-- report: v2 --> and then "# title". \
Output only the report."""

ORIGINAL_CRITIC = """\
You are a demanding senior partner reviewing a storyline before any slide is written. You \
have seen thousands of decks and reports; you reject anything generic.

Brief: {brief}
Audience: {audience}

Do two things.
1. Read only the titles below, in order, as the audience would. Write in two sentences \
what you understood the core message to be. The intended answer is: {answer} — if your \
retelling misses any part of it, above all the decision or recommendation, the storyline is \
not ready: say which titles must carry the missing part.
2. Find what would make this fail in front of the audience: titles that are topics or \
vague, titles with no specific (number, name, comparison), slides that repeat each other, \
gaps or jumps in the logic, the answer coming too late, weak opening, weak ending, a \
layout that does not fit the evidence, too many or too few slides for the length, \
anything generic or obvious to this audience, anything that sounds like a disclaimer or \
talks about "the material" instead of the world.

Reply with a JSON object:
{{"understood": "...", "verdict": "ready" or "revise",
 "problems": [{{"where": "slide/section number or 'overall'", "problem": "...", "fix": "..."}}]}}
Say "ready" only if a top firm would present it as planned.

The storyline:
{story}"""

def test_default_report_prompt_text():
    report = output_type_for("report")
    brief = {"brief": "Compare trial results", "audience": "a research team"}
    planning = {**brief, "pages": "four sections", "memo": "The trial reduced energy use."}
    original_story = ORIGINAL_REPORT_STORY.format(**planning)
    assert prompts.REPORT_STORY.format(**planning, recipe=report.plan) == original_story

    writing = {
        **brief,
        "length": "Write four sections.",
        "language": "Write in English.",
        "format": "Gunther's report format.",
        "memo": planning["memo"],
        "story": "The trial cut cooling use.",
    }
    assert prompts.REPORT_WRITE.format(**writing, recipe=report.write) == (
        ORIGINAL_REPORT_WRITE.format(**writing)
    )

    review = {**brief, "answer": planning["memo"], "story": writing["story"]}
    original_critic = ORIGINAL_CRITIC.format(**review)
    original_plan = original_story.split("How great reports are planned:\n", 1)[1].split(
        "\n\nThe analyst's memo:", 1,
    )[0]
    expected_critic = original_critic.replace(
        "\n\nReply with a JSON object:",
        f"\n\n{original_plan}\n\nReply with a JSON object:",
        1,
    )
    assert prompts.CRITIC.format(**review, recipe=f"\n\n{report.plan}") == expected_critic
    assert prompts.CRITIC.format(**review, recipe=output_type_for("slides").plan) == original_critic


def test_preferences_are_ordered_json_in_the_task_and_leave_evidence_unchanged():
    spec = Spec("report", "student", emphasize=("Prior \"result\"", "Details"), avoid=("猜测",))
    evidence = "[1] A passage."
    system, prompt = material_request(evidence, "Draft the report.", spec)
    material, task = prompt.split(prompts.TASK_BOUNDARY)
    instruction, preferences = task.split("\n\nOutput preferences:\n")
    assert system == prompts.STUDIO_BASE
    assert material == f"The material:\n\n{evidence}"
    assert instruction == "Draft the report."
    assert json.loads(preferences) == {
        "emphasize": ['Prior "result"', "Details"], "avoid": ["猜测"],
    }
