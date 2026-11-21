from gunther.checks import (
    audit_verdict,
    coverage_of,
    issue_view,
    merge_verdicts,
    read_verdicts,
    verdict,
    verdicts_of,
)


def test_merge_preserves_support_for_the_same_claim_and_evidence():
    issue = {"claim": "A result.", "verdict": "unsupported", "note": "Outside the evidence."}
    old = verdicts_of(
        verdict("support", {"text": "A result [1].", "evidenceHash": "a",
                            "verdict": "unsupported", "issue": issue}),
        verdict("checker", {"text": "A different result [2].", "evidenceHash": "b",
                            "verdict": "supported"}),
    )
    active = verdicts_of(
        verdict("support", {"text": "A result [7].", "evidenceHash": "a"}),
        verdict("checker", {"text": "A result [7].", "evidenceHash": "a"}),
        verdict("checker", {"text": "A different result [2].", "evidenceHash": "new"}),
    )
    merged = merge_verdicts(active, old)
    assert set(merged) == set(active)
    assert [(r["text"], r["verdict"]) for r in merged.values()] == [
        ("A result [7].", "unsupported"), ("A result [7].", "unchecked"),
        ("A different result [2].", "unchecked"),
    ]
    assert issue_view(merged) == [issue]


def test_merge_advances_each_stage_and_a_replay_keeps_completed_verdicts():
    checker = verdicts_of(
        verdict("checker", {"text": "A result.", "verdict": "supported"}),
        verdict("checker", {"text": "Still to check."}),
    )
    body = "A result. Still to check."
    old = checker | verdicts_of(audit_verdict(body, "unavailable"))
    advanced = checker | verdicts_of(audit_verdict(body, "supported", []))
    merged = merge_verdicts(advanced, old)
    assert merged == advanced
    assert merge_verdicts(old, merged) == advanced
    coverage = coverage_of(merged, "section")
    assert coverage["audit"] == {"status": "complete", "claims": []}
    assert coverage["status"] == "partial" and coverage["checked"] == 1


def test_legacy_unchecked_issue_is_a_claim_and_newer_outcome_supersedes_it():
    history = {"claim": "A result.", "verdict": "unchecked", "note": "No verdict returned"}
    independent = {"claim": "A result.", "verdict": "unverified", "note": "From memory."}
    text = "A result [4]."
    old = read_verdicts({"issues": [history, independent]}, text)
    [claim] = coverage_of(old, "section")["claims"]
    assert claim["verdict"] == "unchecked" and claim["evidenceHash"] == "legacy"
    assert coverage_of(old, "section")["status"] == "partial"

    # Even a fresh pending verdict belongs to the current evidence rather than the old issue.
    new = verdicts_of(verdict("checker", {"text": text, "evidenceHash": "passage"}))
    merged = merge_verdicts(old | new, old, new)
    assert coverage_of(merged, "section")["claims"] == coverage_of(new, "section")["claims"]
    assert issue_view(merged) == [independent]


def test_legacy_table_claim_uses_its_own_exhibit_header():
    first = "| Label | Rate (%) |"
    second = "| Label | Rate (ms) |"
    row = "| Group | 12 [1] |"
    text = f"{first}\n| --- | --- |\n{row}\n\n{second}\n| --- | --- |\n{row}"
    old = read_verdicts({"coverage": {"claims": [
        {"text": row, "tableRow": [index, 2], "verdict": "unchecked"} for index in range(2)
    ]}}, text)
    new = verdicts_of(verdict("checker", {
        "text": row, "tableRow": [1, 2], "tableHeader": second,
        "evidenceHash": "passage", "verdict": "supported",
    }))
    merged = merge_verdicts(old | new, old, new)
    assert [(claim["tableHeader"], claim["verdict"]) for claim in merged.values()] == [
        (first, "unchecked"), (second, "supported"),
    ]
