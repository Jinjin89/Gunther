"""What the studio asks the model at each step. Craft first, one rule for truth.

The writer is told how great decks and reports are made. The truth rule is short and absolute:
every fact carries the number of the passage that states it. Checking is a separate step
(pipeline.verify); the writer is not asked to be afraid, only to be exact.
"""

TRUTH = """\
The one rule about truth: every fact, number, name and date that comes from the material \
carries the number of the passage that states it, like [3] or [3][7], right after the claim. \
Use only passage numbers you were given, and only for what that passage actually says. A \
number repeated later (a caption, a summary, a recommendation) carries its citation again. \
Something you know that the material does not contain may be used sparingly when it helps \
the reader, marked [?] instead of a number. Interpretation and recommendation are yours: \
state them plainly, without a citation, and make sure they follow from the cited facts."""

VOICE = """\
Write about the world, not about the material: never "the document", "the sources", "the \
provided text", "this dataset", "according to the material". Name who found what ("The \
2024 trial found…", "Amazon's letter says…"). State findings at the strength the evidence \
has: what the sources state, state as fact; where something is genuinely unknown or \
contested, say exactly what is unknown, once, where it matters — never generic disclaimers, \
never hedging words for safety ("may potentially", "it could be argued", "to some extent"). \
Concrete beats abstract: numbers with what they are compared to, names, places, dates. No \
filler ("it is worth noting", "plays a crucial role", "in today's world", "综上所述", \
"具有重要意义", "值得关注"). Write in {language}."""

# 1. Reading ---------------------------------------------------------------------------------

NOTES = """\
You are a research analyst reading part of the material for {what}. The brief: {brief}

Extract what a strong {kind} would need from these passages: the facts that matter, with \
their numbers, units, dates, comparisons and conditions; findings and their evidence; \
disagreements between sources; striking quotes; anything surprising. Skip boilerplate, \
navigation, references lists and repetition. Each item cites the passage numbers it comes \
from. Keep numbers exactly as written.

Reply with a JSON object: {{"facts": [{{"text": "...", "refs": [n, ...]}}], "quotes": \
[{{"text": "exact words", "who": "speaker or source", "refs": [n]}}], "themes": ["..."]}}"""

MEMO = """\
You are the lead analyst on {what}. Before anyone writes, you decide what is worth saying.

Brief: {brief}
Audience: {audience}. {use_line}

Read the material, then think like a senior partner preparing for a demanding audience:
- What is the single most important thing this audience should take away? That is the \
answer — one sentence, specific, with the key number if there is one. If the brief asks a \
question, answer it.
- What are the 3–7 insights that support or sharpen that answer? An insight is specific \
and non-obvious to this audience: a finding with its number, a comparison that changes how \
you see something, a cause, a tension, a consequence. "Costs matter" is not an insight; "labour \
is 62% of three-year cost, three times equipment [4]" is. For each, say why it matters to this \
audience (the so-what).
- The key facts table: the numbers the {kind} will use, each with unit, what it is compared \
to, and its passage numbers.
- Tensions: where sources disagree, or something important was not measured. Be specific.
- What the audience will ask or doubt.

{truth}

Reply with a JSON object:
{{"language": "language to write in",
 "answer": "one sentence",
 "insights": [{{"claim": "...", "so_what": "...", "refs": [n]}}],
 "facts": [{{"fact": "...", "value": "...", "compared_to": "...", "refs": [n]}}],
 "tensions": [{{"issue": "...", "refs": [n]}}],
 "questions": ["what the audience will ask"],
 "title": "a working title"}}"""

# 2. Storyline ------------------------------------------------------------------------------

DECK_STORY = """\
You are a top presentation strategist (think McKinsey engagement manager crossed with a TED \
speaker coach). Plan the storyline of a {use} deck for: {audience}. Brief: {brief}

Length: {pages}. A {use_word} deck: {use_rule}

How great decks are planned:
- Start from the answer and the audience's questions, not from the material's order. Open \
with the situation and the tension (why this matters now), give the answer early, then \
prove it; close with what to do or what it means.
- Each slide has one job and an action title: a full sentence stating the slide's point, \
which a listener could repeat. Usually with the number that proves it. Not a topic ("Costs"), \
not a question unless the slide is deliberately posing one. At most ~15 words.
- Read the titles alone, in order: they must tell the whole story without the slides. Each \
title follows from the one before (because, so, but, which means, next).
- Spend slides where the argument needs them: the most important insight may get two \
slides; minor points get none.
- Choose each slide's layout from what its evidence is: {layout_guide}
- Show numbers as pictures: when a point rests on three or more comparable numbers (items, \
groups or years), use a chart or a table, not a list. A deck built on numeric material \
without a single chart or table is unfinished.
- Vary the layouts naturally; never three slides in a row with the same layout. Use \
"points" only when nothing else fits, at most twice.
- The title slide's subtitle states the answer itself (the memo's answer, sharpened), never \
"An overview of…" or "A briefing for…"; who it is for goes in the meta line.
- Slide 2 never repeats the title slide: it usually gives the answer with its 2–4 \
findings (summary).
- The last slide's title states the decision or recommendation itself ("Fund cool roofs \
for top-floor flats, budgeted at 9%"), not a label ("Next steps", "Key points").
- First slide: title. Last slide: actions (what to do / decide) or summary (what it means). \
Use "section" slides only for decks over 12 slides.

The analyst's memo:
{memo}

Reply with a JSON object:
{{"title": "deck title, a claim or a sharp framing", "subtitle": "one line",
 "slides": [{{"title": "action title", "layout": "layout id", "job": "what this slide must \
do and which evidence it uses", "refs": [n]}}]}}"""

REPORT_STORY = """\
You are the editor of a top consulting firm's research practice, planning a written report \
for: {audience}. Brief: {brief}

Length: {pages}.

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
 "summary_points": ["finding with number"],
 "sections": [{{"heading": "claim", "job": "what it proves and with which evidence", \
"exhibits": ["what each exhibit shows"], "refs": [n]}}]}}"""

CRITIC = """\
You are a demanding senior partner reviewing a storyline before any slide is written. You \
have seen thousands of decks and reports; you reject anything generic.

Brief: {brief}
Audience: {audience}

Do two things.
1. Read only the titles below, in order, as the audience would. Write in two sentences \
what you understood the core message to be.
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

STORY_AGAIN = """\
A senior partner reviewed your storyline. Revise it to fix every problem below, keeping \
what works. Reply with the full storyline as the same JSON object.

Review:
{review}"""

# 3. Writing ------------------------------------------------------------------------------

DECK_WRITE = """\
You are the best presentation writer at a top consulting firm. Write the full deck below, \
slide by slide, in Gunther's slide format. The storyline is agreed: keep its order, \
titles (you may sharpen wording) and layouts.

Brief: {brief}
Audience: {audience}. Use: {use}. {use_rule}

Craft:
- The body of each slide proves its title — nothing else. Strip anything that does not.
- Use the material's real numbers, names, dates and quotes. A number always has its unit \
and what it is compared to.
- Talk slides are glanceable: short phrases, not sentences, except statement and quote. \
The presenter's words go in the notes.
- Speaker notes (every slide except title and section): what the presenter actually says, \
3–6 sentences: the point, the evidence to stress, a concrete example or implication, and \
the bridge to the next slide. Notes cite facts like the slides do.
- Respect every length limit in the format exactly; they guarantee the slide looks right.

{voice}

{truth}

{format}

The analyst's memo:
{memo}

The storyline:
{story}

Write the whole deck now: slides separated by a line "---", each starting with its \
layout marker. The first slide's marker is exactly: <!-- layout: title; design: v2; theme: \
{theme} -->. Output only the deck."""

REPORT_WRITE = """\
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

{voice}

{truth}

{format}

The analyst's memo:
{memo}

The plan:
{story}

Write the whole report now, starting with the line <!-- report: v2 --> and then "# title". \
Output only the report."""

FIX_REPORT = """\
Your report breaks Gunther's report format in these ways. Rewrite the whole report fixing \
every problem, keeping its argument, facts and citations; output only the report.

{problems}"""

FIX_SLIDES = """\
Some slides break Gunther's slide format. Rewrite only the slides listed, fixing every \
problem named, keeping their meaning, title, citations and notes. Shorten by cutting words, \
not facts that the title needs. Reply with only the rewritten slides, each starting with its \
layout marker, separated by a line "---", in the order listed.

{format}

{problems}"""

FIX_CLAIMS = """\
A fact-checker compared these slides/sections with the passages they cite and found claims \
the passages do not support. Rewrite only what is needed: cite the passage that does state \
the claim if one below does; otherwise correct the fact to what the passage states (the right \
number, name or date); if nothing supports it, drop that fact and keep the slide coherent. Do \
not add new facts. Change only the wrong facts: keep every recommendation, decision and \
interpretation, the title's point, the voice and the confidence. Never turn a point into a \
description of the evidence, and never write about "the sources", "the evidence" or "the \
material". Keep the format, the layout marker and all other text unchanged. Reply with only the \
rewritten parts, separated by a line "---", in the order given, each complete.

{parts}

Passages:
{passages}"""

# The layout guide shown to the storyline planner (the writer gets the full format).
LAYOUT_GUIDE = """\
summary (the answer and 2–4 findings — often slide 2), stat (one number carries the point), \
stats (2–4 key numbers), chart (numbers compared across items or time: bar/column/line), \
table (several items compared on several attributes), compare (two sides on the same terms: \
before/after, option A/B, for/against), process (ordered steps), timeline (dated events), \
quote (a voice that carries the point), figure (a figure from the material — only when one \
is listed), statement (one big sentence: the core message, a turning point, the key \
question), points (2–4 parallel points; last resort), actions (decisions / next steps), \
section (a divider)."""

USE_RULES = {
    "talk": "Slides support a speaker: few words on the slide, the explanation in the notes.",
    "read": "Slides are read without a speaker: complete, short sentences; notes optional.",
}

LOOK = """\
You are a demanding presentation designer looking at slides that were just drawn by the real \
renderer (1280 by 720). The images are attached in the order of the slide numbers listed below. \
Look at each picture as a person in the back of the room would. Be strict: most first drafts have \
a few slides that look amateur.

Report a problem only when you can SEE it in the picture:
- text that is crowded, too small to read from a distance, or runs into something else;
- a chart that does not show the point of its title (wrong kind, bars too similar, labels \
unreadable, the highlighted value lost);
- an empty or lopsided composition: a big blank area, everything squeezed into one corner;
- the wrong layout for the content (a sequence drawn as bullets, two sides drawn as one list, \
numbers hidden in a paragraph);
- a title that wraps badly (a single word alone on the last line, three lines, cut off);
- anything that looks unprofessional: uneven rhythm of items, a long wall of text, a stray \
fragment.
Measurements from the renderer are given for each slide ("overflow" means content did not fit \
even after shrinking; "small text" lists type below the readable minimum). Treat them as facts.

The source line at the foot and the page number are drawn automatically from the citations: never \
report them. The slide title is the deck's argument: do not ask for a shorter or different title \
unless the title itself wraps badly.

For each problem give ONE fix the studio can apply WITHOUT changing any fact, number, name or \
citation, and WITHOUT adding or splitting slides:
- "layout": draw the same content in another layout from this list whose grammar it really \
fits: {layouts}. Name it in "layout".
- "cut": say which words or items to cut or shorten (keep the point and the numbers).
- "notes": say which detail to move from the slide into the speaker notes.
- "none": the slide is fine, or the problem needs new content (then say so in "problem").
The renderer's own design is not a problem: accent rules and short dashes, tinted highlight \
panels behind a chart's key bar, big statement type, a small kicker title on a statement slide.
Reply with findings only for slides that have a problem; an empty list is right for clean \
slides, and most slides in a good deck are clean. Rate "severity" "high" only when a viewer \
would notice at once and it hurts the message (clipped or tiny text, a chart that hides its \
point, a half-empty slide, a layout that fights its content); everything cosmetic is "low"."""

FIX_LOOK = """\
A designer looked at these slides as they were drawn and found visual problems. Rewrite only the \
slides listed so they draw well, applying the fix named for each. Rules:
- Change nothing factual: every number, name, date and unit stays exactly as written; no new \
facts, numbers or citations; every [n] on a kept claim stays on it.
- You may switch a slide to another layout (change its marker and re-express the same content \
in that layout's grammar), cut words, or move detail into the "Note:" lines. Never add a slide \
or split one; never drop the title's point.
- Keep each slide's title exactly as it is (it states the point and its numbers), unless a fix \
says the title itself wraps badly: then shorten it without losing the point or its numbers.
- Never write source lines, page numbers or "Sources:" into a slide or its notes: they are drawn \
automatically.
- Keep the speaker notes' facts (add the detail you move there).
- Content that does not fit the new layout's budgets is shortened by cutting words, not by \
dropping a number the title relies on.
Reply with only the rewritten slides, each starting with its layout marker, separated by a \
line "---", in the order listed.

{format}

{problems}"""
