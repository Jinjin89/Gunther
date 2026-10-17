"""The scorecard: one column of numbers per configuration, and a line for every case that failed.

Parts are counted, not cases: precision, recall and "untouched parts identical" are sums over
every case that saved a version, so one case that rewrites a whole deck weighs as much as the
parts it rewrote. ``python -m evals.artifacts.report FOLDER`` writes the scorecard again from
a run's ``raw.json`` (also from a run that did not finish); with ``--rescore`` it first scores
every case again from what the run stored, with the judge's answers as they were, so that a
fix to the scoring does not cost a new run.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.artifacts import score
from evals.artifacts.score import Case


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _share(card: dict[str, Any], key: str) -> str:
    done, total = card[key]
    return f"{done} / {total} ({done / total:.2f})" if total else "none"


def scorecard(cases: dict[str, Case], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The numbers for one configuration. ``results`` holds each case's recorded run."""

    ran = [
        r for r in results.values() if not r["outcome"]["failed"] and not r["outcome"]["impossible"]
    ]
    saved = [r for r in ran if r["measures"]["saved"]]
    parts = {"changed": 0, "changed_ok": 0, "required": 0, "required_done": 0}
    parts |= {"untouched": 0, "damaged": 0, "raw_damaged": 0}
    support = {"ok": 0, "flagged": 0, "unchecked": 0}
    for r in saved:
        m = r["measures"]
        # A case where anything may change has no part that must stay, so it proves nothing.
        if not cases[r["case"]].expect.may_all:
            parts["changed"] += len(m["changed"])
            parts["changed_ok"] += m["changed_ok"]
        parts["required"] += m["required_done"] + len(m["missed"])
        parts["required_done"] += m["required_done"]
        parts["untouched"] += m["untouched"]
        parts["damaged"] += len(m["damaged"])
        parts["raw_damaged"] += len(m["raw_damaged"])
        for name in support:
            support[name] += m["support"][name]

    def count(*names: str, prefixes: tuple[str, ...] = ()) -> list[int]:
        """[how many held, how many were checked] for the checks with these names."""

        found = [
            ok
            for r in ran
            for name, ok in r["checks"].items()
            if name in names or name.startswith(prefixes)
        ]
        return [sum(found), len(found)]

    judged = [r for r in ran if r["judgement"] is not None]
    # What asking is about: the asking cases, and any other case that has to be asked about.
    asking = [
        r
        for r in results.values()
        if cases[r["case"]].category == "asking" or cases[r["case"]].expect.asks_rule == "must"
    ]
    # A case run more than once passes by its pass rate; its runs may disagree.
    runs: dict[str, list[bool]] = {}
    for key, result in results.items():
        runs.setdefault(result.get("case", key), []).append(not result["failures"])
    by_category: dict[str, list[int]] = {}
    for case_id, passes in runs.items():
        total = by_category.setdefault(cases[case_id].category, [0, 0])
        total[0] += sum(passes) / len(passes)
        total[1] += 1
    small = [r["outcome"]["calls"] for r in ran if cases[r["case"]].category == "small edit"]
    return {
        "cases": len(runs),
        "runs": len(results),
        "passed": sum(sum(passes) / len(passes) for passes in runs.values()),
        "disagreed": {
            case: f"{sum(passes)}/{len(passes)}"
            for case, passes in runs.items()
            if 0 < sum(passes) < len(passes)
        },
        "impossible": sum(1 for r in results.values() if r["outcome"]["impossible"]),
        "failed_requests": sum(1 for r in results.values() if r["outcome"]["failed"]),
        "intent": [sum(r["checks"].get("intent") is True for r in results.values()), len(results)],
        "asked": [sum(r["checks"].get("asked") is True for r in asking), len(asking)],
        "precision": [parts["changed_ok"], parts["changed"]],
        "recall": [parts["required_done"], parts["required"]],
        "untouched": [parts["untouched"] - parts["damaged"], parts["untouched"]],
        "untouched_raw": [parts["untouched"] - parts["raw_damaged"], parts["untouched"]],
        "count": count("count"),
        "layout": count("layout"),
        "order": count("order", "new_at"),
        "strings": count("shorter", prefixes=("contains ", "without ")),
        "cites": count("cites"),
        "numbers": count("numbers"),
        "support": support,
        "judge": [sum(r["judgement"]["answer"] for r in judged), len(judged)],
        "judge_silent": sum(1 for r in ran if "judge" in r["failures"]),
        "mean_seconds": _mean([r["outcome"]["seconds"] for r in ran]),
        "mean_tokens": _mean([r["outcome"]["tokens"] for r in ran]),
        "mean_calls": _mean([r["outcome"]["calls"] for r in ran]),
        "small_edit_calls": _mean(small) if small else None,
        "by_category": by_category,
    }


ROWS: tuple[tuple[str, Callable[[dict[str, Any]], str]], ...] = (
    (
        "Cases passed",
        lambda s: (
            f"{s['passed']:.1f} / {s['cases']} ({s['runs']} runs)"
            if s["runs"] != s["cases"]
            else f"{s['passed']:.0f} / {s['cases']}"
        ),
    ),
    ("Not possible today", lambda s: str(s["impossible"])),
    ("Requests that failed", lambda s: str(s["failed_requests"])),
    ("Intent right (every case)", lambda s: _share(s, "intent")),
    ("Asked right (the cases about asking)", lambda s: _share(s, "asked")),
    ("Precision (changed parts that were allowed to change)", lambda s: _share(s, "precision")),
    ("Recall (parts that had to change, and did)", lambda s: _share(s, "recall")),
    ("Untouched parts identical (by passage)", lambda s: _share(s, "untouched")),
    (
        "Untouched parts byte-identical (raw text, information only)",
        lambda s: _share(s, "untouched_raw"),
    ),
    ("Part count right", lambda s: _share(s, "count")),
    ("Layout right", lambda s: _share(s, "layout")),
    ("Order and place of new parts right", lambda s: _share(s, "order")),
    ("Strings and lengths right", lambda s: _share(s, "strings")),
    ("Cited the right note", lambda s: _share(s, "cites")),
    ("Cases with no numbers from nowhere", lambda s: _share(s, "numbers")),
    (
        "Changed parts supported / flagged / not checked",
        lambda s: f"{s['support']['ok']} / {s['support']['flagged']} / {s['support']['unchecked']}",
    ),
    ("Judge said yes", lambda s: _share(s, "judge")),
    ("Mean model calls", lambda s: f"{s['mean_calls']:.1f}"),
    (
        "Mean model calls of a small edit",
        lambda s: "none" if s["small_edit_calls"] is None else f"{s['small_edit_calls']:.1f}",
    ),
    ("Mean tokens", lambda s: f"{s['mean_tokens']:.0f}"),
    ("Mean time (s)", lambda s: f"{s['mean_seconds']:.1f}"),
)


def render(label: str, configurations: list[dict[str, Any]], notes: list[str]) -> str:
    """The report as Markdown. Each configuration: its name, ``card`` and ``results``."""

    lines = [f"# Artifacts exam: {label}", ""]
    lines += [f"- {note}" for note in notes]
    lines += ["", "| | " + " | ".join(c["name"] for c in configurations) + " |"]
    lines.append("|---|" + "---|" * len(configurations))
    for name, read in ROWS:
        lines.append(f"| {name} | " + " | ".join(read(c["card"]) for c in configurations) + " |")
    for configuration in configurations:
        lines += ["", f"## {configuration['name']}", ""]
        card = configuration["card"]
        kinds = card["by_category"]
        lines.append(
            "Passed by kind of request: "
            + ", ".join(f"{name} {done:g}/{total}" for name, (done, total) in kinds.items())
        )
        lines.append("")
        if card.get("disagreed"):
            lines.append(
                "Runs disagreed (passed/runs): "
                + ", ".join(f"{case} {rate}" for case, rate in card["disagreed"].items())
            )
        for key, result in configuration["results"].items():
            if result["failures"]:
                lines.append(f"- {key} ({result['category']}): {'; '.join(result['why'])}")
        if not any(r["failures"] for r in configuration["results"].values()):
            lines.append("Every case passed.")
    return "\n".join(lines) + "\n"


def rescore(result: dict[str, Any], case: Case, library: dict[str, str]) -> dict[str, Any]:
    """A case's record scored again from what it stored: the measures, the checks and the
    reasons. The judge is not asked again; its answer stays as it was."""

    stored = result["outcome"]
    outcome = score.Outcome(
        **{
            **stored,
            "before": score.Version(**stored["before"]) if stored["before"] else None,
            "after": score.Version(**stored["after"]) if stored["after"] else None,
        }
    )
    resolved = case.resolved(result.get("subs", {}))
    broken = bool(outcome.failed or outcome.impossible)
    measures = {} if broken else score.measure(resolved, outcome, library)
    found = score.checks(resolved, outcome, measures)
    verdict = score.Verdict(**result["judgement"]) if result.get("judgement") else None
    failed = score.failures(resolved, outcome, found, verdict)
    why = score.explain(resolved, outcome, measures, found, verdict, result.get("judgeError", ""))
    return {
        **result,
        "measures": measures,
        "checks": found,
        "failures": failed,
        "why": why if failed else [],
    }


RESCORED = "Scored again from what the run stored; the judge's answers are as they were."


def main(argv: list[str] | None = None) -> int:
    """Write ``report.md`` again from a run's ``raw.json``."""

    parser = argparse.ArgumentParser(prog="python -m evals.artifacts.report", description=__doc__)
    parser.add_argument("folder", type=Path, help="a run's folder in evals/results")
    parser.add_argument("--rescore", action="store_true", help="score every case again first")
    args = parser.parse_args(argv)
    folder, rescoring = args.folder, args.rescore
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    cases = {case.id: case for case in score.load_cases()}
    configurations = raw["configurations"]
    if rescoring:
        # The first raw.json is kept, so that the run as it was scored stays on record.
        if not (folder / "raw.as-scored.json").exists():
            shutil.copy(folder / "raw.json", folder / "raw.as-scored.json")
        library = score.library_texts()
        for configuration in configurations:
            configuration["results"] = {
                key: rescore(result, cases[result["case"]], library)
                for key, result in configuration["results"].items()
            }
        if RESCORED not in raw["notes"]:
            raw["notes"].append(RESCORED)
        (folder / "raw.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    for configuration in configurations:
        for result in configuration["results"].values():
            result.setdefault("category", cases[result["case"]].category)
        configuration["card"] = scorecard(cases, configuration["results"])
    notes = raw["notes"] + ([] if raw.get("complete") else ["This run did not finish."])
    label = re.sub(r"^\d{8}-\d{4}-", "", folder.name)
    text = render(label, configurations, notes)
    (folder / "report.md").write_text(text, encoding="utf-8")
    print(f"Written to {folder / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
