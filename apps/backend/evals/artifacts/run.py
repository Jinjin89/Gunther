"""The Artifacts exam: fixed messages sent to a deck and a report built from the demo library.

Run from ``apps/backend``: ``python -m evals.artifacts.run --path baseline --label baseline``.
See the README. It is a developer tool: nothing in the app or the sidecar uses it.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from evals.artifacts import report, score
from evals.ask.run import (
    NO_MODEL,
    RESULTS,
    ExamError,
    _probe_gateway,
    ensure_template,
    make_app,
    write_settings_file,
)
from gunther.llm import ClientFactory, ModelGateway
from gunther.model_profiles import EFFORTS, Effort
from gunther.models import Artifact
from gunther.outputs import section_hash, split_document
from gunther.schemas import OutputModelOut
from gunther.service import KnowledgeService

PATHS = ("baseline", "agent")
# A fixture that lacks what the cases rely on is built again, this many times at most.
FIXTURE_TRIES = 3
# What today's path cannot do, said the way the scorecard says it.
NOT_POSSIBLE = {
    "ask": "it never asks; it starts building at once.",
    "answer": (
        "it cannot answer a question; every message revises the document and saves a version."
    ),
    "structure": "it cannot add, remove or move a part; it can only rebuild the whole document.",
}


@dataclass
class Options:
    settings: Path | None = None
    models: list[str] = field(default_factory=list)
    writer_effort: Effort | None = None
    judge: str | None = None
    only: list[str] = field(default_factory=list)
    label: str = "exam"
    paths: list[str] = field(default_factory=lambda: ["baseline"])
    reseed: bool = False
    rebuild_fixtures: bool = False
    fixtures_only: bool = False
    repeat: int = 1
    jobs: int = 1


@dataclass(frozen=True)
class Configuration:
    path: str
    model: str
    writer_effort: Effort | None

    @property
    def name(self) -> str:
        return f"{self.path}: {self.model} (write {self.writer_effort or 'default'})"


# Counting model calls ---------------------------------------------------------------------


@dataclass
class Usage:
    calls: int = 0
    tokens: int = 0
    seconds: float = 0.0


class Counter:
    """Counts the model calls and tokens a gateway makes while a window is open.

    It listens where the gateway talks to the provider (``_stream``): every call goes
    through it, whoever made it. The output paths keep no trace, as Ask does.
    """

    def __init__(self, gateway: ModelGateway) -> None:
        self._usage: Usage | None = None
        self._lock = threading.Lock()
        real = gateway._stream

        def counted(*args: Any, **kwargs: Any) -> Any:
            tokens = 0
            try:
                result = real(*args, **kwargs)
                used = result[3] if isinstance(result, tuple) and len(result) > 3 else None
                used = used or {}
                tokens = used.get("total_tokens") or (
                    used.get("prompt_tokens", 0) + used.get("completion_tokens", 0)
                )
                return result
            finally:
                with self._lock:
                    if self._usage is not None:
                        self._usage.calls += 1
                        self._usage.tokens += tokens

        gateway._stream = counted  # type: ignore[method-assign]

    @contextmanager
    def window(self) -> Iterator[Usage]:
        used = Usage()
        started = time.perf_counter()
        with self._lock:
            self._usage = used
        try:
            yield used
        finally:
            with self._lock:
                self._usage = None
            used.seconds = time.perf_counter() - started


# One app, one copy of the library ---------------------------------------------------------


def _traced(event: dict[str, Any]) -> list[str]:
    """A decision or a finished action of the agent as a line of the case's notes."""

    kind = event.get("type")
    if kind == "decision":
        return ["decided: " + " · ".join(a.get("label", "") for a in event.get("actions", []))]
    if kind == "action" and event.get("state") in ("done", "failed", "skipped"):
        note = str(event.get("note") or "")[:300]
        return [f"  {event['state']}: {event.get('label', '')}" + (f" — {note}" if note else "")]
    return []


@dataclass
class Session:
    """An app on its own copy of the demo library, with a client that is signed in."""

    client: TestClient
    headers: dict[str, str]
    base_id: str
    workspace_id: str
    service: KnowledgeService
    gateway: ModelGateway
    counter: Counter
    config: Configuration

    def stream(
        self, tail: str, body: dict[str, Any], trace: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """POST to a streaming route of the library and return its last event: (name, data).
        ``trace`` collects the agent's decisions and finished actions, one line each."""

        url = f"/api/knowledge-bases/{self.base_id}/{tail}"
        events: list[tuple[str, dict[str, Any]]] = []
        name = ""
        with self.client.stream("POST", url, headers=self.headers, json=body) as response:
            if response.status_code != 200:
                response.read()
                return "error", {"status": response.status_code, "detail": response.text[:300]}
            for line in response.iter_lines():
                if line.startswith("event: "):
                    name = line[7:]
                elif line.startswith("data: "):
                    events.append((name, json.loads(line[6:])))
                    if trace is not None:
                        trace += _traced(events[-1][1])
        return events[-1] if events else ("error", {"status": 0, "detail": "No result came back."})

    def clone(self, saved: dict[str, Any], content: str | None = None) -> score.Version:
        """A copy of a fixture as the first version of a new lineage.

        ``content`` is the text to save instead of the fixture's (a case may break a word);
        the Checker's findings stay with the parts whose text is unchanged.
        """

        text = saved["content"] if content is None else content
        kept = {item["hash"]: item for item in saved["checks"]}
        parts = split_document(text, saved["kind"])[1]
        checks = [kept[h] for h in (section_hash(part) for part in parts) if h in kept]
        nonce = secrets.token_hex(8)
        model = saved.get("model")
        artifact = self.service._save_output(
            workspace_id=self.workspace_id,
            base_id=self.base_id,
            client_request_id=f"exam_clone_{nonce}",
            fingerprint=self.service._output_fingerprint(
                self.workspace_id, self.base_id, "exam-clone", {"nonce": nonce}
            ),
            parent_id=None,
            kind=saved["kind"],
            style=saved["style"],
            audience=saved["audience"],
            title=saved["title"],
            brief=saved["brief"],
            origin="build",
            content=text,
            scope=saved["scope"],
            inputs=saved["inputs"],
            outline=saved["outline"],
            citations=saved["citations"],
            snapshots=[],
            model=OutputModelOut(**model) if model else None,
            checks=checks,
            made_by=saved["made_by"],
        )
        return score.Version.from_artifact(artifact.model_dump(mode="json", by_alias=True))

    def head(self, version: score.Version) -> score.Version:
        """The newest version of the lineage a version belongs to."""

        listed = self.client.get(
            f"/api/knowledge-bases/{self.base_id}/artifacts", headers=self.headers
        ).json()
        mine = next(item for item in listed if item["id"] == version.id)
        newest = max(
            (item for item in listed if item["lineageId"] == mine["lineageId"]),
            key=lambda item: item["versionNumber"],
        )
        full = self.client.get(
            f"/api/knowledge-bases/{self.base_id}/artifacts/{newest['id']}", headers=self.headers
        )
        return score.Version.from_artifact(full.json())


def write_exam_settings(target: Path, source: Path | None) -> Path:
    """The Ask exam's copy of the settings, and outputs are made with their skills, which is
    what the app does unless a developer turns it off."""

    write_settings_file(target, source)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["developer"]["output_skills"] = True
    payload["developer"].setdefault("output_method", "skills")
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


@contextmanager
def open_session(
    template: Path,
    info: dict[str, Any],
    settings_source: Path | None,
    config: Configuration,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
) -> Iterator[Session]:
    """An app on a fresh copy of the template. The library is already read and indexed, so
    the processing worker stays off: nothing but the case is asking the models."""

    token = secrets.token_hex(32)
    headers = {"X-Gunther-Token": token}
    with tempfile.TemporaryDirectory(prefix="gunther-exam-") as scratch:
        copy = Path(scratch) / "library"
        shutil.copytree(template, copy)
        settings_file = write_exam_settings(
            Path(scratch) / "service-settings.json", settings_source
        )
        app = make_app(
            copy,
            settings_file,
            token,
            factory,
            {**(overrides or {}), "processing_worker_enabled": False},
        )
        with TestClient(app) as client:
            found = client.get("/api/workspace/bootstrap", headers=headers).json()["workspaceId"]
            gateway = client.app.state.models
            yield Session(
                client,
                {**headers, "X-Gunther-Workspace-Id": found},
                info["knowledgeBaseId"],
                found,
                client.app.state.knowledge_service,
                gateway,
                Counter(gateway),
                config,
            )


# Fixtures ---------------------------------------------------------------------------------


def describe(name: str, data: dict[str, Any]) -> str:
    """Why a stream did not end in a version, in one line."""

    if name == "error":
        return f"HTTP {data.get('status')}: {str(data.get('detail', ''))[:300]}"
    return "It was stopped." if name == "stopped" else f"The stream ended with {name}."


def export_version(service: KnowledgeService, artifact_id: str) -> dict[str, Any]:
    """Everything ``_save_output`` needs to save a copy of a version, as plain data."""

    with service.sessions() as db:
        row = db.get(Artifact, artifact_id)
        assert row is not None
        provenance = json.loads(row.provenance_json)
        return {
            "kind": row.kind,
            "style": row.style,
            "audience": row.audience,
            "title": row.title,
            "brief": row.brief,
            "content": row.content,
            "scope": json.loads(row.scope_json),
            "inputs": json.loads(row.inputs_json),
            "outline": json.loads(row.outline_json),
            "citations": json.loads(row.citations_json),
            "checks": list(KnowledgeService._latest_check_records(db, row.id).values()),
            "model": provenance.get("model"),
            "made_by": KnowledgeService._made_by(row),
        }


def build_fixture(
    session: Session, fixture: score.Fixture, say: Callable[[str], None]
) -> dict[str, Any]:
    """Build a fixture through the real build path with its fixed outline; built again when
    the text lacks what the cases rely on."""

    effort = session.config.writer_effort
    model = {"model": session.config.model, **({"effort": effort} if effort else {})}
    calls = tokens = 0
    started = time.perf_counter()
    lacking: list[str] = []
    for attempt in range(1, FIXTURE_TRIES + 1):
        body = {
            **fixture.request(),
            "clientRequestId": f"exam_fixture_{secrets.token_hex(6)}",
            **model,
        }
        with session.counter.window() as used:
            name, data = session.stream("outputs/build/stream", body)
        calls, tokens = calls + used.calls, tokens + used.tokens
        if name != "done":
            lacking = [describe(name, data)]
        else:
            lacking = fixture.problems(score.Version.from_artifact(data).parts)
            if not lacking:
                saved = export_version(session.service, data["id"])
                saved["built"] = {
                    "attempts": attempt,
                    "seconds": round(time.perf_counter() - started),
                    "calls": calls,
                    "tokens": tokens,
                    "model": session.config.model,
                    "effort": effort,
                }
                return saved
        say(f"  The {fixture.name} fixture, try {attempt} of {FIXTURE_TRIES}: {'; '.join(lacking)}")
    raise ExamError(
        f"The {fixture.name} fixture could not be built: {'; '.join(lacking)}. "
        "It is built through the real path, so a failure here is a finding in itself."
    )


def fixture_file(root: Path, template: Path, config: Configuration, fixture: score.Fixture) -> Path:
    """Where a fixture is kept: one file for each (library, model, fixture)."""

    model = re.sub(r"[^A-Za-z0-9]+", "-", config.model).strip("-")
    key = [template.name.removeprefix("template-"), model, config.writer_effort or "default"]
    return root / f"fixture-{fixture.name}-{'-'.join(key)}-{fixture.hash}.json"


def ensure_fixtures(
    root: Path,
    template: Path,
    info: dict[str, Any],
    settings_source: Path | None,
    config: Configuration,
    wanted: list[score.Fixture],
    options: Options,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable[[str], None],
) -> dict[str, dict[str, Any]]:
    """The fixtures the cases start from, built once and reused while the library, the model
    and the fixture's definition are unchanged. Missing ones are built side by side."""

    saved: dict[str, dict[str, Any]] = {}
    missing: list[score.Fixture] = []
    for fixture in wanted:
        path = fixture_file(root, template, config, fixture)
        if options.rebuild_fixtures and path.exists():
            path.unlink()
        if path.exists():
            say(f"Reusing the {fixture.name} fixture {path.name}.")
            saved[fixture.name] = json.loads(path.read_text(encoding="utf-8"))
        else:
            missing.append(fixture)
    failures: list[BaseException] = []

    def build(fixture: score.Fixture) -> None:
        try:
            say(
                f"Building the {fixture.name} fixture ({len(fixture.outline)} parts); "
                "this takes a while."
            )
            with open_session(
                template, info, settings_source, config, factory, overrides
            ) as session:
                made = build_fixture(session, fixture, say)
            fixture_file(root, template, config, fixture).write_text(
                json.dumps(made, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            saved[fixture.name] = made
            took = made["built"]
            say(
                f"The {fixture.name} fixture is built: {took['seconds']} s, {took['calls']} model "
                f"calls, {took['attempts']} {'try' if took['attempts'] == 1 else 'tries'}."
            )
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=build, args=(fixture,), daemon=True) for fixture in missing]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
    return saved


# Today's path -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """A message as the old screen would send it. ``route`` is none when it cannot."""

    route: str
    tail: str = ""
    body: dict[str, Any] = field(default_factory=dict)
    why: str = ""


def route_of(case: score.Case) -> tuple[str, str]:
    """How the old path takes this case, and when it cannot, why.

    A change is a revision, of the part the person selected or else of the whole document.
    A change upstream of the parts is a rebuild, with the message as the brief. A new
    document is a build. What the old path has no way to say (asking, answering, adding,
    removing or moving a part) is not possible.
    """

    expect, today = case.expect, case.today
    structure = NOT_POSSIBLE["structure"]
    asking = expect.asks_rule == "must" or expect.intent == ("ask",)
    if today.route:
        if today.route != "none":
            return today.route, ""
        reason = (
            NOT_POSSIBLE["ask"]
            if asking
            else NOT_POSSIBLE["answer"]
            if expect.intent == ("answer",)
            else structure
        )
        return "none", today.why or reason
    if asking:
        return "none", NOT_POSSIBLE["ask"]
    if expect.intent == ("answer",):
        return "none", NOT_POSSIBLE["answer"]
    if expect.intent == ("make",) or case.artifact == "none":
        return "new", ""
    if case.category == "upstream":
        return "rebuild", ""
    if expect.count_delta or expect.order or expect.new_at:
        return "none", structure
    return "revise", ""


def edited_outline(
    outline: list[dict[str, Any]], edits: tuple[score.Edit, ...]
) -> list[dict[str, Any]]:
    """The version's outline with the person's changes to it, as the outline editor sends it."""

    items = [dict(item) for item in outline]
    for edit in edits:
        item = items[edit.part - 1]
        for key in ("heading", "goal", "layout"):
            value = getattr(edit, key)
            if value is not None:
                item[key] = value
    return items


def baseline_request(
    case: score.Case,
    turn: score.Turn,
    head: score.Version | None,
    config: Configuration,
    route: str,
    request_id: str,
) -> Request:
    """The request the old path makes for one message, from what the old screen knows: the
    message, the part selected (never the text), and the version's own settings."""

    model: dict[str, Any] = {"model": config.model}
    if config.writer_effort:
        model["effort"] = config.writer_effort
    if route == "revise":
        assert head is not None
        body: dict[str, Any] = {"clientRequestId": request_id, "instruction": turn.message, **model}
        focus = turn.focus
        if focus and focus.selected and focus.section:
            body["sectionIndex"] = focus.section - 1
        return Request("revise", f"artifacts/{head.id}/revise/stream", body)
    body = {"clientRequestId": request_id, "scope": {"mode": "library"}, **model}
    if route == "new":
        today = case.today
        body |= {"kind": today.kind or "slides", "audience": today.audience, "brief": turn.message}
        if today.use:
            body["use"] = today.use
        return Request("new", "outputs/build/stream", body)
    assert head is not None
    body |= {"kind": head.kind, "audience": head.audience, "supersedesArtifactId": head.id}
    if head.kind == "report":
        body["style"] = head.style or "overview"
    elif head.use:
        body["use"] = head.use
    if route == "rebuild":
        body["brief"] = turn.message
    else:
        body["brief"] = head.brief
        body["outline"] = edited_outline(head.outline, case.today.edit)
    return Request(route, "outputs/build/stream", body)


def run_baseline_case(
    session: Session, case: score.Case, start: score.Version | None
) -> score.Outcome:
    """Send a case's messages the way today's path would, and report what came of the last."""

    route, why = route_of(case)
    if route == "none":
        return score.Outcome(impossible=why, route="not possible")
    outcome = score.Outcome(route=route)
    turns = [*case.history, score.Turn(case.message, case.focus)]
    head = start
    for number, turn in enumerate(turns, start=1):
        last = number == len(turns)
        request = baseline_request(
            case,
            turn,
            head,
            session.config,
            route if last else "revise",
            f"exam_{secrets.token_hex(8)}",
        )
        with session.counter.window() as used:
            name, data = session.stream(request.tail, request.body)
        if name != "done":
            outcome.failed = describe(name, data)
            return outcome
        saved = score.Version.from_artifact(data)
        if last:
            outcome.before, outcome.after = head, saved
            outcome.intent = "change" if head else "make"
            outcome.calls, outcome.tokens, outcome.seconds = used.calls, used.tokens, used.seconds
        head = saved
    return outcome


# The agent -----------------------------------------------------------------------------------


def turn_body(turn: score.Turn, config: Configuration, request_id: str) -> dict[str, Any]:
    """A message to the Artifacts agent: the text, what the person points at (a part counted
    from 1, selected text, or only the part on screen), and the model under test."""

    body: dict[str, Any] = {
        "clientRequestId": request_id,
        "kind": "message",
        "text": turn.message,
        "model": config.model,
    }
    if config.writer_effort:
        body["effort"] = config.writer_effort
    focus = turn.focus
    if focus is not None:
        body["focus"] = {
            **({"section": focus.section} if focus.section else {}),
            **({"quote": focus.quote} if focus.quote else {}),
            "selected": focus.selected,
        }
    return body


def open_conversation(session: Session, start: score.Version | None) -> str:
    """A conversation for a case: with the case's document as its artifact (brought in as the
    app brings in a device's conversation), or empty when the case starts from nothing."""

    conversation_id = f"exam_{secrets.token_hex(6)}"
    base = f"/api/knowledge-bases/{session.base_id}/artifact-conversations"
    if start is None:
        made = session.client.post(base, headers=session.headers, json={"id": conversation_id})
    else:
        turn = {
            "instruction": "Start from this document.",
            "status": "saved",
            "artifactId": start.id,
        }
        made = session.client.post(
            f"{base}/import",
            headers=session.headers,
            json={"conversations": [{"id": conversation_id, "title": "Exam", "turns": [turn]}]},
        )
    if made.status_code >= 300:
        raise ExamError(f"The conversation could not be made: {made.text[:300]}")
    return conversation_id


def run_agent_case(
    session: Session, case: score.Case, start: score.Version | None
) -> score.Outcome:
    """Send a case's messages through the Artifacts agent's conversation endpoint and report
    what came of the last: it asked, answered, replied, paused, or saved a version."""

    outcome = score.Outcome(route="agent")
    conversation_id = open_conversation(session, start)
    tail = f"artifact-conversations/{conversation_id}/messages/stream"
    turns = [*case.history, score.Turn(case.message, case.focus)]
    head = start
    for number, turn in enumerate(turns, start=1):
        last = number == len(turns)
        body = turn_body(turn, session.config, f"exam_{secrets.token_hex(8)}")
        outcome.notes.append(f"turn {number}: {turn.message[:200]}")
        with session.counter.window() as used:
            name, data = session.stream(tail, body, outcome.notes)
        if name != "done":
            outcome.failed = describe(name, data)
            return outcome
        saved = score.Version.from_artifact(data["artifact"]) if data.get("artifact") else None
        if not last:
            head = saved or head
            continue
        outcome.before, outcome.after = head, saved
        outcome.calls, outcome.tokens, outcome.seconds = used.calls, used.tokens, used.seconds
        outcome.reply = data.get("reply") or ""
        ended = data["outcome"]
        if ended == "asked":
            outcome.intent, outcome.asked = "ask", True
            outcome.question = (data.get("question") or {}).get("text", outcome.reply)
            outcome.options = list((data.get("question") or {}).get("options", []))
        elif ended == "saved":
            outcome.intent = "change" if head else "make"
        elif ended in ("answered", "replied"):
            outcome.intent = "answer"
        else:
            outcome.failed = f"The turn paused before it finished: {outcome.reply[:300]}"
    return outcome


RUNNERS: dict[str, Callable[[Session, score.Case, score.Version | None], score.Outcome] | None] = {
    "baseline": run_baseline_case,
    "agent": run_agent_case,
}


# One case, one configuration --------------------------------------------------------------


def run_case(
    session: Session,
    case: score.Case,
    saved: dict[str, dict[str, Any]],
    runner: Callable[[Session, score.Case, score.Version | None], score.Outcome],
    judge_ref: str,
    library: dict[str, str],
) -> dict[str, Any]:
    """Start a case from its own copy of a fixture, run it, and score it."""

    subs: dict[str, str] = {}
    resolved = case
    try:
        start = None
        if case.artifact != "none":
            fixture = saved[case.artifact]
            content, subs = score.prepare(case, fixture["content"], fixture["kind"])
            start = session.clone(fixture, content)
        resolved = case.resolved(subs)
        outcome = runner(session, resolved, start)
    except Exception as error:  # one broken case must not end an hours-long run
        outcome = score.Outcome(failed=f"The case could not run: {type(error).__name__}: {error}")
    measures: dict[str, Any] = {}
    if not (outcome.failed or outcome.impossible):
        measures = score.measure(resolved, outcome, library)
    found = score.checks(resolved, outcome, measures)
    verdict, error = None, ""
    model = session.gateway.get(judge_ref)
    if found["answered"] and resolved.expect.judge and model is not None:
        verdict, error = score.judge(session.gateway, model, resolved, outcome, measures)
    failed = score.failures(resolved, outcome, found, verdict)
    return {
        "case": case.id,
        "category": case.category,
        "outcome": asdict(outcome),
        "measures": measures,
        "checks": found,
        "judgement": verdict.model_dump() if verdict else None,
        "judgeError": error,
        "failures": failed,
        "why": score.explain(resolved, outcome, measures, found, verdict, error) if failed else [],
        "subs": subs,
    }


def weight(case: score.Case) -> int:
    """A guess at how long a case takes, so that the long ones start first: a change upstream
    of the parts, an outline edit or a new document remakes everything, and every earlier
    message is another turn."""

    remakes = case.category == "upstream" or case.artifact == "none"
    remakes = remakes or case.today.route in ("outline", "rebuild", "new")
    return (len(case.history) + 1) * (3 if remakes else 1)


def run_configuration(
    template: Path,
    info: dict[str, Any],
    settings_source: Path | None,
    config: Configuration,
    cases: list[score.Case],
    saved: dict[str, dict[str, Any]],
    judge_ref: str,
    library: dict[str, str],
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable[[str], None],
    repeat: int = 1,
    jobs: int = 1,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run every case, each from its own copy of a fixture; return each run's record.

    With ``jobs`` above 1 the cases are shared out among that many apps, each on its own
    copy of the library. With ``repeat`` above 1 a case is run that many times and its
    records are kept as ``E1#1``, ``E1#2``…
    """

    runner = RUNNERS[config.path]
    assert runner is not None
    work: queue.Queue[tuple[str, score.Case]] = queue.Queue()
    for case in sorted(cases, key=weight, reverse=True):
        for turn in range(1, repeat + 1):
            work.put((case.id if repeat == 1 else f"{case.id}#{turn}", case))
    results: dict[str, Any] = {}
    problems: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            with open_session(
                template, info, settings_source, config, factory, overrides
            ) as session:
                while True:
                    try:
                        key, case = work.get_nowait()
                    except queue.Empty:
                        return
                    say(f"  {key} ...")
                    result = run_case(session, case, saved, runner, judge_ref, library)
                    with lock:
                        results[key] = result
                        seconds = result["outcome"]["seconds"]
                        verdict = "passed" if not result["failures"] else "failed"
                        say(f"  {key} {verdict} ({seconds:.0f} s)")
                        if progress:
                            progress(dict(results))
        except BaseException as error:
            problems.append(error)

    threads = [
        threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(jobs, work.qsize())))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if problems and not results:
        raise problems[0]
    if not work.empty():
        raise ExamError(
            f"{work.qsize()} cases were not run, because the apps that ran them stopped: "
            f"{problems[0] if problems else 'no reason was given'}"
        )
    order = {case.id: number for number, case in enumerate(cases)}
    return dict(sorted(results.items(), key=lambda item: (order[item[1]["case"]], item[0])))


# The command ------------------------------------------------------------------------------


def code_version() -> str:
    """The commit the exam ran on, and whether the backend had changes that are not committed."""

    root = Path(__file__).resolve().parents[4]
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()  # fmt: skip
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "apps/backend/gunther"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.splitlines()  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return ""
    return head + (
        f", with {len(dirty)} changed file(s) in gunther/ that are not committed" if dirty else ""
    )


def write_raw(
    folder: Path, notes: list[str], recorded: list[dict[str, Any]], complete: bool
) -> None:
    (folder / "raw.json").write_text(
        json.dumps(
            {"notes": notes, "complete": complete, "configurations": recorded},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run_exam(
    options: Options,
    *,
    model_client_factory: ClientFactory | None = None,
    overrides: dict[str, Any] | None = None,
    results_root: Path = RESULTS,
    library_dir: Path = score.LIBRARY_DIR,
    cases_file: Path = score.CASES_FILE,
    say: Callable[[str], None] = print,
) -> Path | None:
    """Run the exam and write its report; the folder it wrote to (None for ``fixtures_only``).

    ``model_client_factory`` and ``overrides`` stand in for real models in tests only.
    """

    for path in options.paths:
        if path not in RUNNERS:
            raise ExamError(f"Unknown path {path}; use one of {', '.join(PATHS)}.")
    settings_source = options.settings
    if settings_source is None and os.environ.get("SERVICE_SETTINGS_FILE"):
        settings_source = Path(os.environ["SERVICE_SETTINGS_FILE"])
    if settings_source is not None and not settings_source.exists():
        raise ExamError(f"The settings file {settings_source} does not exist.")
    cases = score.load_cases(cases_file)
    fixtures = score.load_fixtures(cases_file)
    broken = score.problems(cases, fixtures)
    if broken:
        raise ExamError("The case file is not right: " + "; ".join(broken))
    if options.only:
        unknown = set(options.only) - {case.id for case in cases}
        if unknown:
            raise ExamError(f"Unknown case ids: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case.id in options.only]
    library = score.library_texts(library_dir)
    results_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gunther-exam-settings-") as scratch:
        settings_file = write_exam_settings(
            Path(scratch) / "service-settings.json", settings_source
        )
        template, info = ensure_template(
            results_root, library_dir, settings_file, options, model_client_factory, overrides, say
        )
        gateway = _probe_gateway(template, settings_file, model_client_factory, overrides)

    ready = {model.ref for model in gateway.models}
    asked = options.models
    default = gateway.for_role("outputs") or gateway.for_role("ask")
    if not asked:
        if default is None:
            raise ExamError(NO_MODEL)
        asked = [default[0].ref]
    judge_ref = options.judge or asked[0]
    missing = [ref for ref in [*asked, judge_ref] if ref not in ready]
    if missing:
        raise ExamError(f"Not set up, or no key: {', '.join(missing)}. Ready: {', '.join(ready)}.")
    writer = options.writer_effort or (default[1] if default else None)

    needed = sorted({case.artifact for case in cases} - {"none"})
    wanted = [fixtures[name] for name in needed]
    kept: dict[str, dict[str, dict[str, Any]]] = {}
    for model in asked:
        config = Configuration("fixture", model, writer)
        kept[model] = ensure_fixtures(
            results_root, template, info, settings_source, config, wanted, options,
            model_client_factory, overrides, say,
        )  # fmt: skip
    if options.fixtures_only:
        say("The fixtures are built.")
        return None

    started = datetime.now()
    folder = results_root / f"{started:%Y%m%d-%H%M}-{options.label}"
    folder.mkdir(parents=True, exist_ok=True)
    notes = [
        f"Judge: {judge_ref}, fixed for this run.",
        f"Library: {info['notes']} notes; semantic search "
        + ("on." if not info["semanticOffReason"] else f"off ({info['semanticOffReason']})."),
        f"Cases: {len(cases)}; {options.jobs} at a time"
        + (f"; each run {options.repeat} times." if options.repeat > 1 else "."),
    ]
    version = code_version()
    if version:
        notes.append(f"Code: {version}.")
    for model, made in kept.items():
        for name, item in made.items():
            built = item["built"]
            tries = "try" if built["attempts"] == 1 else "tries"
            notes.append(
                f"Fixture {name} ({model}): built in {built['seconds']} s with {built['calls']} "
                f"model calls in {built['attempts']} {tries}; every case starts from a copy of it."
            )
    if judge_ref in asked:
        notes.append(
            "Warning: the judge is also a model under test, so it may favour its own answers."
        )
    recorded: list[dict[str, Any]] = []
    configurations = [
        Configuration(path, model, writer)
        for model, path in itertools.product(asked, options.paths)
    ]
    for config in configurations:
        say(f"Configuration: {config.name}")

        def progress(results: dict[str, Any], config: Configuration = config) -> None:
            write_raw(folder, notes, [*recorded, {"name": config.name, "results": results}], False)

        results = run_configuration(
            template, info, settings_source, config, cases, kept[config.model], judge_ref, library,
            model_client_factory, overrides, say, options.repeat, options.jobs, progress,
        )  # fmt: skip
        card = report.scorecard({case.id: case for case in cases}, results)
        recorded.append(
            {"name": config.name, "config": asdict(config), "card": card, "results": results}
        )
    write_raw(folder, notes, recorded, True)
    text = report.render(options.label, recorded, notes)
    (folder / "report.md").write_text(text, encoding="utf-8")
    say(text)
    say(f"Written to {folder}")
    return folder


def _effort(text: str) -> Effort:
    if text not in EFFORTS:
        raise argparse.ArgumentTypeError(f"Unknown effort {text}; use {', '.join(EFFORTS)}")
    return text  # type: ignore[return-value]


def parse(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(prog="python -m evals.artifacts.run", description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        help="a service-settings.json (else SERVICE_SETTINGS_FILE, else .env)",
    )
    parser.add_argument(
        "--path",
        default="baseline",
        help="how the messages are handled, comma-separated: baseline (today's path) or agent",
    )
    parser.add_argument(
        "--models",
        default="",
        help="model refs, comma-separated (default: the Outputs job's model, else Ask's)",
    )
    parser.add_argument(
        "--writer-effort", type=_effort, help="effort for writing (default: the Outputs job's)"
    )
    parser.add_argument("--judge", help="model ref for the judge (default: the first model)")
    parser.add_argument("--only", default="", help="case ids, comma-separated")
    parser.add_argument("--label", default="exam")
    parser.add_argument("--reseed", action="store_true", help="read the demo library again")
    parser.add_argument(
        "--rebuild-fixtures", action="store_true", help="build the deck and the report again"
    )
    parser.add_argument(
        "--fixtures-only",
        action="store_true",
        help="build the fixtures that are missing, then stop",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each case this many times; a case's result is then its pass rate",
    )
    parser.add_argument(
        "--jobs", type=int, default=1, help="cases run at the same time, each on its own copy"
    )
    args = parser.parse_args(argv)
    split = lambda text: [part.strip() for part in text.split(",") if part.strip()]  # noqa: E731
    return Options(
        settings=args.settings,
        models=split(args.models),
        writer_effort=args.writer_effort,
        judge=args.judge,
        only=split(args.only),
        label=args.label,
        paths=split(args.path),
        reseed=args.reseed,
        rebuild_fixtures=args.rebuild_fixtures,
        fixtures_only=args.fixtures_only,
        repeat=max(1, args.repeat),
        jobs=max(1, args.jobs),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        # Flushed, so progress shows while the output goes to a file.
        run_exam(parse(argv), say=lambda text: print(text, flush=True))
    except ExamError as error:
        print(f"The exam could not run: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
