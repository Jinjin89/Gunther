# Presentation structure research for Gunther

Research date: 18 October 2026. Companion to [Public slide skills and improvements for Gunther](PRESENTATION_SKILLS_RESEARCH.md).

The concrete implementation sequence is in [Gunther slide improvement plan](PRESENTATION_IMPROVEMENT_PLAN.md).

Improve the reasoning plan before expanding the slide templates. Gunther already asks for a core question, an answer, evidence cards, a story pattern, slide goals, and transitions. Its next useful capability is to evaluate whether the selected evidence and slide sequence actually establish the intended message for the audience.

This can use the current reveal.js renderer. The recommendations below combine primary communication guidance, public skill implementations, and inspection of the current working tree. They are proposals to evaluate, not a measured comparison with a proprietary remote planner.

## Three levels of structure

| Level | Main question | Planning result |
| --- | --- | --- |
| Whole deck | What should this audience understand, decide, or be able to do? | A central question, an evidence-supported answer or open issue, and the reasoning needed to reach it. |
| Sections and slide sequence | Which questions must be answered, and what needs explaining first? | Optional meaningful groups, slide messages, prerequisites, transitions, and time allocation. |
| Individual slide | What does this page establish, and how does its content establish it? | One communicative purpose, the necessary evidence or explanation, and a suitable visual arrangement. |

A source document's structure and the deck's structure have different roles. Recover and retain the source's actual organization for understanding and provenance. Then choose a presentation sequence that fits the audience. Neither step requires a standard paper outline or a fixed set of presentation sections.

## Primary guidance

| Source | Relevant guidance | Application and limits |
| --- | --- | --- |
| [MIT NSE Communication Lab](https://mitcommlab.mit.edu/nse/commkit/structuring-a-slide-presentation/) | Plan from the results and the messages they support, group evidence by those points, inspect gaps in the argument, then build a slide list. Budget against time and audience understanding. | Especially useful for technical material. Adapt its scientific-talk structure rather than making it compulsory for every document. |
| [MIT Mechanical Engineering Communication Lab](https://mitcommlab.mit.edu/meche/commkit/technical-presentation/) | Purpose changes emphasis: a results talk and a request for feedback on methods need different structures. Conclusion timing and title style also depend on the audience. Prepare listeners to interpret data and anticipate their questions. | Supports conditional planning instead of a universal answer-first or answer-last rule. |
| [Barbara Minto's official course description](https://www.barbaraminto.com/course) | Identify the reader's question, organize ideas through vertical and horizontal relationships, and inspect whether groupings justify the insight above them. | Useful for recommendations and analytical reasoning. The course description establishes the method, not an empirical effectiveness estimate. |
| [Nancy Duarte on presentation structure](https://www.duarte.com/blog/move-presentation-audience-with-story-techniques-in-presentations/) | Define the audience's intended change and use the relationship between current circumstances and a possible future to develop a persuasive story. | A useful option for change proposals and persuasion. A hypothetical future must stay distinguishable from established results. |
| [Assertion-Evidence approach](https://www.assertion-evidence.com/) | Build technical content around concise messages supported by visual evidence. | Useful for result and evidence slides. Setup, definition, navigation, and discussion pages can have other purposes. |

These methods address different needs. A decision briefing may start with a recommendation; an explanation may first establish the concepts needed to understand a result. A literature synthesis may need to preserve unresolved disagreement. The material and audience determine which relationships belong in the story.

## What public skills add

| Public implementation | Useful mechanism | Adaptation for Gunther |
| --- | --- | --- |
| [Baoyu analysis framework](https://github.com/JimLiu/baoyu-skills/blob/main/skills/baoyu-slide-deck/references/analysis-framework.md) | Separates message hierarchy, audience questions and barriers, visual opportunities, flow, and content selection. | Add audience concerns and deliberate content selection to the existing evidence-card workflow. Retain qualifications that change a claim's meaning. |
| [Baoyu outline format](https://github.com/JimLiu/baoyu-skills/blob/main/skills/baoyu-slide-deck/references/outline-template.md) | Gives each slide a narrative goal, key content, visual direction, and layout guidance. | Make a slide's job visible before its rendering details. Gunther already has several related fields, so improve their contracts rather than duplicate them. |
| [Olito Labs narrative-first HTML skill](https://github.com/Olito-Labs/slides/blob/main/SKILL.md) | Creates a governing thought and an outline of slide assertions with supporting points before building HTML; tests the title sequence and each slide's relevance. | Add a compact argument outline before detailed composition. Treat its consulting conventions and fixed aesthetic as implementation choices. |
| [Brusdeylins presentation preparation skill](https://github.com/Brusdeylins/ppt-skill/blob/main/plugin/skills/ppt-prepare/SKILL.md) | Separates briefing, core message, storyline, slide messages, headlines, content/layout, and notes. Includes a challenge to the recommendation. | Preserve the distinction between a message and its display title. Consolidate phases into a bounded planning pass and review rather than copying its repeated confirmation gates. |
| [Dreambase deck genre reference](https://github.com/DreambaseAI/skills/blob/main/skills/dreambase-data-presentation/references/deck-genres.md) | Associates deck purposes with expected evidence, audience decisions, and likely failure modes. | Store guidance on when a narrative approach fits and fails. Its specific genre sequences, numerical rules, and third-party claims need separate verification before reuse. |

These community implementations are examples of planning mechanisms, not a ranking of popular or proven planners. Their strongest transferable feature is the separation of reasoning, slide messages, and visual construction.

## Current Gunther structure design

The [story reference](../apps/backend/gunther/skills/slides/references/story.md) offers seven patterns: `what`, `why`, `how`, `guide`, `which`, `change`, and `learn`. The understanding stage chooses one with a reason. Material cards already retain claims, references, evidence excerpts, argument roles, scope, and units.

The [planner](../apps/backend/gunther/skill_runner.py) represents each slide with a heading, goal, layout, link to the previous slide, cards, and search queries. The [workspace](../apps/backend/gunther/artifact_workspace.py) also provides stable item identities and saved design choices. This is a substantial base for better structure planning.

The main limitations are specific:

1. **One pattern is selected early.** `story.md` supplies fairly fixed sequences and makes `learn` defer the core message. Mixed purposes and different audience knowledge may require a more flexible sequence. Keep the patterns as guidance with applicability conditions.
2. **The outline combines reasoning with layout selection.** A heading and layout are produced before a separate, explicit assessment of the argument. The `goal` field partly captures a slide's purpose, but it is not independently checked as the message the evidence establishes.
3. **The representation is flat.** It does not explicitly record a slide's prerequisites, its contribution to an intermediate argument, or the concern it resolves. Optional grouping would help longer decks; shorter decks need no section hierarchy.
4. **Transition checks verify presence.** `slide_links()` reports empty fields. It does not establish that a claimed consequence or contrast follows from the material. Exact duplicate-title checks likewise cannot detect all repeated messages or reasoning gaps. Source: [outline checks](../apps/backend/gunther/skill_checks.py).
5. **An overlong outline can lose its middle.** After a repair attempt, `structure()` caps an outline by retaining the first `high - 1` items and the final item. Required evidence, a prerequisite, or a limitation can disappear without a coverage check. This is a concrete risk in the code path, not evidence that it occurs in every generated deck.
6. **Ordinary polish has limited structural repair.** It rewrites existing pages, with restrictions on changing count, order, and layout. The workspace already has insert/delete/move operations; connect structural review to those operations where a replan is appropriate. Sources: [polish and planning](../apps/backend/gunther/skill_runner.py), [workspace operations](../apps/backend/gunther/artifact_steps.py).

## Proposed planning workflow

### Establish the audience task and evidence boundaries

Extend the existing brief only where it lacks consequential information: what the audience needs to understand or decide, what they already know, what concerns must be answered, and what content the user requires. Distinguish user-provided information from inferred audience assumptions.

Retain the current question, answer, purpose, and gaps. Make the answer's status explicit when needed: supported conclusion, provisional synthesis, or unresolved question. An attractive story should not turn incomplete or conflicting evidence into certainty.

### Build the reasoning before assigning pages

Group evidence cards by the questions or propositions they address. For each proposed main point, identify support, relevant contrary evidence, qualifications, and missing assumptions. Decide which points are essential for the audience task and which belong in optional detail.

These relationships describe the presentation argument. A fact supporting a recommendation does not imply that the source established a causal relationship. A concept needing explanation before a later slide is a presentation prerequisite, not a factual dependency in the world.

Keep groups only when they express meaningful relationships. Avoid a mandatory number of pillars or a claim of exhaustive coverage when the available material is partial. Record why selected content belongs in the deck and why other content can remain outside it, while retaining the original sources.

### Compare genuinely different sequences when useful

For a substantial ambiguous brief, propose at most two candidate sequences using the same evidence and constraints. For example, one could state the recommendation early while another establishes a necessary concept first. Compare what each assumes about the audience, where it addresses concerns, and which reasoning might be hardest to follow.

Choose directly when the brief makes the sequence clear. Candidate comparison should resolve a real planning choice, not add routine model calls or additional user approvals.

### Define slide messages before display titles and layouts

Give each page a purpose in the reasoning plan. A compact extension of the existing outline is enough:

| Planning information | Purpose |
| --- | --- |
| Stable identity and optional group | Keep references intact through edits and show meaningful sections where needed. |
| Audience question and slide message | Explain what the page answers and what its content establishes. A setup page may explain a concept rather than assert a result. |
| Evidence card identities | Keep the message tied to support and necessary qualifications. |
| Prerequisites, when present | Identify what must already have been explained. |
| Transition reason | Explain why this page follows the previous one, in terms of the audience's understanding. |
| Priority and time estimate | Allocate attention and make compression decisions explicit. |

Reuse `goal`, `cards`, and `link` where they can express these meanings; add fields only for information that must be stored or checked separately. A slide's rhetorical role and visual layout should remain independent: a table can establish a limitation, compare options, or provide evidence.

Derive the display title from the message and page purpose. A factual takeaway should state only what the evidence supports. A definition or discussion question can use an appropriate topic or question title. Then select a visual expression from the renderer's actual capabilities.

### Review the outline before detailed writing

Add a bounded semantic review alongside the current mechanical checks. Have it identify specific pages and evidence cards when it finds an unsupported conclusion, an unexplained prerequisite, a repeated message, a missing required topic, an unresolved material objection, or a transition that introduces an unearned inference.

The reviewer should be able to reconstruct the proposed answer and explain why its supporting points establish it. A fluent title sequence alone is insufficient. Semantic findings remain judgments with evidence and reasons; an empty warning list is not proof of logical validity.

Apply structural fixes before page design when possible. Merging, moving, splitting, or dropping optional material should preserve stable identities where appropriate, user locks, required content, source bindings, and the intended conclusion's limitations. A structural change should mark affected drafts and designs for regeneration through the existing versioning workflow.

### Replan within the budget

Replace positional truncation with a deliberate compression plan. Merge repeated messages, combine compatible evidence, reduce optional background, or move genuinely secondary explanation to notes or an appendix when allowed. Keep limitations visible when they affect interpretation.

Honor an explicit slide total and distinguish it from a default range. Budget live presentations using estimated time and complexity as well as count; reading decks need enough on-page explanation. If required content cannot fit the constraints, preserve it in the planning state and report the conflict rather than silently removing the reasoning that supports the ending.

## Example of a structure improvement

For a presentation about this project's slide quality, a list such as “Background, Codex, Claude, Other tools, Conclusion” organizes sources by name. It leaves the presenter to explain why those topics establish a recommendation.

An alternative structure can follow the questions the project needs answered:

| Audience question | Page's job |
| --- | --- |
| What outcome are we trying to improve? | Define quality criteria and the conditions of comparison. |
| What can Gunther already do? | Establish the relevant baseline using the current implementation. |
| Where are the demonstrated gaps? | Separate observed limitations from hypotheses about the remote system. |
| Which techniques address those gaps? | Compare public mechanisms using the same criteria. |
| What tradeoffs affect the choice? | Explain evidence preservation, editability, cost, and remaining uncertainty. |
| What should we test first? | Propose a bounded experiment and define what would justify the next change. |

This is an illustration for this research topic, not a production template or a required six-slide sequence. Its improvement is an explicit relationship between the audience's questions, the evidence, and the resulting action.

## Implementation and evaluation priorities

Start by changing the planning instructions and review contract: reasoning before layout, explicit slide messages, supported transitions, content coverage, and a deliberate response to page limits. Reuse the existing cards, stable identities, goal/link fields, and workspace editing operations.

Add structured prerequisites, optional groups, and priority/time fields only after trial outlines show what needs persistence. Then connect structural repair to draft invalidation, regeneration, and the rendered-slide review proposed in the companion note.

Evaluate with varied source organizations and audience tasks. Include cases with contradictory evidence, no firm conclusion, a concept needed before a result, a fixed short count, and overlong model output. Required evidence and limitations should survive compression. A presentation can be well structured while accurately concluding that the evidence is insufficient.

Compare baseline and revised outlines using the same sources, model settings, and documented call budget, before visual styling. Ask reviewers to identify the main question, reconstruct the reasoning, flag unexplained assumptions, and assess whether the sequence fits the audience. Then check full decks for whether writing and design preserved that plan. Track added latency and calls alongside usefulness; same-model reader roleplay is a diagnostic, not independent evidence of audience comprehension.
