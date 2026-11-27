"""The Artifacts exam: fixed messages sent to a deck and a report built from the demo library.

Run from ``apps/backend``: ``python -m evals.artifacts.run --label exam``.
See the README. It is a developer tool: nothing in the app or the sidecar uses it.
"""

from __future__ import annotations

import argparse
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
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import partial
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
from gunther.output_types import OUTPUT_TYPES
from gunther.workspace_storage import snapshot_database

# A fixture that lacks what the cases rely on is built again, this many times at most.
FIXTURE_TRIES = 3


@dataclass
class Options:
    settings: Path | None = None
    models: list[str] = field(default_factory=list)
    writer_effort: Effort | None = None
    judge: str | None = None
    only: list[str] = field(default_factory=list)
    label: str = "exam"
    reseed: bool = False
    rebuild_fixtures: bool = False
    fixtures_only: bool = False
    repeat: int = 1
    jobs: int = 1


@dataclass(frozen=True)
class Configuration:
    model: str
    writer_effort: Effort | None

    @property
    def name(self) -> str:
        return f"agent: {self.model} (write {self.writer_effort or 'default'})"


# Counting model calls ---------------------------------------------------------------------


@dataclass
class Usage:
    calls: int = 0
    tokens: int = 0
    seconds: float = 0.0


class Counter:
    """Counts the model calls and tokens a gateway makes while a window is open.

    It listens where the gateway talks to the provider (``_stream``): every call goes
    through it, whoever made it. The Artifacts agent keeps no trace, as Ask does.
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
    directory: Path
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

    def artifact(self, artifact_id: str) -> dict[str, Any]:
        response = self.client.get(
            f"/api/knowledge-bases/{self.base_id}/artifacts/{artifact_id}", headers=self.headers,
        )
        return response.raise_for_status().json()

    def baseline(
        self, artifact_id: str, case: score.Case
    ) -> tuple[score.Version, dict[str, str]]:
        """Read the fixture and use an exact-text edit if the case changes its baseline."""

        artifact = self.artifact(artifact_id)
        original = score.Version.from_artifact(artifact)
        if original.number != 1 or not (artifact.get("provenance") or {}).get("creationPlan"):
            raise ExamError("The cached fixture does not point to an approved first version.")
        content, substitutions = score.prepare(case, original.content, original.kind)
        if content != original.content:
            response = self.client.post(
                f"/api/knowledge-bases/{self.base_id}/artifacts/{artifact_id}/edits",
                headers=self.headers,
                json={"clientRequestId": f"exam_edit_{secrets.token_hex(8)}", "content": content},
            )
            edit = response.raise_for_status().json()
            original = score.Version.from_artifact(self.artifact(edit["id"]))
            if original.content != content:
                raise ExamError("The saved baseline differs from the exact-text edit.")
        return original, substitutions

    def head(self, version: score.Version) -> score.Version:
        """The newest version of the lineage a version belongs to."""

        listed = self.client.get(
            f"/api/knowledge-bases/{self.base_id}/artifacts", headers=self.headers
        ).raise_for_status().json()
        mine = next(item for item in listed if item["id"] == version.id)
        newest = max(
            (item for item in listed if item["lineageId"] == mine["lineageId"]),
            key=lambda item: item["versionNumber"],
        )
        return score.Version.from_artifact(self.artifact(newest["id"]))


def copy_library(source: Path, target: Path) -> None:
    """Copy library files and take a SQLite backup that includes committed WAL data."""

    database = "gunther.sqlite"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(database, f"{database}-*"))
    snapshot_database((source / database).absolute(), (target / database).absolute())


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
        copy_library(template, copy)
        settings_file = write_settings_file(
            Path(scratch) / "service-settings.json", settings_source
        )
        app = make_app(
            copy,
            settings_file,
            token,
            factory,
            {**(overrides or {}), "library_root": None, "processing_worker_enabled": False},
        )
        with TestClient(app) as client:
            found = client.get("/api/workspace/bootstrap", headers=headers).json()["workspaceId"]
            gateway = client.app.state.models
            yield Session(
                client,
                {**headers, "X-Gunther-Workspace-Id": found},
                info["knowledgeBaseId"],
                copy,
                gateway,
                Counter(gateway),
                config,
            )


# Fixtures ---------------------------------------------------------------------------------


def describe(name: str, data: dict[str, Any]) -> str:
    """Why a stream did not end in a version, in one line."""

    if name == "error":
        return f"HTTP {data.get('status')}: {str(data.get('detail', ''))[:300]}"
    return data.get("detail") or (
        "It was stopped." if name == "stopped" else f"The stream ended with {name}."
    )


Stream = Callable[[dict[str, Any]], tuple[str, dict[str, Any]]]


def plan_request(
    setup: dict[str, Any], outline: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    nonce = secrets.token_hex(8)
    body = {
        "kind": "plan", "clientRequestId": f"exam_plan_{nonce}",
        "planId": f"draft_{nonce}", "setup": setup,
    }
    if outline is not None:
        body["initialOutline"] = outline
    return body


def _plan_problem(
    planned: dict[str, Any], expected_outline: list[dict[str, Any]] | None
) -> str:
    plan = planned.get("plan") or {}
    proposal = plan.get("proposal") or {}
    if (
        planned["outcome"] != "planned" or plan.get("planStatus") != "ready"
        or not plan.get("planRevision") or plan.get("pending") or plan.get("blocker") is not None
        or not proposal.get("coverage") or planned.get("artifact") is not None
    ):
        return f"The Plan turn did not leave a ready proposal. {planned.get('reply', '')}".strip()
    outline = plan.get("outline")
    if OUTPUT_TYPES[plan["options"]["type"]].capabilities.outline and (
        not outline or proposal["count"] != {"mode": "exact", "value": len(outline)}
        or any(not row.get("id") or not row.get("heading") for row in outline)
    ):
        return "The reviewed plan has an incomplete outline."
    if expected_outline is not None and [
        {field: row.get(field) for field in ("id", "heading", "goal", "layout", "notes")}
        for row in outline or []
    ] != expected_outline:
        return "The reviewed outline differs from the fixture's fixed rows."
    return ""


def approve_plan(
    send: Stream, planned: dict[str, Any], outline: list[dict[str, Any]] | None = None
) -> tuple[str, dict[str, Any]]:
    """Inspect the reviewed plan, then explicitly approve that exact revision."""

    if problem := _plan_problem(planned, outline):
        return "incomplete", {"detail": problem}
    plan = planned["plan"]
    name, data = send({
        "kind": "approve", "clientRequestId": f"exam_approve_{secrets.token_hex(8)}",
        "planId": plan["planId"], "planRevision": plan["planRevision"],
    })
    if name == "done":
        artifact = data.get("artifact") or {}
        creation = (artifact.get("provenance") or {}).get("creationPlan") or {}
        if (
            data["outcome"] != "saved" or artifact.get("versionNumber") != 1
            or creation.get("revision") != plan["planRevision"]
            or creation.get("planId") != plan["planId"]
            or creation.get("outline") != plan["outline"]
        ):
            return "incomplete", {"detail": "Approval did not save the reviewed first version."}
    return name, data


def plan_and_approve(
    send: Stream, setup: dict[str, Any], outline: list[dict[str, Any]] | None = None
) -> tuple[str, dict[str, Any]]:
    name, data = send(plan_request(setup, outline))
    return approve_plan(send, data, outline) if name == "done" else (name, data)


@dataclass(frozen=True)
class FixtureSnapshot:
    directory: Path
    artifact_id: str
    built: dict[str, Any]


def read_fixture(directory: Path) -> FixtureSnapshot:
    metadata = json.loads((directory / "fixture.json").read_text(encoding="utf-8"))
    return FixtureSnapshot(directory, metadata["artifactId"], metadata["built"])


def save_fixture(session: Session, target: Path, artifact_id: str, built: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix=".fixture-", dir=target.parent) as staging:
        snapshot, previous = Path(staging) / "snapshot", Path(staging) / "previous"
        copy_library(session.directory, snapshot)
        (snapshot / "fixture.json").write_text(
            json.dumps({"artifactId": artifact_id, "built": built}, indent=1), encoding="utf-8",
        )
        if target.exists():
            target.rename(previous)
        try:
            snapshot.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise


def build_fixture(
    fixture: score.Fixture,
    target: Path,
    open_attempt: Callable[[], AbstractContextManager[Session]],
    say: Callable[[str], None],
) -> FixtureSnapshot:
    """Plan and approve the fixed outline in a fresh library on each attempt."""

    calls = tokens = 0
    started = time.perf_counter()
    lacking: list[str] = []
    outline = fixture.initial_outline()
    for attempt in range(1, FIXTURE_TRIES + 1):
        with open_attempt() as session:
            setup = fixture.setup() | {
                "model": session.config.model, "effort": session.config.writer_effort,
            }
            tail = f"artifact-conversations/{new_conversation_id()}/messages/stream"
            with session.counter.window() as used:
                name, data = plan_and_approve(partial(session.stream, tail), setup, outline)
            calls, tokens = calls + used.calls, tokens + used.tokens
            if name != "done":
                lacking = [describe(name, data)]
            else:
                artifact = session.artifact(data["artifact"]["id"])
                lacking = fixture.problems(score.Version.from_artifact(artifact).parts)
                if not lacking:
                    built = {
                        "attempts": attempt,
                        "seconds": round(time.perf_counter() - started),
                        "calls": calls,
                        "tokens": tokens,
                        "model": session.config.model,
                        "effort": session.config.writer_effort,
                    }
                    save_fixture(session, target, artifact["id"], built)
                    return FixtureSnapshot(target, artifact["id"], built)
        say(f"  The {fixture.name} fixture, try {attempt} of {FIXTURE_TRIES}: {'; '.join(lacking)}")
    raise ExamError(
        f"The {fixture.name} fixture could not be built: {'; '.join(lacking)}. "
        "It is built through the real path, so a failure here is a finding in itself."
    )


def fixture_path(root: Path, template: Path, config: Configuration, fixture: score.Fixture) -> Path:
    """One approved snapshot directory for each library, model, effort and fixture."""

    model = re.sub(r"[^A-Za-z0-9]+", "-", config.model).strip("-")
    key = [template.name.removeprefix("template-"), model, config.writer_effort or "default"]
    return root / f"fixture-{fixture.name}-{'-'.join(key)}-{fixture.hash}"


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
) -> dict[str, FixtureSnapshot]:
    """The fixtures the cases start from, built once and reused while the library, the model
    and the fixture's definition are unchanged. Missing ones are built side by side."""

    saved: dict[str, FixtureSnapshot] = {}
    missing: list[score.Fixture] = []
    for fixture in wanted:
        path = fixture_path(root, template, config, fixture)
        if not options.rebuild_fixtures and (path / "fixture.json").is_file():
            snapshot = read_fixture(path)
            if (snapshot.built["model"], snapshot.built["effort"]) == (
                config.model, config.writer_effort,
            ):
                say(f"Reusing the {fixture.name} fixture {path.name}.")
                saved[fixture.name] = snapshot
                continue
        missing.append(fixture)
    failures: list[BaseException] = []

    def build(fixture: score.Fixture) -> None:
        try:
            say(
                f"Building the {fixture.name} fixture ({len(fixture.outline)} parts); "
                "this takes a while."
            )
            made = build_fixture(
                fixture, fixture_path(root, template, config, fixture),
                partial(open_session, template, info, settings_source, config, factory, overrides),
                say,
            )
            saved[fixture.name] = made
            took = made.built
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


def new_conversation_id() -> str:
    return f"exam_{secrets.token_hex(8)}"


def open_conversation(session: Session) -> str:
    """Open an empty conversation for a case that begins without an artifact."""

    conversation_id = new_conversation_id()
    base = f"/api/knowledge-bases/{session.base_id}/artifact-conversations"
    made = session.client.post(base, headers=session.headers, json={"id": conversation_id})
    if made.status_code >= 300:
        raise ExamError(f"The conversation could not be made: {made.text[:300]}")
    return conversation_id


def run_agent_case(
    session: Session, case: score.Case, start: score.Version | None
) -> score.Outcome:
    """Send a case's messages through the Artifacts agent's conversation endpoint and report
    what came of the last: it asked, answered, replied, paused, or saved a version."""

    outcome = score.Outcome(route="agent")
    conversation_id = start.conversation_id if start else open_conversation(session)
    if not conversation_id:
        raise ExamError("The approved fixture has no owning conversation.")
    tail = f"artifact-conversations/{conversation_id}/messages/stream"
    turns = [*case.history, score.Turn(case.message, case.focus)]
    head = start
    for number, turn in enumerate(turns, start=1):
        last = number == len(turns)
        if last and case.plan is not None:
            setup = case.plan.model_dump(mode="json", by_alias=True) | {
                "focus": turn.message,
                "model": session.config.model,
                "effort": session.config.writer_effort,
            }
            body = plan_request(setup)
        else:
            body = turn_body(turn, session.config, f"exam_{secrets.token_hex(8)}")
        outcome.notes.append(f"turn {number}: {turn.message[:200]}")
        send = partial(session.stream, tail, trace=outcome.notes)
        approved = False
        with session.counter.window() as used:
            name, data = send(body)
            if last and case.approve and name == "done" and data["outcome"] == "planned":
                name, data = approve_plan(send, data)
                approved = name == "done"
                if approved:
                    outcome.notes.append("Approved the reviewed plan.")
        outcome.before = head
        outcome.calls, outcome.tokens, outcome.seconds = used.calls, used.tokens, used.seconds
        if name != "done":
            outcome.failed = describe(name, data)
            return outcome
        saved = score.Version.from_artifact(data["artifact"]) if data.get("artifact") else None
        if saved is not None and (head is None or saved.number == 1) and not approved:
            outcome.after = saved
            outcome.failed = "The turn saved a first version without scripted approval."
            return outcome
        if not last:
            head = saved or head
            continue
        outcome.after = saved
        outcome.reply = data.get("reply") or ""
        ended = data["outcome"]
        if ended == "asked":
            outcome.intent, outcome.asked = "ask", True
            outcome.question = (data.get("question") or {}).get("text", outcome.reply)
            outcome.options = list((data.get("question") or {}).get("options", []))
        elif ended == "saved":
            outcome.intent = "change" if head else "make"
        elif ended == "planned":
            outcome.intent, outcome.plan = "plan", data["plan"]
        elif ended in ("answered", "replied"):
            outcome.intent = "answer" if head else "reply"
        else:
            outcome.failed = f"The turn paused before it finished: {outcome.reply[:300]}"
    return outcome


# One case, one configuration --------------------------------------------------------------


def run_case(
    session: Session,
    case: score.Case,
    fixture: FixtureSnapshot | None,
    judge_ref: str,
    library: dict[str, str],
) -> dict[str, Any]:
    """Start a case from its own copy of a fixture, run it, and score it."""

    subs: dict[str, str] = {}
    resolved = case
    try:
        start = None
        if fixture is not None:
            start, subs = session.baseline(fixture.artifact_id, case)
        resolved = case.resolved(subs)
        outcome = run_agent_case(session, resolved, start)
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
    of the parts or a new document can require a full build, and every earlier message
    is another turn."""

    remakes = case.category == "upstream" or case.artifact == "none"
    return (len(case.history) + 1) * (3 if remakes else 1)


def run_configuration(
    template: Path,
    info: dict[str, Any],
    settings_source: Path | None,
    config: Configuration,
    cases: list[score.Case],
    saved: dict[str, FixtureSnapshot],
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

    With ``jobs`` above 1 that many cases can run in parallel. Each case and repeat
    opens its own app. Repeated records are kept as ``E1#1``, ``E1#2``…
    """

    work: queue.Queue[tuple[str, score.Case]] = queue.Queue()
    for case in sorted(cases, key=weight, reverse=True):
        for turn in range(1, repeat + 1):
            work.put((case.id if repeat == 1 else f"{case.id}#{turn}", case))
    results: dict[str, Any] = {}
    problems: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            while True:
                try:
                    key, case = work.get_nowait()
                except queue.Empty:
                    return
                say(f"  {key} ...")
                fixture = saved[case.artifact] if case.artifact != "none" else None
                with open_session(
                    fixture.directory if fixture else template, info, settings_source,
                    config, factory, overrides,
                ) as session:
                    result = run_case(session, case, fixture, judge_ref, library)
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
    if problems:
        raise problems[0]
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

    overrides = {**(overrides or {}), "library_root": None}
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
        settings_file = write_settings_file(
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
    kept: dict[str, dict[str, FixtureSnapshot]] = {}
    for model in asked:
        config = Configuration(model, writer)
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
    if info["failedJobs"]:
        notes.append(f"Unavailable setup jobs (tolerated): {'; '.join(info['failedJobs'])}.")
    version = code_version()
    if version:
        notes.append(f"Code: {version}.")
    for model, made in kept.items():
        for name, item in made.items():
            built = item.built
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
    configurations = [Configuration(model, writer) for model in asked]
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
