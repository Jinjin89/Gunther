"""Studio claim checking, surgical repairs and chart fallback."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from copy import copy
from dataclasses import replace
from typing import Any

from gunther.checks import Verdicts, finding, verdicts_of
from gunther.citations import CITATION_GROUP, MARKDOWN_TABLE, check_citations, collapse_citations
from gunther.evidence import Evidence
from gunther.llm import ModelError
from gunther.llm import unfence as _unfence
from gunther.output_checks import check_part
from gunther.outputs import Draft, Events, Issue, Pool, _marked, check_heading_refs, split_document
from gunther.skill_checks import CHARTS
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.drafting import source_images_kept
from gunther.studio.requests import WORKERS, material_request, warm_map
from gunther.studio.state import Run, _keeps_heading, _matches_plan, _no_events

logger = logging.getLogger("gunther.studio.pipeline")

STUDIO_CHECK = (
    "Check this slide or report section. Its title states the conclusion the cited evidence "
    "supports: judge the title against the sources cited in it. Only factual statements can be "
    "'not' or 'partly': numbers, names, dates, events, and what a source says or found — "
    "use 'not' when the cited source does not state it or states otherwise. Recommendations, "
    "decisions, priorities, interpretations and transitions are the author's judgement: mark "
    "them 'inference' or 'nonfactual', never 'not', even when the sources do not say them. "
    "For a chart row, support requires the source to state each value for that row label "
    "and series, including its unit. Use the citations in each value cell, or the row "
    "label's citations if the value cell has none. A value for a different label or series "
    "does not support the row."
)

# What the claim fix works on: a cited fact its passage does not state, and a number with no
# source (the fixer cites the passage that states it, or drops the number).
FIXABLE = ("unsupported", "contradicted", "unsourced")


class _MaterialCheckerGateway:
    """Adapt only the studio's sentence checks, keeping Ask's checking logic intact."""

    def __init__(self, agents, material: str) -> None:
        self.agents, self.material = agents, material

    def complete_json(self, model, schema, *, system, prompt, **kwargs):
        system, prompt = material_request(self.material, system + "\n\n" + prompt)
        self.agents._count_model_call()
        return self.agents.gateway.complete_json(
            model, schema, system=system, prompt=prompt, schema_at="end", **kwargs
        )


def _chart_rows_supported(
    tables: list[tuple[int, re.Match[str]]], coverage: Mapping[str, Any] | None
) -> bool:
    if len(tables) != 1:
        return False
    index, table = tables[0]
    rows = [
        (row_index, row)
        for row_index, line in enumerate(collapse_citations(table[0]).split("\n"))
        for row in fmt.read_table([line.strip()])[0]
    ]
    if len(rows) < 3 or not 2 <= len(rows[0][1]) <= 4:
        return False
    (_, head), *data = rows
    supported = {
        tuple(claim["tableRow"]) for claim in (coverage or {}).get("claims", [])
        if claim.get("tableRow") and claim["verdict"] == "supported"
    }
    for row_index, (label, *cells) in data:
        if (index, row_index) not in supported:
            return False
        if len(cells) != len(head) - 1 or not fmt.CITE.sub("", label).strip(" *_`"):
            return False
        if not all(
            fmt.number_cell(cell) and CITATION_GROUP.search(cell + " " + label) for cell in cells
        ):
            return False

    return True


def _stating(issues, by_ref: dict[int, Evidence], most: int = 6) -> set[int]:
    """Passages that state the figures of claims flagged as having no source, so the fix can
    cite them. Small whole numbers match too much to tell; a sign is a direction a passage may
    state in words ("down 9.5%")."""

    wanted = {
        n.lstrip("-")
        for issue in issues
        if issue.verdict == "unsourced"
        for n in fmt.numbers_in(issue.claim)
        if "." in n or len(re.sub(r"\D", "", n)) >= 3
    }
    found: list[int] = []
    for ref, item in by_ref.items():
        stated = {n.lstrip("-") for n in fmt.numbers_in(item.context or item.text)}
        if ref is not None and wanted & stated:
            found.append(ref)
    return set(found[:most])


def _lend_refs(line: str, rest: str, most: int = 6) -> str:
    """The line with the citations other slides give for the same figures added (for checking
    only). A line with no figure, or whose figures no other slide cites, is left as it is."""

    plain = re.sub(r"\s*\[\d+\]", "", line)
    wanted = fmt.numbers_in(plain)
    if not wanted:
        return line
    lent: list[str] = []
    for other in rest.split("\n"):
        cited = re.findall(r"\[(\d+)\]", other)
        if cited and wanted & fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", other)):
            lent += cited
    own = re.findall(r"\[(\d+)\]", line)
    extra = [n for n in dict.fromkeys(lent) if n not in own][:most]
    return line.rstrip() + "".join(f" [{n}]" for n in extra) if extra else line


class StudioVerification:
    # 5. Verifying -------------------------------------------------------------------------

    def verify(
        self,
        model,
        run: Run,
        parts: list[str],
        pool: Pool,
        emit: Events,
        material: str | None = None,
    ) -> list[Draft]:
        """Warm with the first part, then check and repair the remaining parts in parallel."""

        label = "Checking every claim against its source"
        self._stage(emit, "verify", label, "running", f"{len(parts)} slides")
        by_ref = pool.by_ref()

        rest = "\n".join(parts[1:])

        def check(text: str) -> Draft:
            if run.spec.kind == "report":
                text = fmt.cite_exhibit_rows(fmt.cite_captions(fmt.table_separators(text)))
            text, _ = check_citations(text, set(by_ref))
            return Draft(text, self._claims(model, text, by_ref, rest, material=material))

        def check_and_fix(text: str) -> Draft:
            draft = check(text)
            if not any(issue.verdict in FIXABLE for issue in draft.issues or []):
                return draft
            title = fmt.MARK.match(text) and fmt.parse_slide(text).layout == "title"
            fixed = self._fix_claims(
                model, [draft], by_ref, rest if title else "", material=material
            )
            if not fixed:
                return draft
            if run.plan or run.spec.kind == "report":
                preamble, repaired = split_document(fixed[0], run.spec.kind)
                if preamble or len(repaired) != 1 or not _keeps_heading(run, text, repaired):
                    return draft
            return check(fixed[0])

        drafts = warm_map(
            check_and_fix,
            parts,
            WORKERS,
            lambda done, total: self._progress(
                emit, "verify", label, f"{done} of {total} slides checked"
            ),
        )
        left = sum(
            1
            for d in drafts
            for i in d.issues or []
            if i.verdict in ("unsupported", "contradicted")
        )
        self._stage(
            emit,
            "verify",
            label,
            "done",
            f"{left} claim(s) still flagged" if left else "all claims supported",
        )
        return drafts

    def _claim_text(self, text: str, rest: str = "") -> str:
        marked = _marked(text)
        body = marked.body
        lines = body.split("\n")
        if marked.layout == "title":
            # The title slide's meta line (audience · date) states no claim.
            content = [n for n, line in enumerate(lines) if line.strip()]
            if len(content) >= 3:
                lines[content[2]] = ""
            lines = [_lend_refs(line, rest) for line in lines]
        checked = check_heading_refs(body, lines)
        if self.scientific_context:
            # A numbered step's own number is not a claim to source.
            checked = re.sub(r"(?m)^([ \t]*)\d+[.)]\s+", r"\1", checked)
        return checked

    def _claims(
        self,
        model,
        text: str,
        by_ref: dict[int, Evidence],
        rest: str = "",
        *,
        material: str | None = None,
        prior: Verdicts | None = None,
        inventory: Verdicts | None = None,
    ) -> Verdicts:
        """One claim check per part. A title is checked against the passages its own slide or
        section cites (it states their conclusion). The title slide states the whole deck's
        conclusion, so a figure on it is checked against the passages the other slides (``rest``)
        cite for that same figure, not only its own. Interpretation and recommendation that
        follow from cited facts are the author's judgement, not findings; what is flagged is a
        cited claim its passages do not state, and a specific fact (a number) with no source."""

        body = _marked(text).body
        checked = self._claim_text(text, rest)
        checker = copy(self.ask)
        checker.gateway = _MaterialCheckerGateway(
            self,
            material if material is not None else self._material(list(by_ref.values())),
        )
        if self.scientific_context:
            checker.gateway.material += (
                "\nActual image readings (linked asset IDs identify the source figures above):\n"
                + json.dumps(self.scientific_context.get("figure_readings", []), ensure_ascii=False)
            )
        checked = check_part(
            checker, model,
            STUDIO_CHECK + ("\n" + P.SCIENCE_TRUTH if self.scientific_context else ""),
            checked,
            by_ref, limit=self.limits.sentences, prior=prior or {}, inventory=inventory,
        )
        for claim in checked.values():
            label = claim.get("label", claim["verdict"])
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", claim["text"]).strip()
            # An image reference is provenance, so its id and crop coordinates are not facts.
            said = re.sub(r"!\[([^\]]*)\]\(gunther-asset:[^)]*\)", r"\1", plain)
            if label in {"not", "partly"}:
                claim["issue"] = Issue(plain[:300], "unsupported", claim.get("reason") or "").out()
            elif label == "unsourced" and re.search(r"\d", said):
                issue = Issue(plain[:300], "unsourced", "A specific fact with no source.")
                claim["issue"] = issue.out()
        # Syntax findings come from the current section independently of its model verdicts.
        held = {row["issue"]["claim"][:80] for row in checked.values() if "issue" in row}
        findings = []
        for line in fmt.uncited_lines([ln for ln in body.split("\n") if not ln.startswith(":::")]):
            plain = re.sub(r"\s*\[(?:\?|\d+)\]", "", line).strip()
            if plain[:80] not in held and not re.match(r"^(Source|Sources|来源)\s*[:：]", plain):
                issue = Issue(plain[:300], "unsourced", "A number with no citation.")
                findings.append(finding(issue.out()))
        return checked | verdicts_of(*findings)

    def _cover(
        self, model, drafts: list[Draft], pool: Pool, material: str | None = None
    ) -> list[Draft]:
        """The subtitle is the deck's core message: after the slides are final, it is made to
        state what their titles say (claim fixes can move a title away from it), then the
        title slide is checked against its sources again."""

        if not drafts or fmt.parse_slide(drafts[0].text).layout != "title":
            return drafts
        cover = fmt.parse_slide(drafts[0].text)
        body = [line for line in cover.body if line.strip()]
        if not cover.title or not body:
            return drafts
        titles = [fmt.parse_slide(d.text).title for d in drafts[1:]]
        titles = [t for t in titles if t]
        rest = "\n".join(d.text for d in drafts[1:])
        cited = [
            line.strip()
            for line in rest.split("\n")
            if re.search(r"\[\d+\]", line)
            and fmt.numbers_in(re.sub(r"\s*\[\d+\]", "", line)) & fmt.numbers_in(" ".join(titles))
        ][:30]
        system = P.COVER.format(
            title=cover.title,
            subtitle=body[0],
            titles="\n".join(f"{n}. {t}" for n, t in enumerate(titles, 2)),
            cited="\n".join(cited) or "(none)",
        )
        system, prompt = material_request(
            material if material is not None else self._material(list(pool.by_ref().values())),
            system + "\n\nReply now.",
        )
        try:
            line = _unfence(self._text(model, system, prompt, "low", 2000)).strip()
        except ModelError as error:
            logger.info("Aligning the subtitle failed: %s", error)
            self.skipped.append(
                {"step": "verify", "label": "Subtitle alignment unavailable", "reason": str(error)}
            )
            return drafts
        line = line.splitlines()[0].strip() if line else ""
        line = re.sub(r"^(Subtitle|副标题)\s*[:：]\s*", "", line)
        if not line or line == body[0] or line.startswith("#"):
            return drafts
        text = drafts[0].text.replace(body[0], line, 1)
        by_ref = pool.by_ref()
        text, _ = check_citations(text, set(by_ref))
        draft = Draft(text, self._claims(model, text, by_ref, rest, material=material))
        if any(i.verdict in ("unsupported", "contradicted") for i in draft.issues or []):
            logger.info("The aligned subtitle is not supported; keeping the old one")
            return drafts
        return [draft, *drafts[1:]]

    def _refit(
        self,
        model,
        effort,
        run: Run,
        drafts: list[Draft],
        pool: Pool,
        material: str | None = None,
    ) -> list[Draft]:
        """Claim fixes can lengthen a slide past its budget: the format is checked again, and a
        slide that changed is checked against its sources once more."""

        joined = "\n\n---\n\n".join(d.text for d in drafts)
        if not fmt.check_deck(
            joined, read=run.use == "read", notes_required=False, scientific=bool(run.study)
        ):
            return drafts
        prior_errors = run.format_repair_errors
        fitted = fmt.split_slides(self.fix_format(model, effort, run, joined, _no_events))
        if len(fitted) != len(drafts):
            run.format_repair_errors = prior_errors
            return drafts
        changed = [i for i, (d, t) in enumerate(zip(drafts, fitted, strict=True)) if d.text != t]
        by_ref = pool.by_ref()

        rest = "\n".join(fitted[1:])

        def check(text: str) -> Draft:
            return Draft(text, self._claims(model, text, by_ref, rest, material=material))

        again = warm_map(check, [fitted[i] for i in changed], WORKERS)
        for index, draft in zip(changed, again, strict=True):
            drafts[index] = draft
        return drafts

    def _chart_fallback(self, draft: Draft, index: int) -> Draft:
        """Keep the reader's data and citations when the Checker cannot license a chart."""
        layout = fmt.parse_slide(draft.text).layout
        is_chart = layout in ("chart", *CHARTS)
        source = D.visible_content(draft.text) if is_chart else draft.text
        tables = list(enumerate(MARKDOWN_TABLE.finditer(source)))

        def exhibit(match: re.Match[str]) -> str:
            attrs = match["attrs"]
            if not fmt.EXHIBIT_CHART.search(attrs):
                return match[0]
            inside = [
                (i, table) for i, table in tables
                if match.start("body") <= table.start() < match.end("body")
            ]
            if _chart_rows_supported(inside, draft.coverage):
                return match[0]
            return match[0].replace(attrs, fmt.EXHIBIT_CHART.sub("", attrs).rstrip(), 1)

        if is_chart:
            if _chart_rows_supported(tables, draft.coverage):
                return draft
            fallback = "compare" if layout in CHARTS else "table"
            text = fmt.with_marker(fmt.without_design(draft.text), fallback)
        else:
            text = fmt.EXHIBIT.sub(exhibit, draft.text)
        if text == draft.text:
            return draft
        self.skipped.append({
            "step": "verify",
            "label": f"Part {index + 1} drawn as a table",
            "reason": "The Checker did not support every chart row against its citations.",
        })
        return replace(draft, text=text)

    def _fix_claims(
        self,
        model,
        drafts: list[Draft],
        by_ref,
        rest: str = "",
        *,
        material: str | None = None,
    ) -> list[str] | None:
        parts = []
        refs: set[int] = set()
        for number, draft in enumerate(drafts, 1):
            flagged = [i for i in draft.issues or [] if i.verdict in FIXABLE]
            parts.append(
                f"Part {number}:\n{draft.text}\n\nUnsupported in part {number}:\n"
                + "\n".join(f'- "{i.claim}" ({i.note})' for i in flagged)
            )
            lent = "\n".join(_lend_refs(line, rest) for line in draft.text.split("\n"))
            refs |= {int(n) for n in re.findall(r"\[(\d+)\]", lent if rest else draft.text)}
            refs |= _stating(flagged, by_ref)
        passages = "\n\n".join(self._passage(by_ref[r], 3000) for r in sorted(refs) if r in by_ref)
        system = P.FIX_CLAIMS.format(parts="\n\n=====\n\n".join(parts), passages=passages)
        if self.scientific_context:
            system = (
                "Repair only the unsupported scientific statements listed below. Correct them "
                "from clean evidence or omit them. Do not preserve unwarranted confidence, "
                "causal claims or interpretations; retain images, essential explanations "
                "and format.\n"
                + P.SCIENCE_TRUTH
                + "\n"
                + "\n\n=====\n\n".join(parts)
                + "\nPassages:\n"
                + passages
            )
        system, prompt = material_request(
            material if material is not None else self._material(list(by_ref.values())),
            system + "\n\nRewrite them now.",
        )
        try:
            # Repairing listed sentences from given passages is constrained work: on DeepSeek
            # "medium" rounds up to high thinking, which this does not need.
            text = _unfence(self._text(model, system, prompt, "low", 16_000))
        except ModelError as error:
            logger.info("Fixing unsupported claims failed: %s", error)
            self.skipped.append(
                {"step": "verify", "label": "Claim repair unavailable", "reason": str(error)}
            )
            return None
        pieces = [p.strip() for p in re.split(r"\n-{3,}\n", "\n" + text + "\n") if p.strip()]
        if len(pieces) != len(drafts):
            logger.info("Claim fix returned %d parts for %d", len(pieces), len(drafts))
            if len(drafts) == 1:
                self.skipped.append({
                    "step": "verify", "label": "Claim repair unavailable",
                    "reason": "The repair did not return a single revised part.",
                })
                return None

            # Asked again one part at a time; a part whose fix fails keeps its text.
            def single(draft: Draft) -> str:
                one = self._fix_claims(model, [draft], by_ref, rest, material=material)
                return one[0] if one else draft.text

            return warm_map(single, drafts, WORKERS)
        out = []
        for draft, piece in zip(drafts, pieces, strict=True):
            piece = re.sub(r"\A\s*Part \d+:\s*\n", "", piece)
            # A slide keeps its marker even when the fixer dropped it.
            marker = fmt.MARK.match(draft.text)
            if marker and not fmt.MARK.match(piece):
                piece = marker.group(0).strip() + "\n" + piece
            out.append(piece if source_images_kept(draft.text, piece) else draft.text)
        return out

    def _fit_checked(
        self, model, effort, run: Run, drafts: list[Draft], pool: Pool, material: str,
    ) -> list[Draft]:
        """Refit verified slides and record any remaining scientific problems."""

        if not run.study:
            # The subtitle restates what the action titles claim; a scientific deck's
            # titles may name topics, so its subtitle stays as written and reviewed.
            aligned = self._cover(model, drafts, pool, material)
            if _matches_plan(run, [d.text for d in aligned]):
                drafts = aligned
        drafts = self._refit(model, effort, run, drafts, pool, material)
        if not run.study:
            return drafts
        healed = []
        for draft in drafts:
            fixed, repaired = S.heal_images(draft.text, run.figure_readings or [], pool.items)
            for note in repaired:
                self._science_limit(run, note)
            healed.append(replace(draft, text=fixed) if repaired else draft)
        deck = "\n\n---\n\n".join(d.text for d in healed)
        # Claim repairs may reword a slide that named a panel: that is delivered as an open
        # problem, never a lost build.
        known = set(run.limitations or [])
        for problem in self._science_code(run, deck, pool):
            if problem not in known:
                self._science_limit(run, problem)
                if run.review_status == "ready":
                    run.review_status = "open_problems"
        return healed
