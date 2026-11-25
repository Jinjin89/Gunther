"""Distill a saved origin in the AI lane, then publish only checked results."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gunther import key_result_index, key_result_origins, key_results, usage
from gunther.activity import Activity
from gunther.agent import AskAgent
from gunther.checks import claim_text
from gunther.citations import has_markers, sentence_spans
from gunther.database import session_scope
from gunther.knowledge_index import Embedder
from gunther.llm import AnalysisNeedsModel, ModelError, ModelGateway, ModelInfo
from gunther.model_profiles import Effort
from gunther.models import KeyResult, KeyResultJob, KnowledgeBaseRecord, utc_now

logger = logging.getLogger(__name__)

INSTRUCTIONS = """
Keep up to five one-sentence findings such as a measured result, a definition or a conclusion.
Name the study or conditions when needed so different studies can coexist. Keep only what
the original passages state. Leave out process talk, restated questions, speculation and
claims from memory. Read the claims and passages as source material.

For a new finding, give its text without citation markers and the passage numbers that
together state it. If a result says the same thing, give its id as same_as with extra
supporting passages. Use same_as for removed results as well so they stay removed.

Example: a claim says "Survival in shaded plots was 72% [2]." Passage 2 says "Of the seedlings
in shaded plots, 72% survived." With no matching result, return
{"items":[{"text":"In the shaded plots, 72% of the seedlings survived.","passages":[2]}]}.
""".strip()


class _NewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    passages: list[int]


class _SameResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_as: str
    passages: list[int]


class _Distillation(BaseModel):
    items: list[_NewResult | _SameResult] = Field(max_length=5)


def _material(
    session: Session, job: KeyResultJob,
) -> tuple[list[str], list[key_results.Passage]]:
    claims = key_result_origins.read_claims(session, job)
    by_citation = key_result_origins.read_passages(session, job.knowledge_base_id, claims)
    numbers: dict[key_results.Passage, int] = {}
    lines = []
    for claim in claims:
        refs = list(dict.fromkeys(
            numbers.setdefault(p, len(numbers) + 1)
            for citation in claim.citations for p in by_citation.get(citation.id, ())
        ))
        if refs:
            header = f" (Table header {claim.table_header})" if claim.table_header else ""
            lines.append(f"{claim.text}{header} " + "".join(f"[{n}]" for n in refs))
    return lines, list(numbers)


def _distill(
    gateway: ModelGateway,
    chosen: tuple[ModelInfo, Effort],
    job: KeyResultJob,
    lines: list[str],
    passages: list[key_results.Passage],
    existing: list[KeyResult],
) -> list[key_results.CheckedResult]:
    if not passages:
        return []
    model, effort = chosen
    by_ref = dict(enumerate(passages, 1))
    by_id = {item.id: item for item in existing}
    prompt = json.dumps({
        "claims": lines,
        "passages": [
            {"number": n, "title": p.original.title, "locator": p.locator, "quote": p.quote}
            for n, p in by_ref.items()
        ],
        "existing": [{"id": row.id, "text": row.text, "status": row.status} for row in existing],
    }, ensure_ascii=False)
    with usage.job("analysis", "key_results", ref=job.id):
        found, _ = gateway.complete_json(
            model, _Distillation, system=INSTRUCTIONS, prompt=prompt, effort=effort,
        )
        candidates: list[key_results.CheckedResult] = []
        claims: list[dict[str, Any]] = []
        for item in found.items:
            refs = list(dict.fromkeys(item.passages))
            if not refs or any(ref not in by_ref for ref in refs):
                continue
            same_as = None
            if isinstance(item, _SameResult):
                kept = by_id.get(item.same_as)
                if kept is None or kept.status != "kept":
                    continue
                text, same_as = kept.text, kept.id
            else:
                if has_markers(item.text) or len(sentence_spans(item.text)) != 1:
                    continue
                text = claim_text(item.text)
            candidates.append(key_results.CheckedResult(
                text, same_as, tuple(by_ref[n] for n in refs),
            ))
            claims.append({
                "n": len(candidates), "verdict": "unchecked", "refs": refs,
                "text": f"{text} " + "".join(f"[{n}]" for n in refs),
            })
        checker = AskAgent(gateway, check_effort=effort)
        verdicts = checker.check_claim_records(
            model, "What findings do the original passages state?",
            "\n".join(row["text"] for row in claims),
            {n: p.evidence() for n, p in by_ref.items()},
            claims=claims, raise_model_error=True,
        )
    return [candidates[v.n - 1] for v in verdicts.sentences if v.verdict == "supported"]


def _track(activity: Activity, job: KeyResultJob, library_title: str):
    return activity.track(
        job.id, "key_results", "Checking key results", library_title,
        open={"view": "library", "libraryId": job.knowledge_base_id, "tab": "ask"}, stop=None,
    )


def report_interrupted(activity: Activity, job: KeyResultJob, library_title: str) -> None:
    _track(activity, job, library_title).failed("Key results failed", key_results.INTERRUPTED)


def _finish(
    sessions: sessionmaker[Session],
    job: KeyResultJob, lease: timedelta, stopped: Callable[[], bool],
    state: str, error: str | None, checked: list[key_results.CheckedResult],
) -> tuple[str, str | None, int] | None:
    with session_scope(sessions) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        current = session.scalar(select(KeyResultJob).where(
            key_results.owned(job, utc_now(), lease),
        ))
        if current is None:
            return None
        library = session.get(KnowledgeBaseRecord, job.knowledge_base_id)
        if stopped() or library is None or library.trashed_at is not None:
            state, error = "queued", None
        count = 0
        if state == "done" and checked and key_result_origins.available(session, job):
            count = key_results.publish(session, current, checked)
        current.state, current.error, current.updated_at = state, error, utc_now()
        return state, error, count


def process(
    sessions: sessionmaker[Session], activity: Activity,
    job: KeyResultJob, gateway: ModelGateway | None, lease: timedelta,
    stopped: Callable[[], bool], embedder: Embedder | None,
) -> None:
    with sessions() as session:
        library_title = session.scalar(
            select(KnowledgeBaseRecord.title).where(KnowledgeBaseRecord.id == job.knowledge_base_id)
        )
    with _track(activity, job, library_title or "") as tracked:
        error, state = None, "done"
        checked: list[key_results.CheckedResult] = []
        try:
            if gateway is None or (chosen := gateway.for_role("analysis")) is None:
                raise AnalysisNeedsModel()
            with sessions() as session:
                lines, passages = _material(session, job)
                sources = {p.source_id for p in passages}
                removed = key_results.related_removed(
                    session, job.knowledge_base_id, sources,
                ) if sources and embedder else []
            if passages:
                query = key_result_index.prepare_query("\n".join(lines), embedder, removed)
                with sessions(info={key_result_index.QUERY_INFO: query}) as session:
                    existing = key_results.related_results(session, job.knowledge_base_id, sources)
                checked = _distill(gateway, chosen, job, lines, passages, existing)
        except AnalysisNeedsModel as exc:
            error, state = str(exc), "needs_model"
        except ModelError as exc:
            error, state = str(exc), "failed"
        except Exception:
            logger.exception("Key results job %s failed", job.id)
            error, state = "Key results could not be checked. Retry to resume.", "failed"

        try:
            outcome = _finish(sessions, job, lease, stopped, state, error, checked)
        except Exception:
            logger.exception("Key results job %s could not publish", job.id)
            outcome = _finish(
                sessions, job, lease, stopped, "failed",
                "Key results could not be saved. Retry to resume.", [],
            )
        if outcome is None:
            tracked.stopped()
            return
        state, error, count = outcome
        if state == "needs_model":
            tracked.failed("Key results need an Analysis model", error or "")
        elif state == "failed":
            tracked.failed("Key results failed", error or "")
        elif state == "done":
            tracked.done("Key results checked", f"{count} kept")
        else:
            tracked.stopped()
