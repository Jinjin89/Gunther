"""Designed slides: a style for the deck, then every slide drawn as HTML by the model, and a
fact guard that makes sure the drawing changed no fact.

The slide's Markdown stays the content spec (the verified words, numbers, citations and notes).
The design is one more block at its end:

    :::design
    <section class="slide">…</section>
    :::

A slide without that block is drawn by the template renderer (old decks, and any slide whose
design failed the guard twice). See docs/DESIGNED_SLIDES_PLAN.md.
"""

from __future__ import annotations

import html as htmllib
import logging
import re
import unicodedata
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from gunther.llm import ModelError
from gunther.studio import format as fmt
from gunther.studio import prompts as P

logger = logging.getLogger(__name__)

STYLES_DIR = Path(__file__).resolve().parent / "styles"
PLACEHOLDER = "<gunther-chart></gunther-chart>"
NOTE_LINE = re.compile(r"^\s*(?:Note|Notes|备注|讲者备注)\s*[:：]")
CITATION = re.compile(r"\[(?:\?|\d+(?:\s*[,，]\s*\d+)*)\]")
# A number, with its sign only when the sign stands alone ("GPT-4" and "2021-2023" hold no signs).
NUMBER = re.compile(r"(?:(?<![\w.])[-+−])?(?<![\d.,，])\d[\d,，.]*")
LIST_NUMBER = re.compile(r"(?m)^\s*\d+[.)]\s+")
INLINE_TAG = re.compile(
    r"</?(?:span|b|strong|i|em|sup|sub|u|small|mark|a|tspan|code)\b[^>]*>", re.I
)
CHART_TAG = re.compile(r"<gunther-chart\b[^>]*>\s*</gunther-chart>", re.I)
MAX_HTML = 60_000

# What a designed slide must never hold (the app sanitises it again before showing it).
UNSAFE = (
    (re.compile(r"<\s*script", re.I), "a <script> element"),
    (re.compile(r"\son[a-z]+\s*=", re.I), "an on… event attribute"),
    (re.compile(r"javascript\s*:", re.I), "a javascript: address"),
    (re.compile(r"https?://|(?<![\w:])//[\w.-]+\.\w", re.I), "an external web address"),
    (
        re.compile(r"<\s*(?:img|link|iframe|object|embed|base|form|style)\b", re.I),
        "a forbidden element",
    ),
    (re.compile(r"@import|expression\s*\(", re.I), "an @import or expression()"),
    (
        re.compile(r"""\s(?:href|src|action|formaction)\s*=\s*(?:["']\s*(?!#)|(?!["']|#))""", re.I),
        "a link or source",
    ),
    (re.compile(r"<\s*!?\s*\[?\s*CDATA|<\s*foreignObject", re.I), "an embedded document"),
)
# The one web address an inline SVG legitimately carries.
SVG_NAMESPACE = re.compile(r"""\sxmlns(?::\w+)?\s*=\s*["']http://www\.w3\.org/[^"']*["']""", re.I)


class StylePick(BaseModel):
    style: str
    why: str = ""


def load_style(name: str) -> tuple[str, str]:
    """A style's guide (Markdown with its CSS) and the base CSS every style shares."""

    name = name if name in fmt.STYLES else "light"
    return (STYLES_DIR / f"{name}.md").read_text(encoding="utf-8"), (
        STYLES_DIR / "base.css"
    ).read_text(encoding="utf-8")


# Choosing the style ------------------------------------------------------------------------


def pick_style(agents, model, *, title: str, subtitle: str, brief: str, audience: str) -> str:
    """One short call: light, dark or bold, from the title, the brief and the audience. A
    reply that is not one of them (or no reply) is light."""

    prompt = (
        f"Deck title: {title}\nSubtitle: {subtitle}\nBrief: {brief or '(none)'}\n"
        f"Audience: {audience}"
    )
    try:
        pick = agents._json(model, StylePick, P.STYLE_PICK, prompt, "low")
    except ModelError as error:
        logger.info("Choosing a style failed: %s", error)
        return "light"
    style = pick.style.strip().lower()
    return style if style in fmt.STYLES else "light"


# What the designer is shown, and what it may keep -------------------------------------------


def visible_content(slide: str) -> str:
    """A slide as the designer sees it: no marker, no speaker notes, no design."""

    text = fmt.MARK.sub("", fmt.without_design(slide), count=1)
    kept: list[str] = []
    for line in text.split("\n"):
        if NOTE_LINE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def designable(slide: str) -> bool:
    """A slide with a picture from the material is drawn by the template: a design may not
    carry images."""

    return not any(fmt.IMAGE.match(line.strip()) for line in visible_content(slide).split("\n"))


def extract_section(reply: str) -> str:
    """The first <section>…</section> of a reply, made safe to keep inside the slide's
    Markdown: no code fences, no line that is a slide break or a block end."""

    text = re.sub(r"```(?:html)?", "", reply)
    found = re.search(r"<section\b.*</section>", text, re.S | re.I)
    if not found:
        return ""
    lines = [
        line.rstrip()
        for line in found.group(0).split("\n")
        if not re.fullmatch(r"\s*-{3,}\s*", line) and not line.lstrip().startswith(":::")
    ]
    return "\n".join(lines).strip()


# The fact guard ----------------------------------------------------------------------------


def numbers(text: str) -> set[str]:
    """The numbers a text states: not its citation marks or its list numbering."""

    text = LIST_NUMBER.sub("", CITATION.sub("", text))
    found = (fmt.normal_number(m.group().rstrip(".,，")) for m in NUMBER.finditer(text))
    return {n for n in found if n}


def refs(text: str) -> set[int]:
    return {int(n) for mark in CITATION.findall(text) for n in re.findall(r"\d+", mark)}


def words(text: str) -> str:
    """Letters and digits only, in lower case: titles compare regardless of case, spacing and
    punctuation."""

    plain = CITATION.sub("", unicodedata.normalize("NFKC", text))
    return "".join(ch.lower() for ch in plain if ch.isalnum())


def visible_text(html: str) -> str:
    """The text a viewer reads: tags removed (the chart placeholder too), entities decoded."""

    html = re.sub(r"<!--.*?-->|<style\b.*?</style>", " ", html, flags=re.S | re.I)
    html = INLINE_TAG.sub("", CHART_TAG.sub(" ", html))
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", html))


def check_design(slide: str, html: str) -> list[str]:
    """What is wrong with a design, as findings the designer can act on. Empty: it changes
    no fact. ``slide`` is the verified slide (Markdown), ``html`` the design."""

    problems: list[str] = []
    content = visible_content(slide)
    parsed = fmt.parse_slide(slide)
    chart = parsed.layout == "chart"
    table = "\n".join(line for line in content.split("\n") if line.startswith("|")) if chart else ""
    rest = "\n".join(line for line in content.split("\n") if not (chart and line.startswith("|")))
    # The chart draws its table's values and citations; they need no copy on the slide.
    drawn_numbers = numbers(table) - numbers(rest)
    drawn_refs = refs(table) - refs(rest)

    if not re.match(r"<section\b[^>]*\bclass=[\"'][^\"']*\bslide\b", html, re.I):
        problems.append('The slide must be one <section class="slide">.')
    if len(html) > MAX_HTML:
        problems.append("The slide's HTML is far too long: keep it simple.")
    scrubbed = SVG_NAMESPACE.sub("", html)
    problems += [
        f"The HTML holds {what}: remove it." for found, what in UNSAFE if found.search(scrubbed)
    ]

    text = visible_text(html)
    want, got, allowed = numbers(content) - drawn_numbers, numbers(text), numbers(content)
    if missing := sorted(want - got):
        problems.append("These numbers from the content are missing: " + ", ".join(missing))
    if extra := sorted(n for n in got - allowed if not re.fullmatch(r"[1-9]", n)):
        problems.append("These numbers are not in the content (remove them): " + ", ".join(extra))
    shown = refs(text)
    if lost := sorted(refs(content) - drawn_refs - shown):
        problems.append("These citations are missing: " + ", ".join(f"[{n}]" for n in lost))
    if added := sorted(shown - refs(content)):
        problems.append(
            "These citations are not in the content: " + ", ".join(f"[{n}]" for n in added)
        )
    if "[?]" in content and "[?]" not in text:
        problems.append("The [?] mark of the content is missing.")
    placeholders = len(re.findall(r"<gunther-chart\b", html, re.I))
    if chart and (placeholders != 1 or len(CHART_TAG.findall(html)) != 1):
        problems.append(
            f"The slide needs exactly one {PLACEHOLDER} (it has {placeholders}): the chart is "
            "drawn there."
        )
    if not chart and placeholders:
        problems.append(f"This slide has no chart table: remove the {PLACEHOLDER}.")
    if parsed.title and words(parsed.title) not in words(text):
        problems.append(f'The title "{parsed.title}" must appear with its words unchanged.')
    return problems


# Designing ---------------------------------------------------------------------------------


def design_slides(
    agents, model, slides: Sequence[str], style: str, *, workers: int
) -> tuple[list[str], int, list[dict[str, str]]]:
    """Every slide designed on its own, all at once. Back: the slides (each that passed the
    guard now ends with its design block), how many were designed, and one note for each slide
    that was not (it is drawn by the template)."""

    from gunther.studio.pipeline import _in_context

    guide, base = load_style(style)
    parsed = [fmt.parse_slide(slide) for slide in slides]
    outline = "\n".join(
        f"{n}. [{p.layout}] {p.title or '(cover)'}" for n, p in enumerate(parsed, 1)
    )

    def design(index: int) -> tuple[str, str]:
        """The slide's design and the findings that kept it from having one."""

        slide = slides[index]
        if not designable(slide):
            return "", ""
        system = P.DESIGN.format(
            style=guide,
            base=base,
            outline=outline,
            number=index + 1,
            total=len(slides),
            layout=parsed[index].layout,
            content=visible_content(slide),
        )
        problems: list[str] = []
        for _ in range(2):
            prompt = "Design the slide now." + (
                P.DESIGN_AGAIN.format(problems="\n".join(f"- {p}" for p in problems))
                if problems
                else ""
            )
            try:
                reply = agents._text(model, system, prompt, "medium", 16_000)
            except ModelError as error:
                logger.info("Designing slide %d failed: %s", index + 1, error)
                problems = [f"The model could not answer: {error}"]
                continue
            html = extract_section(reply)
            problems = check_design(slide, html) if html else ["The reply held no <section>."]
            if not problems:
                return html, ""
        return "", "; ".join(problems)

    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(_in_context(design), range(len(slides))))
    out: list[str] = []
    notes: list[dict[str, str]] = []
    for index, (slide, (html, why)) in enumerate(zip(slides, results, strict=True)):
        out.append(fmt.with_design(slide, html) if html else slide)
        if why:
            notes.append(
                {
                    "step": "design",
                    "label": f"Slide {index + 1} drawn plainly",
                    "reason": why[:600],
                }
            )
    designed = sum(1 for html, _ in results if html)
    return out, designed, notes


def apply_style(slides: list[str], style: str) -> list[str]:
    """The title slide's marker records the deck's style (the app reads it from there)."""

    first = fmt.parse_slide(slides[0])
    attrs: dict[str, Any] = {**first.attrs, "style": style}
    return [fmt.with_marker(slides[0], first.layout or "title", **attrs), *slides[1:]]
