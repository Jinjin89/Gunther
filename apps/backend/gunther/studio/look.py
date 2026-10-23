"""Render and look: the finished deck is drawn by the real renderer, measured, and shown to a
model that can see. What it finds is fixed once, on at most four slides, without changing a fact.

See docs/ARTIFACT_QUALITY_GOAL.md §4.6. This never fails a build: with no renderer, no browser
or no model that can see, the step says why and the deck is saved as it was.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, Field

from gunther.llm import Image, ModelError, ModelInfo
from gunther.model_profiles import Effort
from gunther.outputs import BuiltOutput, Draft, Events, Pool
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio.reference import DECK_FORMAT, READ_NOTE

logger = logging.getLogger(__name__)

MAX_FIXED = 4  # slides changed in the one fix round
IMAGES_PER_CALL = 4
# Layouts a fix may move a slide to: the title and dividers are not content, and a figure needs
# a picture the slide does not have.
TARGET_LAYOUTS = tuple(name for name in fmt.LAYOUTS if name not in ("title", "section", "figure"))
FIXABLE = ("unsupported", "contradicted", "unsourced")


class LookFinding(BaseModel):
    slide: int
    problem: str = ""
    severity: Literal["high", "low"] = "low"
    action: Literal["layout", "cut", "notes", "none"] = "none"
    layout: str = ""
    fix: str = ""


class LookReview(BaseModel):
    findings: list[LookFinding] = Field(default_factory=list)


def vision_model(gateway: Any, writer: ModelInfo, effort: Effort | None):
    """The model that looks: the photos job's when it can see, else the writer when it can."""

    finder = getattr(gateway, "for_role", None)
    chosen = finder("photos") if finder else None
    if chosen and chosen[0].vision:
        return chosen
    if writer.vision:
        return writer, effort
    return None


def measured(snapshot: dict, layouts: list[str] | None = None) -> list[dict[str, Any]]:
    """What the renderer measured on each slide: overflow, type below the readable floor, and a
    layout it could not draw from the content as written (it fell back to plain text)."""

    out = []
    for n, slide in enumerate(snapshot.get("slides", [])):
        small = [
            f"{item['text'][:40]!r} {item['size']:.0f}px (minimum {item['minimum']})"
            for item in slide.get("smallText", [])
        ]
        overflow = bool(slide.get("overflow"))
        # A designed slide names each element that spills out of its box or off the stage.
        spill = [
            f"{item.get('element', 'element')} {str(item.get('text', ''))[:40]!r} "
            + ("is outside the stage" if item.get("kind") == "outside" else "spills out of its box")
            for item in (slide.get("designed") or {}).get("overflow", [])
        ]
        asked = layouts[n] if layouts and n < len(layouts) else ""
        fallback = bool(asked) and asked != "text" and slide.get("drawn") == "text"
        out.append(
            {
                "overflow": overflow,
                "why": slide.get("overflowWhy") or "",
                "small": small,
                "spill": spill,
                "fallback": asked if fallback else "",
                "score": (10 if overflow else 0) + len(small) + (6 if fallback else 0),
            }
        )
    return out


def _facts(text: str) -> set[str]:
    """The numbers a slide states: not its marker, citation marks or list numbering."""

    text = fmt.MARK.sub("", fmt.without_design(text), count=1)
    text = fmt.CITE.sub("", text)
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    return fmt.numbers_in(text)


def _refs(text: str) -> set[str]:
    return set(re.findall(r"\[(\d+)\]", text))


def look(
    agents,
    built: BuiltOutput,
    drafts: list[Draft],
    *,
    model: ModelInfo,
    effort: Effort | None,
    use: str,
    pool: Pool,
    assemble: Callable[[list[Draft]], BuiltOutput],
    emit: Events,
    unfence: Callable[[str], str],
    material: str | None = None,
) -> tuple[BuiltOutput, dict[str, Any], dict[str, Any] | None]:
    """The deck after one look, what happened, and the snapshot of the deck as returned."""

    step, label = "look", "Looking at the slides"
    record: dict[str, Any] = {"status": "skipped", "applied": [], "findings": []}
    agents._stage(emit, step, label, "running")
    snapshot: list[dict[str, Any] | None] = [None]

    def finish(reason: str, detail: str = ""):
        if reason:
            record["reason"] = reason
            agents.skipped.append(
                {"step": step, "label": "Slides were not looked at", "reason": reason}
            )
        agents._stage(emit, step, label, "done", detail or reason)
        return built, record, snapshot[0]

    renderer = getattr(agents, "renderer", None)
    if renderer is None:
        return finish("Slide rendering is not set up in this installation.")
    try:
        before = renderer.capture(built, use)
    except Exception as error:  # noqa: BLE001 - never fail the build
        logger.info("The look at the slides could not render them: %s", error)
        return finish(f"The slides could not be drawn: {error}"[:300])
    snapshot[0] = before
    first = measured(before, [fmt.parse_slide(t).layout for t in fmt.split_slides(built.content)])
    record["before"] = {"snapshotId": before.get("id"), "measured": first}
    record["status"] = "measured"
    sees = vision_model(agents.gateway, model, effort)
    if sees is None:
        problems = sum(1 for m in first if m["score"])
        record["model"] = None
        return finish(
            "No model that can see images is set up (the photos job or the writing model).",
            f"{problems} slide(s) measured with a problem; nothing changed",
        )
    eye, eye_effort = sees
    record["model"] = eye.ref

    # 1. Look: every slide's picture, a few at a time.
    slides = fmt.split_slides(built.content)
    parsed = [fmt.parse_slide(text) for text in slides]
    files = {slide["index"]: slide for slide in before["slides"]}
    system = P.LOOK.format(layouts=", ".join(TARGET_LAYOUTS))

    def review(numbers: list[int]) -> list[LookFinding]:
        images, lines = [], []
        for n in numbers:
            images.append(
                Image.from_path(renderer.file(before["id"], files[n - 1]["image"]), "image/png")
            )
            facts = first[n - 1]
            notes = []
            if facts["fallback"]:
                notes.append(
                    f"the {facts['fallback']} layout could not be drawn: shown as plain text"
                )
            if facts["overflow"]:
                notes.append("OVERFLOW: " + (facts["why"] or "content did not fit"))
            if facts["small"]:
                notes.append("small text: " + "; ".join(facts["small"][:3]))
            lines.append(
                f"Image {len(images)} = slide {n}, layout {parsed[n - 1].layout}, title: "
                f"{parsed[n - 1].title or '(cover)'}"
                + (f" — measured: {' | '.join(notes)}" if notes else " — measured: no problem")
            )
        result, _ = agents.gateway.complete_json(
            eye,
            LookReview,
            system=system,
            prompt="Slides:\n" + "\n".join(lines),
            images=images,
            effort=eye_effort or "low",
            schema_at="end",
        )
        return [f for f in result.findings if f.slide in numbers]

    batches = [
        list(range(i, min(i + IMAGES_PER_CALL, len(slides) + 1)))
        for i in range(1, len(slides) + 1, IMAGES_PER_CALL)
    ]
    findings: list[LookFinding] = []
    failed = 0
    from gunther.studio.pipeline import warm_map

    def safe(numbers: list[int]) -> list[LookFinding]:
        nonlocal failed
        try:
            return review(numbers)
        except (ModelError, LookupError, OSError) as error:
            logger.info("Looking at slides %s failed: %s", numbers, error)
            failed += 1
            return []

    for part in warm_map(safe, batches, 3):
        findings += part
    for finding in findings:
        if finding.action == "layout" and (
            finding.layout not in TARGET_LAYOUTS
            or finding.layout == parsed[finding.slide - 1].layout
        ):
            finding.action = "cut"
            finding.fix = finding.fix or f"Shorten it ({finding.problem})"
    record["findings"] = [f.model_dump() for f in findings]
    record["reviewFailures"] = failed
    if failed and failed == len(batches):
        return finish("The model could not look at the slides.")

    # 2. Choose: what was measured wrong, then what the eye found; at most four slides.
    wanted: dict[int, list[str]] = {}
    rank: dict[int, int] = {}
    for n, facts in enumerate(first, 1):
        if facts["score"] and fmt.design_of(slides[n - 1]):
            # A designed slide is drawn again: its words stay, only its look changes.
            what = []
            if facts["overflow"]:
                what.append(
                    "text spills out of its box: "
                    + ("; ".join(facts["spill"][:4]) or facts["why"] or "content did not fit")
                )
            if facts["small"]:
                what.append("text under 18 px: " + "; ".join(facts["small"][:4]))
            wanted.setdefault(n, []).append(
                "; ".join(what) + ". Redraw it so everything fits inside its box and no text is "
                "under 18 px (shrink boxes' content by simplifying the layout, never by cutting "
                "or rewording the words)."
            )
            rank[n] = rank.get(n, 0) + 100 + facts["score"]
        elif facts["score"]:
            what = ["The renderer measured: "]
            if facts["fallback"]:
                what.append(
                    f"the {facts['fallback']} layout could not be drawn from the content as "
                    "written (it was shown as plain text): write the content exactly in that "
                    "layout's grammar, or switch to a layout it fits. "
                )
            if facts["overflow"]:
                what.append(
                    "the content does not fit the slide (" + (facts["why"] or "overflow") + "). "
                )
            if facts["small"]:
                what.append("type below the readable size: " + "; ".join(facts["small"][:3]) + ". ")
            wanted.setdefault(n, []).append(
                "".join(what) + "Cut words or move detail into the notes until it fits."
            )
            rank[n] = rank.get(n, 0) + 100 + facts["score"]
    for finding in findings:
        # What the eye finds is acted on only when a viewer would notice at once; the rest is
        # recorded. (Models looking at a clean slide invent small complaints.)
        if finding.action == "none" or not finding.problem or finding.severity != "high":
            continue
        if fmt.design_of(slides[finding.slide - 1]):
            # What the eye found on a designed slide is a design to change, not words to cut.
            wanted.setdefault(finding.slide, []).append(f"{finding.problem} {finding.fix}".strip())
            rank[finding.slide] = rank.get(finding.slide, 0) + 10
            continue
        how = {
            "layout": f"switch to the {finding.layout} layout",
            "cut": "cut words",
            "notes": "move detail into the notes",
        }[finding.action]
        wanted.setdefault(finding.slide, []).append(
            f"{finding.problem} Fix: {how}. {finding.fix}".strip()
        )
        rank[finding.slide] = rank.get(finding.slide, 0) + (10 if finding.severity == "high" else 3)
    targets = sorted(wanted, key=lambda n: (-rank[n], n))[:MAX_FIXED]
    targets.sort()
    record["targets"] = targets
    if not targets:
        record["status"] = "clean"
        return finish("", "no slide needed a fix")

    # 3. Fix. A designed slide is drawn again (its words stay); the others are rewritten, one
    # writer call for all of them.
    def fix_plain(targets: list[int]) -> tuple[dict[int, Draft], str | tuple[str, str] | None]:
        """The rewritten slides that passed every check, or why none did."""

        read = use == "read"
        asked = "\n\n".join(
            f"Slide {n} — what to fix:\n"
            + "\n".join(f"- {text}" for text in wanted[n])
            + f"\n\nSlide {n} as written:\n{fmt.without_design(drafts[n - 1].text)}"
            for n in targets
        )
        fix_system = P.FIX_LOOK.format(
            format=DECK_FORMAT + ("\n\n" + READ_NOTE if read else ""), problems=""
        )
        try:
            fixed = fmt.split_slides(
                unfence(
                    agents._text(model, fix_system, asked + "\n\nRewrite them now.", effort, 12_000)
                )
            )
        except ModelError as error:
            logger.info("Fixing what the look found failed: %s", error)
            return {}, (f"The fix could not be written: {error}"[:300])
        if len(fixed) != len(targets):
            record["status"] = "fix_dropped"
            return {}, (f"The fix came back with {len(fixed)} slides for {len(targets)}; dropped.")
        candidate = [d.text for d in drafts]
        changed: list[int] = []
        for n, text in zip(targets, fixed, strict=True):
            text = fmt.strip_labels(text)
            old = drafts[n - 1].text
            new = fmt.parse_slide(text)
            if n == 1:
                text = fmt.with_marker(text, "title", **fmt.parse_slide(old).attrs)
            elif new.layout not in TARGET_LAYOUTS:
                continue
            if (
                _facts(text) - _facts(old)
                or _refs(text) != _refs(old)
                or _facts(fmt.parse_slide(old).title) - _facts(new.title)
            ):
                logger.info(
                    "Slide %d: the fix changed a number or dropped or added a citation; dropped", n
                )
                record.setdefault("rejected", []).append({"slide": n, "why": "changed a fact"})
                continue
            # A slide the fix rewrote loses its design: the template draws it from the new words.
            if text.strip() != fmt.without_design(old).strip():
                candidate[n - 1] = text
                changed.append(n)
        if not changed:
            record["status"] = "fix_dropped"
            return {}, ("", "the fix changed nothing usable")

        # The format, in the context of the whole deck, and the claims against their sources.
        joined = "\n\n---\n\n".join(candidate)
        original = "\n\n---\n\n".join(d.text for d in drafts)
        before_problems = dict(fmt.check_deck(original, read=read, notes_required=use == "talk"))
        now = dict(fmt.check_deck(joined, read=read, notes_required=use == "talk"))
        for n in list(changed):
            if len(now.get(n, [])) > len(before_problems.get(n, [])):
                candidate[n - 1] = drafts[n - 1].text
                changed.remove(n)
                record.setdefault("rejected", []).append(
                    {"slide": n, "why": "; ".join(now[n])[:200]}
                )
        if not changed:
            return {}, ("", "the fix broke the format; dropped")
        by_ref = pool.by_ref()

        def recheck(n: int) -> Draft:
            issues, coverage = agents._claims(model, candidate[n - 1], by_ref, material=material)
            return Draft(
                candidate[n - 1], issues + agents._chart_issues(candidate[n - 1], by_ref), coverage
            )

        rechecked = dict(zip(changed, warm_map(recheck, changed, 4), strict=True))
        revised = list(drafts)
        for n, draft in rechecked.items():
            was = sum(1 for i in drafts[n - 1].issues or [] if i.verdict in FIXABLE)
            now_bad = sum(1 for i in draft.issues if i.verdict in FIXABLE)
            if now_bad > was:
                record.setdefault("rejected", []).append(
                    {"slide": n, "why": "a claim lost its source"}
                )
                continue
            revised[n - 1] = draft
        changed = [n for n in changed if revised[n - 1] is not drafts[n - 1]]
        if not changed:
            return {}, ("", "the fix lost a claim's source; dropped")
        return {n: revised[n - 1] for n in changed}, None

    revised = list(drafts)
    changed: list[int] = []
    designed_targets = [n for n in targets if fmt.design_of(slides[n - 1])]
    plain_targets = [n for n in targets if n not in designed_targets]
    record["designed"] = designed_targets
    if designed_targets:
        from gunther.studio import design as D
        from gunther.studio.pipeline import WORKERS

        # The drafts' own citation numbers (the pool's), not the renumbered ones the deck shows:
        # the design is renumbered with the text when the deck is assembled.
        held = [d.text for d in drafts]
        try:
            redrawn, _, notes = D.redesign(
                agents,
                model,
                held,
                {n - 1: " ".join(wanted[n]) for n in designed_targets},
                workers=WORKERS,
                style=D.deck_style(slides),
                keep_on_fail=True,
            )
            agents.skipped += notes
        except Exception as error:  # noqa: BLE001 - never fail the build
            logger.info("Redesigning the slides failed: %s", error)
            redrawn = held
        for n in designed_targets:
            if redrawn[n - 1] != held[n - 1]:
                revised[n - 1] = replace(drafts[n - 1], text=redrawn[n - 1])
                changed.append(n)
    stopped: str | tuple[str, str] | None = None
    if plain_targets:
        fixed_plain, stopped = fix_plain(plain_targets)
        for n, draft in fixed_plain.items():
            revised[n - 1] = draft
            changed.append(n)
    if not changed:
        if stopped:
            return finish(*stopped) if isinstance(stopped, tuple) else finish(stopped)
        record["status"] = "fix_dropped"
        return finish("", "the new designs changed nothing usable")
    changed.sort()

    # 4. Look again: the same measurements; a slide that got worse keeps its old self.
    try:
        after_built = assemble(revised)
        after = renderer.capture(after_built, use)
        second = measured(after, [fmt.parse_slide(d.text).layout for d in revised])
        worse = [n for n in changed if second[n - 1]["score"] > first[n - 1]["score"]]
        if worse:
            for n in worse:
                revised[n - 1] = drafts[n - 1]
                record.setdefault("rejected", []).append({"slide": n, "why": "drew worse"})
            changed = [n for n in changed if n not in worse]
            if not changed:
                return finish("", "every fix drew worse; dropped")
            after_built = assemble(revised)
            after = renderer.capture(after_built, use)
            second = measured(after, [fmt.parse_slide(d.text).layout for d in revised])
    except Exception as error:  # noqa: BLE001 - never fail the build
        logger.info("Checking the fixed slides failed: %s", error)
        return finish(f"The fixed slides could not be drawn again: {error}"[:300])
    record["status"] = "fixed"
    record["applied"] = [
        {
            "slide": n,
            "from": parsed[n - 1].layout,
            "to": fmt.parse_slide(revised[n - 1].text).layout,
            "before": first[n - 1]["score"],
            "after": second[n - 1]["score"],
        }
        for n in changed
    ]
    record["after"] = {"snapshotId": after.get("id"), "measured": second}
    agents._stage(emit, step, label, "done", f"fixed slide(s) {', '.join(map(str, changed))}")
    snapshot[0] = after
    return after_built, record, after
