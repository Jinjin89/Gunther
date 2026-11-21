"""Saved verification verdicts and the projections used by readers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from typing import Any

Verdicts = dict[str, dict[str, Any]]
LEGACY_EVIDENCE = "legacy"
PENDING = {"unchecked", "unavailable"}
_RANK = {"unchecked": 0, "unavailable": 1, "supported": 2, "unsupported": 2}
_OUTCOME = ("verdict", "label", "reason", "issue", "findings")
_CITE = re.compile(r"\[(?:[ipd]:)?\d+(?:[ \t]*[,，、][ \t]*\d+)*\]")


def claim_text(text: str) -> str:
    plain = " ".join(_CITE.sub("", text).split())
    return re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", plain)


def verdict_key(row: Mapping[str, Any]) -> str:
    stage = row["stage"]
    text = row["text"]
    # The audit reads the whole section and needs to distinguish cited from uncited text.
    text = " ".join(_CITE.sub("[#]", text).split()) if stage == "audit" else claim_text(text)
    context = (row.get("tableRow"), claim_text(row.get("tableHeader", "")))
    digest = hashlib.sha256(repr((text, context)).encode()).hexdigest()
    return f"{stage}:{digest}:{row.get('evidenceHash') or ''}"


def verdict(stage: str, claim: Mapping[str, Any]) -> dict[str, Any]:
    result = {**claim, "stage": stage}
    label = result.get("verdict", "unchecked")
    if label == "unchecked" and (result.get("reason") or "").startswith("Check unavailable:"):
        label = "unavailable"
    if label not in _RANK:
        result["label"] = label
        label = "supported" if label in {"nonfactual", "inference"} else "unsupported"
    return {**result, "verdict": label}


def verdicts_of(*rows: dict[str, Any]) -> Verdicts:
    # Use the same rule when identical claims occur more than once in one section.
    return merge_verdicts(
        {verdict_key(row): row for row in rows}, *({verdict_key(row): row} for row in rows)
    )


def merge_verdicts(active: Verdicts, *prior: Verdicts) -> Verdicts:
    """Take the best outcome for each active key, using current claim and citation metadata."""
    # A current evidence key supersedes the legacy outcome for the same claim and stage.
    superseded = {
        f"{key.rsplit(':', 1)[0]}:{LEGACY_EVIDENCE}" for key, row in active.items()
        if row.get("evidenceHash") != LEGACY_EVIDENCE
    }
    winners: Verdicts = {}
    for source in (*prior, active):
        for key, row in source.items():
            if key in active and _RANK[row["verdict"]] > _RANK.get(
                winners.get(key, {}).get("verdict"), -1
            ):
                winners[key] = row
    return {
        key: {**{k: v for k, v in claim.items() if k not in _OUTCOME},
              **{k: winners[key][k] for k in _OUTCOME if k in winners[key]}}
        for key, claim in active.items() if key not in superseded
    }


def stage_rows(verdicts: Verdicts, stage: str) -> list[dict[str, Any]]:
    return [row for row in verdicts.values() if row["stage"] == stage]


def has_stage(verdicts: Verdicts, *stages: str) -> bool:
    return any(row["stage"] in stages for row in verdicts.values())


def complete(verdicts: Verdicts) -> bool:
    return all(row["verdict"] not in PENDING for row in verdicts.values())


def claim_view(row: Mapping[str, Any]) -> dict[str, Any]:
    view = {k: v for k, v in row.items() if k not in {"stage", "label", "issue"}}
    label = row.get("label", row["verdict"])
    view["verdict"] = {"unsupported": "not", "unavailable": "unchecked"}.get(label, label)
    return view


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unchecked = sum(row["verdict"] in PENDING for row in rows)
    checked = len(rows) - unchecked
    status = "skipped" if not rows else "complete"
    if unchecked:
        status = ("unavailable" if not checked and any(r["verdict"] == "unavailable" for r in rows)
                  else "partial")
    return {
        "status": status, "considered": len(rows), "checked": checked, "unchecked": unchecked,
        "claims": [claim_view(row) for row in rows],
    }


def coverage_of(verdicts: Verdicts, digest: str) -> dict[str, Any]:
    coverage = _coverage(stage_rows(verdicts, "checker"))
    coverage["answerHash"] = digest
    statuses = {coverage["status"], _coverage(stage_rows(verdicts, "legacy"))["status"]}
    if support := stage_rows(verdicts, "support"):
        coverage["support"] = _coverage(support)
        statuses.add(coverage["support"]["status"])
    if rows := stage_rows(verdicts, "audit"):
        audit = rows[0]
        status = {"unavailable": "unavailable", "unchecked": "partial"}.get(
            audit["verdict"], "complete"
        )
        coverage["audit"] = {"status": status, "claims": audit.get("findings")}
        statuses.add(status)
    for status in ("unavailable", "partial"):
        if status in statuses:
            coverage["status"] = status
            break
    return coverage


def issue_view(verdicts: Verdicts) -> list[dict[str, str]]:
    issues = [
        {"claim": claim, "verdict": "unsourced", "note": "No source is cited for this."}
        for audit in stage_rows(verdicts, "audit") for claim in audit.get("findings") or []
    ]
    seen = {claim_text(issue["claim"]) for issue in issues}
    for stage in ("finding", "support", "checker"):
        for row in stage_rows(verdicts, stage):
            if (issue := row.get("issue")) and (plain := claim_text(issue["claim"])) not in seen:
                issues.append(issue)
                seen.add(plain)
    return issues


def finding(issue: Mapping[str, str]) -> dict[str, Any]:
    return verdict("finding", {
        "text": issue["claim"], "verdict": "unsupported", "issue": dict(issue),
    })


def audit_verdict(text: str, outcome: str, findings: list[str] | None = None) -> dict[str, Any]:
    return verdict("audit", {"text": text, "verdict": outcome, "findings": findings})


def read_verdicts(record: Mapping[str, Any], text: str) -> Verdicts:
    """Convert a historical check at a storage boundary."""
    if "verdicts" in record:
        return record["verdicts"] or {}
    coverage = record.get("coverage") or {}
    issues = record.get("issues") or []
    rows = [finding(issue) for issue in issues if issue.get("verdict") != "unchecked"]
    pending = {claim_text(issue["claim"]): issue for issue in issues
               if issue.get("verdict") == "unchecked"}
    matched = set()
    table_headers = {}
    if any("tableRow" in claim and "tableHeader" not in claim
           for claim in coverage.get("claims") or []):
        # Older Checker rows recorded the exhibit and row position. Read its header there.
        from gunther.agent import check_claims

        table_headers = {tuple(row["tableRow"]): row["tableHeader"]
                         for row in check_claims(text, {}) if "tableRow" in row}
    for stage, data in (("checker", coverage), ("support", coverage.get("support") or {})):
        claims = data.get("claims") or []
        status = data.get("status")
        for claim in claims:
            if "text" not in claim:
                continue
            if claim.get("verdict") == "unchecked" and (
                status == "failed" or (stage == "support" and status == "unavailable")
            ):
                claim = {**claim, "verdict": "unavailable"}
            evidence = claim.get("evidenceHash") or LEGACY_EVIDENCE
            row = verdict(stage, {**claim, "evidenceHash": evidence})
            position = tuple(row.get("tableRow", ()))
            if "tableHeader" not in row and (header := table_headers.get(position)):
                row["tableHeader"] = header
            if stage == "checker":
                plain = claim_text(row["text"].replace("[?]", ""))
                key = plain if plain in pending else plain[:300].rstrip()
                if key in pending:
                    matched.add(key)
                    if row["verdict"] in PENDING:
                        row["issue"] = pending[key]
            rows.append(row)
        if not claims and status in {"partial", "failed", "unavailable"} and (
            stage != "checker" or not pending
        ):
            rows.append(verdict("legacy", {"text": text, "verdict": (
                "unchecked" if status == "partial" else "unavailable"
            )}))
    rows.extend(verdict("checker", {
        "text": issue["claim"], "evidenceHash": LEGACY_EVIDENCE, "verdict": "unchecked",
        "reason": issue.get("note", ""), "issue": issue,
    }) for key, issue in pending.items() if key not in matched)
    if audit := coverage.get("audit"):
        status = {"complete": "supported", "partial": "unchecked"}.get(
            audit["status"], "unavailable"
        )
        rows.append(audit_verdict(text, status, audit.get("claims")))
    return verdicts_of(*rows)


def map_refs(
    state: Verdicts, numbers: Mapping[int, int], translate: Callable[[str], str]
) -> Verdicts:
    return {
        key: {
            **row, "text": translate(row["text"]),
            **({"refs": [numbers[ref] for ref in row["refs"] if ref in numbers]}
               if "refs" in row else {}),
            **({"tableHeader": translate(row["tableHeader"])} if "tableHeader" in row else {}),
        }
        for key, row in state.items()
    }
