# ruff: noqa: E501
"""The report: ``python -m evals.quality.report DIR`` re-renders ``DIR/report.md`` from
``DIR/raw.json``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from evals.quality import judge as judging

SCORES = ("insight", "storyline", "titles", "evidence", "clarity", "correctness", "visual")


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def num(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def summarize(builds: list[dict[str, Any]]) -> dict[str, Any]:
    """The numbers of a group of builds (one method, optionally one kind)."""

    ok = [b for b in builds if not b.get("error")]
    verdicts = [v["verdict"] for b in ok for v in b.get("verdicts", [])]
    titled = [b["titlesOnly"]["pass"] for b in ok if b.get("titlesOnly")]
    defect_rules: dict[str, int] = {}
    for build in ok:
        for rule, count in (build.get("defectCounts") or {}).items():
            defect_rules[rule] = defect_rules.get(rule, 0) + count
    absolutes = [b["absolute"] for b in ok if b.get("absolute")]
    return {
        "builds": len(builds),
        "ok": len(ok),
        "failed": [
            f"{b['set']}/{b['label']}: {b['error'][:120]}" for b in builds if b.get("error")
        ],
        "parts": len(verdicts),
        "shipRate": judging.ship_rate(verdicts),
        "strictShipRate": (sum(1 for v in verdicts if v == "ship") / len(verdicts))
        if verdicts
        else None,
        "titlesPass": (sum(titled) / len(titled)) if titled else None,
        "scores": {name: judging.mean([a.get(name) for a in absolutes]) for name in SCORES},
        "defects": sum(defect_rules.values()),
        "defectRules": defect_rules,
        "unmeasured": sum(1 for b in ok if not b.get("measured")),
        "seconds": judging.mean([b.get("seconds") for b in ok]),
        "calls": judging.mean([b.get("calls") for b in ok]),
        "tokens": judging.mean([b.get("tokens") for b in ok]),
    }


def _row(name: str, s: dict[str, Any]) -> str:
    scores = " / ".join(num(s["scores"][n]) for n in SCORES[:6])
    rules = ", ".join(f"{k} {v}" for k, v in sorted(s["defectRules"].items())) or "none"
    return (
        f"| {name} | {s['ok']}/{s['builds']} | {pct(s['shipRate'])} ({s['parts']}) | "
        f"{pct(s['strictShipRate'])} | {pct(s['titlesPass'])} | {scores} | {num(s['scores']['visual'])} | "
        f"{s['defects']} ({rules}) | {num(s['seconds'], 0)} | {num(s['calls'], 0)} / "
        f"{num((s['tokens'] or 0) / 1000 if s['tokens'] is not None else None, 0)}k |"
    )


HEAD = (
    "| | Built | Ship rate (slides/sections) | Strict ship | Titles-only pass | "
    "Rubric: insight / storyline / titles / evidence / clarity / correctness | Visual | "
    "Hard defects | Seconds | Calls / tokens |\n|---|---|---|---|---|---|---|---|---|---|"
)


def render(raw: dict[str, Any]) -> str:
    out = [f"# Quality evaluation: {raw['label']}", ""]
    out += [
        f"- Started {raw['started']}; writing model `{raw['model']}`; judge `{raw['judge']}` "
        f"({'sees slide images' if raw.get('judgeSeesImages') else 'text only'}); code {raw['codeHash']}.",
        f"- Judge usage: {raw['judgeUsage']['calls']} calls, {raw['judgeUsage']['tokens']} tokens "
        "(not counted in the build columns).",
        "- Ship rate = (ship + minor edit) / all slides or sections; strict = ship only. "
        "The bar: ship rate >= 80%, titles-only >= 90%, hard defects 0 (docs/ARTIFACT_QUALITY_GOAL.md).",
        "",
    ]
    builds = raw["builds"]
    unrendered = [b for b in builds if not b.get("error") and not (b.get("render") or {}).get("ok")]
    if unrendered:
        notes = sorted({(b.get("render") or {}).get("note", "not rendered") for b in unrendered})
        out += [f"- Not rendered ({len(unrendered)} artifacts): {'; '.join(notes)}. "
                "Overflow, small text and empty-half defects could not be measured.", ""]  # fmt: skip
    for method in raw["methods"]:
        mine = [b for b in builds if b["method"] == method]
        out += [f"## Method: {method}", "", HEAD]
        for label in ("deck", "report"):
            group = [b for b in mine if b["label"] == label]
            if group:
                out.append(_row(label + "s", summarize(group)))
        total = summarize(mine)
        out.append(_row("**all**", total))
        out.append("")
        if total["failed"]:
            out += ["Failed builds:", *[f"- {line}" for line in total["failed"]], ""]
    if raw.get("pairwise"):
        out += ["## Pairwise (blind, order swapped; a result must survive the swap)", ""]
        out += [
            "| Set | Kind | " + " | ".join(judging.PAIR_CRITERIA) + " |",
            "|---|---|" + "---|" * len(judging.PAIR_CRITERIA),
        ]  # noqa: E501
        wins = {c: [] for c in judging.PAIR_CRITERIA}
        for pair in raw["pairwise"]:
            if "criteria" not in pair:
                out.append(
                    f"| {pair['set']} | {pair['label']} | judge failed: {'; '.join(pair['errors'])[:100]} |"
                )
                continue
            cells = []
            for c in judging.PAIR_CRITERIA:
                v = pair["criteria"][c]
                wins[c].append(v)
                cells.append({"first": pair["first"], "second": pair["second"], "tie": "tie"}[v])
            out.append(f"| {pair['set']} | {pair['label']} | " + " | ".join(cells) + " |")
        first, second = raw["pairwise"][0]["first"], raw["pairwise"][0]["second"]
        out += ["", f"{second} against {first} (win / tie / loss for {second}):", ""]
        for c in judging.PAIR_CRITERIA:
            t = judging.tally_pairs(wins[c])
            out.append(f"- {c}: {t['second']} / {t['tie']} / {t['first']}")
        out.append("")
    out += ["## Material", ""]
    for set_id, info in raw["sets"].items():
        out += [f"### {set_id}: {info['title']}", "", f"Question: {info['question']}", ""]
        if info.get("note"):
            out += [info["note"], ""]
        for source in info["sources"]:
            out.append(f"- {source['title']} ({source.get('url', '')}): {source['how']}"
                       + (f"; error: {source['error']}" if source.get("error") else ""))  # fmt: skip
        if info.get("failedJobs"):
            out.append(f"- Tolerated failed jobs: {'; '.join(info['failedJobs'])}")
        out += [
            "",
            f"Deck brief: {info['deckBrief']}",
            "",
            f"Report brief: {info['reportBrief']}",
            "",
        ]
    out += ["## Artifacts", ""]
    for b in sorted(builds, key=lambda x: (x["set"], x["label"], x["method"])):
        rel = Path("builds") / b["set"] / f"{b['label']}-{b['method']}"
        out += [f"### {b['set']} / {b['label']} / {b['method']}", ""]
        if b.get("error"):
            out += [f"**Build failed**: {b['error']}", ""]
            continue
        out.append(f"[content]({rel}/content.md) · [citations]({rel}/citations.json) · "
                   f"[outline]({rel}/outline.json) · [checks]({rel}/checks.json) · title: {b.get('title')}")  # fmt: skip
        out.append(f"Time {num(b.get('seconds'), 0)} s{' (reused)' if b.get('reused') else ''}, "
                   f"{b.get('calls')} calls, {b.get('tokens')} tokens, {b.get('parts')} "
                   f"{'slides' if b['kind'] == 'slides' else 'sections'}, "
                   f"{b.get('issues', 0)} checker issues.")  # fmt: skip
        absolute = b.get("absolute")
        if absolute:
            out.append("Rubric: " + ", ".join(f"{n} {absolute[n]}" for n in SCORES if absolute.get(n) is not None)
                       + f". Ship rate {pct(b.get('shipRate'))}.")  # fmt: skip
            out.append(f"Judge's summary: {absolute['summary']}")
        elif b.get("judgeError"):
            out.append(f"Judge failed: {b['judgeError']}")
        titles = b.get("titlesOnly")
        if titles:
            out.append(f"Titles-only: **{'pass' if titles['pass'] else 'fail'}**"
                       + (f" - {titles.get('reason', titles.get('error', ''))}" if titles else "")
                       + (f"\n\n> Retold from titles: {titles['retold']}" if titles.get("retold") else ""))  # fmt: skip
        out.append("")
        if b.get("defects"):
            out += [f"Hard defects ({len(b['defects'])}):", ""]
            out += [f"- {d['where']}: {d['rule']} - {d['detail']}" for d in b["defects"]]
            out.append("")
        elif b.get("measured") is False:
            out += ["Hard defects: none found by the linter (layout not measured).", ""]
        else:
            out += ["Hard defects: none.", ""]
        if b.get("verdicts"):
            out += ["| # | Verdict | Reason |", "|---|---|---|"]
            out += [
                f"| {v['index']} | {v['verdict']} | {v['reason'].replace('|', '/')} |"
                for v in b["verdicts"]
            ]
            out.append("")
        if b.get("pngs"):
            out.append("Renders: " + " ".join(f"[{Path(p).stem}]({rel}/{p})" for p in b["pngs"]))
            out.append("")
    return "\n".join(out) + "\n"


def write(folder: Path) -> str:
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    text = render(raw)
    (folder / "report.md").write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m evals.quality.report DIR")
        return 2
    print(write(Path(args[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
