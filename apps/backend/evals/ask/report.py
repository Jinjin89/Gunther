"""The scorecard: one row of numbers per configuration, and a line for every case that failed."""

from __future__ import annotations

from typing import Any

from evals.ask.score import Case, Judgement, unverified


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def scorecard(cases: dict[str, Case], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The numbers for one configuration. ``results`` holds each case's recorded run."""

    answered = [
        (cases[r.get("case", i)], r) for i, r in results.items() if not r["outcome"]["failed"]
    ]
    support = {"supported": 0, "partly": 0, "not": 0}
    unmarked, unverified_counts, times, tokens, calls = [], [], [], [], []
    covered = total = 0
    traps = 0
    search_ok = search_total = 0
    for case, result in answered:
        outcome, judged = result["outcome"], result["judgement"]
        if case.search != "any":
            search_total += 1
            search_ok += bool(result["checks"].get("searched"))
        traps += not result["checks"].get("no_trap", True)
        unverified_counts.append(unverified(outcome["answer"]))
        times.append(outcome["seconds"])
        tokens.append(outcome["tokens"])
        calls.append(outcome["calls"])
        if judged is not None:
            judgement = Judgement.model_validate(judged)
            for verdict in judgement.support:
                support[verdict.verdict] += 1
            unmarked.append(len(judgement.unmarked))
            wanted = len(case.must_mention)
            total += wanted
            covered += sum(([*judgement.mentioned, *[False] * wanted])[:wanted])
    # A case run more than once passes by its pass rate; its runs may disagree.
    runs: dict[str, list[bool]] = {}
    for key, result in results.items():
        runs.setdefault(result.get("case", key), []).append(not result["failures"])
    return {
        "cases": len(runs),
        "runs": len(results),
        "passed": sum(sum(passes) / len(passes) for passes in runs.values()),
        "disagreed": {
            case: f"{sum(passes)}/{len(passes)}"
            for case, passes in runs.items()
            if 0 < sum(passes) < len(passes)
        },
        "failed_requests": len(results) - len(answered),
        "searched_correctly": f"{search_ok}/{search_total}",
        "support": support,
        "unmarked_per_answer": _mean(unmarked),
        "trap_citations": traps,
        "unverified_per_answer": _mean(unverified_counts),
        "mentions_covered": f"{covered}/{total}",
        "mean_seconds": _mean(times),
        "mean_tokens": _mean(tokens),
        "mean_calls": _mean(calls),
    }


ROWS = (
    (
        "Cases passed",
        lambda s: f"{s['passed']:.1f} / {s['cases']} ({s['runs']} runs)"
        if s["runs"] != s["cases"]
        else f"{s['passed']:.0f} / {s['cases']}",
    ),
    ("Searched correctly", lambda s: s["searched_correctly"]),
    (
        "Citation support (supported / partly / not)",
        lambda s: f"{s['support']['supported']} / {s['support']['partly']} / {s['support']['not']}",
    ),
    ("Unmarked claims per answer", lambda s: f"{s['unmarked_per_answer']:.2f}"),
    ("Trap citations", lambda s: str(s["trap_citations"])),
    ("Unverified [?] per answer", lambda s: f"{s['unverified_per_answer']:.2f}"),
    ("Mentions covered", lambda s: s["mentions_covered"]),
    ("Mean time (s)", lambda s: f"{s['mean_seconds']:.1f}"),
    ("Mean tokens", lambda s: f"{s['mean_tokens']:.0f}"),
    ("Mean model calls", lambda s: f"{s['mean_calls']:.1f}"),
)


def research_lines(results: dict[str, dict[str, Any]]) -> list[str]:
    """For each research answer: how much of its budget it used, and what it cost."""

    lines: list[str] = []
    for case_id, result in results.items():
        outcome = result["outcome"]
        research = outcome["context"].get("research")
        if not research:
            continue
        used, limits = research.get("used", {}), research.get("limits", {})
        lines.append(
            f"- {case_id} research ({research.get('state')}, {research.get('budget') or 'no'} "
            f"budget): searches {used.get('searches', 0)}/{limits.get('searches', 0)}, "
            f"reads {used.get('reads', 0)}/{limits.get('reads', 0)}, "
            f"{outcome['seconds']:.0f} s, {outcome['tokens']} tokens, {outcome['calls']} calls"
        )
    return ["", "Research cases:", *lines] if lines else []


def render(
    label: str,
    configurations: list[dict[str, Any]],
    skipped: dict[str, list[str]],
    notes: list[str],
) -> str:
    """The report as Markdown. Each configuration: its name, ``card``, ``cases`` and ``results``."""

    lines = [f"# Ask exam: {label}", ""]
    lines += [f"- {note}" for note in notes]
    if skipped:
        names = [f"{case} needs {' and '.join(needs)}" for case, needs in skipped.items()]
        lines.append(f"- Skipped (a key is missing): {', '.join(names)}")
    lines += ["", "| | " + " | ".join(c["name"] for c in configurations) + " |"]
    lines.append("|---|" + "---|" * len(configurations))
    for name, read in ROWS:
        lines.append(f"| {name} | " + " | ".join(read(c["card"]) for c in configurations) + " |")
    for configuration in configurations:
        lines += ["", f"## {configuration['name']}", ""]
        disagreed = configuration["card"].get("disagreed")
        if disagreed:
            lines.append(
                "Runs disagreed (passed/runs): "
                + ", ".join(f"{case} {rate}" for case, rate in disagreed.items())
            )
        for case_id, result in configuration["results"].items():
            if result["failures"]:
                reason = result["outcome"]["failed"] or ", ".join(result["failures"])
                lines.append(f"- {case_id}: {reason}")
        if not any(r["failures"] for r in configuration["results"].values()):
            lines.append("Every case passed.")
        lines += research_lines(configuration["results"])
    return "\n".join(lines) + "\n"
