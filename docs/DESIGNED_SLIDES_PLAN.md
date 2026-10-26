# Designed slides: plan

Status: built 2026-10-23 (P1–P4, commits 03901e5…bc3f0a1), tested with fake models only; a real-model run and the Mac trial are pending.

This is a historical visual-design record. The current content and interaction requirements
are in [Scientific presentations that explain the study](SCIENTIFIC_PRESENTATION_WORKFLOW.md).
Source evidence, scientific explanation and legibility take precedence over decorative
visual quotas or mandatory layout variation for research presentations.

## 1. Goal

A Gunther deck should look as good as one Claude Code or Codex designs from scratch: a strong
theme, clear sections, and a visual on every slide. Every fact must stay checked.
**Correct data and the best looks are both top priority.**

What the user decided:

| Question | Decision |
|---|---|
| Fixed templates, or free design? | **Free design**: the model designs each slide as HTML |
| What "Export to PowerPoint" gives | **The design as a background picture, with titles and text as real, editable text boxes on top** |
| How the look is chosen | **Automatically, to fit the topic and audience; the user can switch styles in one click afterwards (the content stays the same)** |
| How many styles | **One "Style" menu: Light, Dark, Bold, plus the user's template if one is imported. No separate accent picker: each style has its own colours, fonts and motif** |
| Reports | **Slides first**; reports later, the same way, once slides prove it |
| Models | DeepSeek Flash only (see CLAUDE.md) |

## 2. Why today's decks look weak

Today the writer pours text into 15 fixed layouts, drawn in one plain theme
(`apps/desktop/src/outputs/deck/`). In 14 recent decks, none used a section slide and none had
an agenda, and most slides are a title plus text. The design score (Flash vision judge) sits
around 6.5/10, the lowest of all the scores, and nothing ever tuned it.

What the good tools do (sources: Anthropic's pptx skill, the frontend-slides skill, Codex's
Slides skill):
- The model designs every slide itself, as code (HTML or pptxgenjs). It does not fill
  templates.
- A design guide steers it:
  - a topic-specific palette: one dominant colour at about 60–70% of the visual weight,
    1–2 supporting tones and one sharp accent;
  - a dark/light "sandwich": dark cover and section slides, light content slides;
  - one motif repeated on every slide;
  - "every slide needs a visual element";
  - layouts that vary, so the same one never appears twice in a row;
  - big numbers at 60–72 pt;
  - no plain blue as the default, no accent lines under titles, no decorative bars.
- A curated set of styles (frontend-slides has 12 presets and 34 bold design systems), so the
  look is never generic.
- They render the slides, look at every one, and fix them.

## 3. The new pipeline

The content half stays exactly as it is: it is Gunther's strength.

```
read → memo → storyline (+critic) → write the deck (the current slide format) → check claims
     → [NEW] choose a style → [NEW] design each slide (HTML, in parallel) → [NEW] fact guard
     → [NEW] render, measure, look (vision) → redesign the weak slides → save
```

The current Markdown slide format becomes the **content spec**. It holds the verified words,
numbers, citations, speaker notes and a layout *hint*. The designer may ignore the hint. It may
not change the facts.

### 3.1 Styles (new: `apps/backend/gunther/studio/styles/`)

Three complete styles, plus the user's template, replace today's themes (Paper, Ink,
Editorial) and the accent picker:

| Style | Look | Good for |
|---|---|---|
| **Light** | White content pages, dark cover and section slides, one strong colour | Most decks: reports, research, teaching |
| **Dark** | Dark pages throughout, a bright accent, big numbers | Board and pitch decks, keynote talks |
| **Bold** | Large blocks of colour, big type, high energy | Strategy, product launches, persuasive talks |
| **Your template** (only when one is imported) | The template's colours and fonts | Company decks |

- **Each style is one file**, covering:
  - its mood and what it suits;
  - its palette as CSS variables (dominant, secondary, accent, dark background, light
    background, text, muted);
  - its fonts;
  - its motif, in words and as a CSS snippet;
  - 3–4 sample slide snippets the designer can follow.
  Adding a style later means adding one file.
- **Fonts must work offline and in Chinese:**
  - bundle open-licensed fonts (for example Inter, Source Serif, Noto Sans/Serif SC) in the
    renderer;
  - each style names a Latin font and a CJK fallback.
- **Choosing a style:**
  - when the user has imported a template, use it;
  - otherwise one short Flash call picks Light, Dark or Bold, from the brief, the audience and
    the deck's title.
- **The menu:** one "Style" menu in the app replaces "Theme" and the accent colours. Picking
  a style re-runs only the design steps.

### 3.2 Designing each slide (new step `design`, in `studio/design.py`)

- **One Flash call per slide, all slides at once.** The call gets:
  - the style file;
  - the deck outline (all titles, so slides vary and sections are known);
  - this slide's verified content;
  - the design rules.
- **It returns one `<section>` of HTML** on a fixed 1280×720 stage. It uses only the style's
  CSS variables and classes plus inline styles.
- **Deck structure the designer must produce:**
  - the cover and section dividers are dark, content slides light;
  - decks of 8 or more slides get an agenda after the cover and section dividers between
    their parts. The storyline step already groups slides; add `section` to the storyline
    schema and require it for long decks.
- **Facts are placed, not retyped:**
  - **Citations** are written as `<cite-ref n="3"></cite-ref>`. The app draws them.
  - **Charts** are `<gunther-chart ref="c1">` placeholders. The app draws them from the
    verified chart data with the existing visx charts, styled by the style's colours. The model
    never types chart numbers.
  - **Diagrams and icons:** the model may draw inline SVG (flows, cycles, layers, 2×2 grids,
    icons). Any text inside the SVG goes through the fact guard like everything else.
- **What the HTML may contain:**
  - no scripts;
  - no external URLs;
  - images only from the material (by reference, as today's `figure` layout does);
  - sanitised with DOMPurify (an established library) before display.

### 3.3 The fact guard (deterministic, no model)

For each designed slide, the visible text is pulled out of the HTML and compared with the
slide's verified content:
- every number, name and citation in the verified slide appears on the designed slide;
- no number appears that the verified slide does not have (page numbers and the agenda's
  slide numbers excepted);
- the title says the same thing (normalised text match).

A slide that fails is designed once more, with the guard's findings attached. If it fails
again, it falls back to today's template rendering of that slide, so a deck is never lost and
never wrong.

### 3.4 Render, measure, look

- **Render:** reuse the headless Chromium renderer (`gunther/slide_render.py`) to render every
  designed slide.
- **Measure:** text that overflows its box, text under 18 px, overlapping elements, and
  contrast below 4.5:1. Each is a hard problem, fixed by redesigning that slide.
- **Look:** the vision critic (`studio/look.py`) scores each slide's design against the style
  and the rules ("a visual on every slide", "no text wall", "matches the deck").
- **Redesign:** the weakest slides, at most 4, are redesigned once.

### 3.5 In the app

- **Viewer and present mode:** they show the designed slides. Each slide is a sandboxed
  `iframe` (`srcdoc`, no scripts) or a shadow root, scaled to fit.
- **Style menu:** it replaces the theme menu and the accent colours, offering Light, Dark,
  Bold and the user's template. Picking a style re-runs only the design steps, not the words
  or the claim check.
- **Edit by talking:** an edit to a slide's words goes through the claim check, then that
  slide is designed again. An edit to its look ("make this a diagram") designs that slide again
  with the request attached.
- **Old decks:** saved decks keep working through today's renderer, chosen by the content
  marker (no `design: html` marker means the old path).

### 3.6 Export

- **PDF:** print the rendered HTML stage by stage from the same Chromium renderer.
- **PowerPoint:** for each slide:
  - render a background picture with the text hidden;
  - measure each text element's box, font size, colour and weight in the browser;
  - write them with pptxgenjs as real text boxes on top of the picture, with the notes as
    speaker notes;
  - export charts as part of the picture (Anthropic keeps them native; ours stay a picture to
    keep the exact look — revisit later).

## 4. Measuring it

Add a **design bar** to the quality eval (`evals/quality`; Flash judges only):
- **Visual score ≥ 8/10** from the Flash vision judge, which sees every slide. The rubric
  gets: "a strong, consistent theme", "a visual on every slide", "clear sections", "no text
  walls", "varied layouts".
- **New hard defects:**
  - a slide with no visual element;
  - the same layout three times in a row;
  - overflow;
  - text under 18 px;
  - contrast below 4.5:1;
  - a fact-guard fallback.
- **Pairwise:** the new decks against today's template decks, blind and order-swapped.
  Target: wins or ties on ≥ 80% of decks, with no loss on evidence or correctness.
- **The existing bars must still hold:** ship rate ≥ 80%, titles-only ≥ 90%, every number
  cited.
- **Time:** the design step runs all slides at once; budget it at about 2 minutes on top of
  today's roughly 6 minutes per deck.

## 5. Phases (Opus plans; a Sonnet subagent builds each phase; Opus reviews)

| Phase | What | Done when |
|---|---|---|
| D0. Spike | The three styles (Light, Dark, Bold) plus the `design` step for one deck, outside the app (dev script, Flash). Render and look at the PNGs. | The decks clearly beat today's on a side-by-side look, judged by the user from a contact sheet |
| D1. Styles | The three styles and the template-as-style, the bundled fonts, the style picker | Each style renders a sample deck cleanly in English and Chinese |
| D2. Design step | `design.py` and the fact guard, wired into the pipeline after the claim check; fallback per slide | Unit tests: the guard catches a dropped number, an added number and a changed title |
| D3. Render and look | Measuring designed slides, the design-aware vision critic, redesigning weak slides | Overflow and contrast defects are fixed automatically on the eval |
| D4. App | Viewer, present mode, style menu, edit by talking, old decks still open | Browser smoke test: create → switch style → edit a slide → present |
| D5. Export | PDF; PowerPoint as picture plus editable text boxes | The files open on the user's Mac in PowerPoint and Keynote with the text editable |
| D6. Tune | The design bar on the eval, Flash only, until it holds | The §4 bars hold over 2 runs |

**D0 comes first and decides whether the rest is worth building.** Flash's design skill is
unknown, so prove it before any app work.

## 6. Risks

- **Flash may design poorly.** D0 tests this first. If it does, tighten the styles: more
  sample snippets, fewer free choices.
- **Text overflow in free HTML.** Mitigated by measuring and redesigning (§3.4), plus a fixed
  stage size and minimum font sizes in each style's CSS.
- **Facts drifting in the design step.** Mitigated by placeholders for citations and charts
  plus the deterministic guard (§3.3).
- **Safety of model-written HTML in the app.** Mitigated by sanitising, no scripts, a
  sandboxed frame and no network.

## 7. What the D0 spike showed (2026-10-23)

- **Decks:** 3 real decks (31 slides), designed by Flash in Light, Dark and Bold. The script
  is `/tmp/studio_dev/design/spike.py` (outside the repo).
- **Looks:** far better than the template decks, with a real theme and a visual on every
  slide; Chinese works.
- **Facts:** every slide passed the fact guard, about 1 in 4 on the second try.
- **Time:** designing a whole deck took 64–112 s, with all slides at once.
- **To fix in the build:**
  - content sits in the top two-thirds, leaving empty space;
  - decorative "bars" that look like data (they must be banned);
  - the serif "%" glyph;
  - small grey captions;
  - no section slides.
- **The user removed the API key** because of the token cost. From here on, testing uses only
  small self-written tests with fake models: no Flash runs.

## 8. Build decisions

- **Storage:** a designed slide keeps its verified Markdown (the content spec) and gains one
  block at its end, after the speaker notes:

  ```
  :::design
  <section class="slide">…</section>
  :::
  ```

  - **Citations** inside the HTML stay as plain `[3]` text right after the claim. The existing
    renumbering (`agent.renumber_citations`) and citation code then work unchanged; the app
    draws them as it does today.
  - **The deck's style** sits in the title slide's marker: `style: light|dark|bold`.
  - **A slide with no `:::design` block** is drawn by today's template renderer (old decks, and
    fallbacks).
- **Charts:** `<gunther-chart></gunther-chart>` in the HTML. The app draws the slide's parsed
  chart table there with the existing `Charts.tsx`, using the style's colours. The design
  prompt bans any chart-like shapes not drawn from data.
- **Style CSS:**
  - `apps/desktop/src/outputs/deck/styles/{base,light,dark,bold}.css` is what the app uses;
  - the backend's `gunther/studio/styles/{light,dark,bold}.md` carries the same CSS block for
    the designer;
  - a backend test checks the two copies stay identical.
- **The user's template:** applied in the app by mapping its colours and fonts onto the style
  variables (the HTML uses only variables). It needs no backend design step.
- **Fonts:** Inter and Source Serif 4 through `@fontsource` (an established, small package);
  for Chinese, the system CJK fonts (PingFang SC on Mac, Noto Sans CJK SC, Microsoft YaHei).
- **Safety:** DOMPurify sanitises the HTML (no scripts, no event handlers, no external URLs);
  the slide is drawn in a shadow root on a fixed 1280×720 stage, scaled to fit.
- **Phases:**
  - P1, backend: styles, picking a style, the design step, the guard, storage, sections in
    the storyline.
  - P2, app: the designed-slide renderer, the style menu, present mode, print, the
    render-slides entry.
  - P3: restyle and single-slide redesign actions, and the look step on designed slides.
  - P4: export (PDF, and PowerPoint as a picture plus text boxes via `html-to-image` and
    pptxgenjs).
