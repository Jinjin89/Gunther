"""The Ask exam: fixed questions over a demo library, through the real Ask path.

Run from ``apps/backend``: ``python -m evals.ask.run --label baseline``. See the README.
It is a developer tool: nothing in the app or the sidecar uses it.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from evals.ask import report, score
from gunther.config import Settings
from gunther.llm import ClientFactory, ModelGateway
from gunther.main import create_app
from gunther.model_profiles import EFFORTS, Effort

RESULTS = Path(__file__).resolve().parents[1] / "results"
LIBRARY_TITLE = "Velmora cool roofs"
SETUP_TIMEOUT_SECONDS = 600
# Writing a capture's AI summary is a nicety; every other failed job spoils the library.
TOLERATED_JOBS = {"digest"}
FINISHED = {"completed", "failed"}


class ExamError(Exception):
    """Something that stops the exam, said plainly for the person running it."""


@dataclass
class Options:
    settings: Path | None = None
    models: list[str] = field(default_factory=list)
    writer_effort: Effort | None = None
    plan_efforts: list[Effort] = field(default_factory=lambda: ["low"])
    check_efforts: list[Effort] = field(default_factory=lambda: ["off"])
    judge: str | None = None
    only: list[str] = field(default_factory=list)
    label: str = "exam"
    reseed: bool = False
    repeat: int = 1
    # Let Ask pick a skill itself (ASK_AUTO_SKILLS); the auto-skill cases only run with it.
    auto_skills: bool = False


@dataclass(frozen=True)
class Configuration:
    model: str
    writer_effort: Effort | None
    plan_effort: Effort
    check_effort: Effort

    @property
    def name(self) -> str:
        writer = self.writer_effort or "default"
        return f"{self.model} (write {writer}, plan {self.plan_effort}, check {self.check_effort})"


# The app ---------------------------------------------------------------------------


def library_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.md")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def write_settings_file(target: Path, source: Path | None) -> Path:
    """A copy of the saved settings that keeps how answers are made, or a file that only says so."""

    payload: dict[str, Any] = {}
    if source is not None:
        payload = json.loads(source.read_text(encoding="utf-8"))
    payload["developer"] = {**(payload.get("developer") or {}), "traces": True}
    target.write_text(json.dumps(payload), encoding="utf-8")
    target.chmod(0o600)
    return target


def make_app(
    directory: Path,
    settings_file: Path,
    token: str,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
):
    """An app on the library in ``directory``, with its processing worker on and real models."""

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{directory / 'gunther.sqlite'}",
        assets_dir=directory / "assets",
        recordings_dir=directory / "recordings",
        speech_dir=directory / "speech",
        seed_demo=False,
        auth_token=token,
        service_settings_file=settings_file,
        **(overrides or {}),
    )
    return create_app(settings, model_client_factory=factory)


def wait_for_jobs(client: TestClient, headers: dict, source_ids: set[str], say: Callable) -> list:
    """Poll until every job of these sources has finished; return the ones that failed."""

    deadline = time.monotonic() + SETUP_TIMEOUT_SECONDS
    while True:
        jobs = client.get("/api/developer/jobs?limit=200", headers=headers).json()["jobs"]
        mine = [job for job in jobs if job["sourceId"] in source_ids]
        waiting = [job for job in mine if job["state"] not in FINISHED]
        if mine and not waiting:
            return [job for job in mine if job["state"] == "failed"]
        if time.monotonic() > deadline:
            raise ExamError(
                f"The library was still being read after {SETUP_TIMEOUT_SECONDS // 60} minutes "
                f"({len(waiting)} jobs waiting). Check Settings → Developer → Jobs in the app."
            )
        time.sleep(2)


def seed_template(
    target: Path,
    library_dir: Path,
    settings_file: Path,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable,
) -> dict[str, Any]:
    """Create the demo library in ``target`` with the real worker, so runs share one index."""

    token = secrets.token_hex(32)
    headers = {"X-Gunther-Token": token}
    files = sorted(library_dir.glob("*.md"))
    say(f"Seeding the demo library ({len(files)} notes); this reads and indexes every note.")
    with TestClient(make_app(target, settings_file, token, factory, overrides)) as client:
        if not client.app.state.models.models:
            raise ExamError(NO_MODEL)
        base_id = client.post(
            "/api/knowledge-bases",
            headers=headers,
            json={
                "title": LIBRARY_TITLE,
                "question": "How well do cool roofs work in Velmora?",
                "description": "The Ask exam's demo library.",
            },
        ).json()["id"]
        sources = set()
        for path in files:
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].removeprefix("# ").strip()
            created = client.post(
                "/api/sources",
                headers=headers,
                json={"title": title, "kind": "note", "knowledgeBaseId": base_id, "content": text},
            )
            if created.status_code >= 300:
                raise ExamError(f"A note could not be added ({title}): {created.text[:200]}")
            sources.add(created.json()["source"]["id"])
        failed = wait_for_jobs(client, headers, sources, say)
        broken = [job for job in failed if job["kind"] not in TOLERATED_JOBS]
        if broken:
            listing = "; ".join(
                f"{job['kind']} of {job['sourceTitle']}: {job['error']}" for job in broken
            )
            raise ExamError(f"Reading the library failed, so the exam would be unfair: {listing}")
        semantic = client.app.state.knowledge_service.index.semantic_off_reason
        info = {
            "knowledgeBaseId": base_id,
            "semanticOffReason": semantic,
            "failedJobs": [f"{job['kind']}: {job['sourceTitle']}" for job in failed],
            "notes": len(files),
        }
    return info


NO_MODEL = (
    "No model is set up, so there is nothing to examine. Put a .env at the repo root "
    "(LLM_PROVIDER, LLM_API_KEY, LLM_MODEL, LLM_BASE_URL, TAVILY_API_KEY) or pass "
    "--settings PATH to a copy of the app's service-settings.json."
)


def ensure_template(
    root: Path,
    library_dir: Path,
    settings_file: Path,
    options: Options,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable,
) -> tuple[Path, dict[str, Any]]:
    """The seeded library for these files, built once and reused while the files are unchanged."""

    template = root / f"template-{library_hash(library_dir)}"
    marker = template / "template.json"
    if options.reseed and template.exists():
        shutil.rmtree(template)
    if marker.exists():
        say(f"Reusing the seeded library {template.name}.")
        return template, json.loads(marker.read_text(encoding="utf-8"))
    partial = root / f"{template.name}.partial"
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        info = seed_template(partial, library_dir, settings_file, factory, overrides, say)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    (partial / "template.json").write_text(json.dumps(info), encoding="utf-8")
    partial.rename(template)
    return template, info


# One case, one configuration ------------------------------------------------------


def trace_usage(trace: Any) -> tuple[int, int]:
    """(model calls, tokens) over every call recorded in a trace."""

    calls = tokens = 0
    stack = [trace]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if "usage" in item:
                calls += 1
                used = item["usage"] or {}
                tokens += used.get("total_tokens") or (
                    used.get("prompt_tokens", 0) + used.get("completion_tokens", 0)
                )
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return calls, tokens


def run_case(
    client: TestClient, headers: dict, base_id: str, case: score.Case, config: Configuration
) -> score.Outcome:
    made = client.post(f"/api/knowledge-bases/{base_id}/sessions", headers=headers, json={})
    session_id = made.json()["id"]
    brief = any(path.endswith("/brief/refresh") for path in client.app.openapi()["paths"])
    turn: dict[str, Any] = {}
    started = time.perf_counter()
    for text in [*case.history, case.question]:
        body: dict[str, Any] = {"content": text, "web": case.web, "model": config.model}
        if config.writer_effort:
            body["effort"] = config.writer_effort
        if case.skill:
            body["skill"] = case.skill
        if case.budget:
            body["budget"] = case.budget
        started = time.perf_counter()
        response = client.post(f"/api/sessions/{session_id}/messages", headers=headers, json=body)
        if response.status_code >= 300:
            return score.Outcome(failed=f"HTTP {response.status_code}: {response.text[:200]}")
        turn = response.json()
        seconds = time.perf_counter() - started
        if brief:
            client.post(f"/api/sessions/{session_id}/brief/refresh", headers=headers)
    message = turn["assistantMessage"]
    outcome = score.Outcome(
        answer=message["content"],
        citations=message["citations"],
        context=message["context"],
        seconds=seconds,
    )
    traced = client.get(
        f"/api/sessions/{session_id}/messages/{message['id']}/trace", headers=headers
    )
    if traced.status_code == 200:
        outcome.calls, outcome.tokens = trace_usage(traced.json())
    return outcome


def run_configuration(
    template: Path,
    info: dict[str, Any],
    settings_source: Path | None,
    config: Configuration,
    cases: list[score.Case],
    judge_ref: str,
    library: dict[str, str],
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable,
    repeat: int = 1,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Run every case on a fresh copy of the template; return each run's record and the skipped.

    With ``repeat`` above 1 a case is run that many times on the same copy (a new
    conversation each time), and its records are kept as ``L1#1``, ``L1#2``…
    """

    skipped: dict[str, list[str]] = {}
    results: dict[str, Any] = {}
    token = secrets.token_hex(32)
    headers = {"X-Gunther-Token": token}
    with tempfile.TemporaryDirectory(prefix="gunther-exam-") as scratch:
        copy = Path(scratch) / "library"
        shutil.copytree(template, copy)
        settings_file = write_settings_file(
            Path(scratch) / "service-settings.json", settings_source
        )
        app = make_app(copy, settings_file, token, factory, overrides)
        with TestClient(app) as client:
            service = client.app.state.knowledge_service
            service.agent_efforts = (config.plan_effort, config.check_effort)
            gateway: ModelGateway = client.app.state.models
            judge_model = gateway.get(judge_ref)
            if judge_model is None:
                raise ExamError(f"The judge {judge_ref} is not a model that is set up.")
            have = {
                "tavily": client.app.state.online_search.mode == "tavily",
                "auto_skills": service.auto_skills,
            }
            for case in cases:
                missing = [need for need in case.needs if not have.get(need)]
                if missing:
                    skipped[case.id] = missing
                    continue
                for turn in range(1, repeat + 1):
                    key = case.id if repeat == 1 else f"{case.id}#{turn}"
                    say(f"  {key} ...")
                    outcome = run_case(client, headers, info["knowledgeBaseId"], case, config)
                    found = score.checks(case, outcome)
                    judgement, why = (None, "")
                    if not outcome.failed:
                        judgement, why = score.judge(gateway, judge_model, case, outcome, library)
                    results[key] = {
                        "case": case.id,
                        "outcome": asdict(outcome),
                        "checks": found,
                        "judgement": judgement.model_dump() if judgement else None,
                        "judgeError": why,
                        "failures": score.failures(case, found, judgement),
                    }
    return results, skipped


# The command -----------------------------------------------------------------------


def run_exam(
    options: Options,
    *,
    model_client_factory: ClientFactory | None = None,
    overrides: dict[str, Any] | None = None,
    results_root: Path = RESULTS,
    library_dir: Path = score.LIBRARY_DIR,
    cases_file: Path = score.CASES_FILE,
    say: Callable[[str], None] = print,
) -> Path:
    """Run the exam and write its report; the folder it wrote to.

    ``model_client_factory`` and ``overrides`` stand in for real models in tests only.
    """

    if options.auto_skills:
        overrides = {**(overrides or {}), "ask_auto_skills": True}
    settings_source = options.settings
    if settings_source is None and os.environ.get("SERVICE_SETTINGS_FILE"):
        settings_source = Path(os.environ["SERVICE_SETTINGS_FILE"])
    if settings_source is not None and not settings_source.exists():
        raise ExamError(f"The settings file {settings_source} does not exist.")
    cases = score.load_cases(cases_file)
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
    default = gateway.for_role("ask")
    if not asked:
        if default is None:
            raise ExamError(NO_MODEL)
        asked = [default[0].ref]
    missing = [ref for ref in [*asked, options.judge or asked[0]] if ref not in ready]
    if missing:
        raise ExamError(f"Not set up, or no key: {', '.join(missing)}. Ready: {', '.join(ready)}.")
    writer = options.writer_effort or (default[1] if default else None)
    judge_ref = options.judge or asked[0]

    configurations = [
        Configuration(model, writer, plan, check)
        for model, plan, check in itertools.product(
            asked, options.plan_efforts, options.check_efforts
        )
    ]
    started = datetime.now()
    folder = results_root / f"{started:%Y%m%d-%H%M}-{options.label}"
    folder.mkdir(parents=True, exist_ok=True)
    recorded, skipped = [], {}
    for config in configurations:
        say(f"Configuration: {config.name}")
        results, skipped_here = run_configuration(
            template, info, settings_source, config, cases, judge_ref, library,
            model_client_factory, overrides, say, options.repeat,
        )  # fmt: skip
        skipped.update(skipped_here)
        card = report.scorecard({case.id: case for case in cases}, results)
        recorded.append(
            {"name": config.name, "config": asdict(config), "card": card, "results": results}
        )

    notes = [
        f"Judge: {judge_ref}, fixed for this run.",
        f"Library: {info['notes']} notes; semantic search "
        + ("on." if not info["semanticOffReason"] else f"off ({info['semanticOffReason']})."),
    ]
    if info["failedJobs"]:
        notes.append(f"Failed setup jobs (tolerated): {'; '.join(info['failedJobs'])}.")
    if judge_ref in asked:
        notes.append(
            "Warning: the judge is also a model under test, so it may favour its own answers."
        )
    text = report.render(options.label, recorded, skipped, notes)
    (folder / "report.md").write_text(text, encoding="utf-8")
    (folder / "raw.json").write_text(
        json.dumps({"notes": notes, "configurations": recorded}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    say(text)
    say(f"Written to {folder}")
    return folder


def _probe_gateway(
    template: Path,
    settings_file: Path,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
) -> ModelGateway:
    """The models this machine has set up (no app is kept running)."""

    with tempfile.TemporaryDirectory(prefix="gunther-exam-probe-") as scratch:
        probe = Path(scratch)
        with TestClient(make_app(probe, settings_file, "x" * 64, factory, overrides)) as client:
            return client.app.state.models


def _levels(text: str) -> list[Effort]:
    levels = [part.strip() for part in text.split(",") if part.strip()]
    bad = [level for level in levels if level not in EFFORTS]
    if bad:
        raise argparse.ArgumentTypeError(
            f"Unknown effort {', '.join(bad)}; use {', '.join(EFFORTS)}"
        )
    return levels  # type: ignore[return-value]


def parse(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(prog="python -m evals.ask.run", description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        help="a service-settings.json (else SERVICE_SETTINGS_FILE, else .env)",
    )
    parser.add_argument(
        "--models", default="", help="model refs, comma-separated (default: the Ask job's model)"
    )
    parser.add_argument(
        "--writer-effort", choices=EFFORTS, help="effort for writing (default: the Ask job's)"
    )
    parser.add_argument("--plan-effort", type=_levels, default=["low"], help="e.g. off,low")
    parser.add_argument("--check-effort", type=_levels, default=["off"], help="e.g. off,low")
    parser.add_argument("--judge", help="model ref for the judge (default: the first model)")
    parser.add_argument("--only", default="", help="case ids, comma-separated")
    parser.add_argument("--label", default="exam")
    parser.add_argument("--reseed", action="store_true", help="read the demo library again")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run each case this many times; a case's result is then its pass rate",
    )
    parser.add_argument(
        "--auto-skills",
        action="store_true",
        help="let Ask pick a skill itself (ASK_AUTO_SKILLS=true); the auto-skill cases need it",
    )
    args = parser.parse_args(argv)
    split = lambda text: [part.strip() for part in text.split(",") if part.strip()]  # noqa: E731
    return Options(
        settings=args.settings,
        models=split(args.models),
        writer_effort=args.writer_effort,
        plan_efforts=args.plan_effort,
        check_efforts=args.check_effort,
        judge=args.judge,
        only=split(args.only),
        label=args.label,
        reseed=args.reseed,
        repeat=max(1, args.repeat),
        auto_skills=args.auto_skills,
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
