# ruff: noqa: E501
"""The quality evaluation: real material, reports and decks built through the real app path,
rendered, linted and judged.

Run from ``apps/backend``: ``python -m evals.quality.run --sets velmora --methods skills``.
See ``evals/quality/README.md``. It is a developer tool: nothing in the app or the sidecar
uses it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from evals.artifacts.run import Counter
from evals.ask.run import (
    NO_MODEL,
    RESULTS,
    ExamError,
    _probe_gateway,
    make_app,
    write_settings_file,
)
from evals.quality import judge as judging
from evals.quality import lint as linting
from evals.quality import materials, report
from gunther.llm import ClientFactory
from gunther.outputs import split_document

REPO = materials.BACKEND.parents[1]
CAPTURE = REPO / "apps" / "desktop" / "evals" / "deck" / "capture.mjs"
METHODS = ("skills", "studio")
KINDS = {"deck": "slides", "report": "report"}
DEFAULT_JUDGE = "deepseek/deepseek-flash"
RENDER_TIMEOUT_SECONDS = 600


@dataclass
class Options:
    settings: Path | None = None
    sets: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=lambda: ["skills"])
    kinds: list[str] = field(default_factory=lambda: ["deck", "report"])
    model: str | None = None
    judge: str | None = None
    jobs: int = 2
    label: str = "quality"
    reuse: bool = False
    seed_only: bool = False
    # Earlier results folders whose skills builds stand in for this run's (the old method's
    # code does not change; rebuilding it costs about 25 minutes an artifact).
    baseline_from: tuple[str, ...] = ()
    # Earlier results folders whose builds (any method) are judged again as they are.
    builds_from: tuple[str, ...] = ()
    reseed: bool = False
    effort: str | None = None
    render: bool = True
    do_judge: bool = True
    visual_judge: str | None = None


@dataclass(frozen=True)
class Job:
    material: materials.MaterialSet
    label: str  # "deck" or "report"
    method: str

    @property
    def name(self) -> str:
        return f"{self.material.id}/{self.label}-{self.method}"


# Code hash and keys -----------------------------------------------------------------------


def code_hash() -> str:
    """Names the code that builds an output: the backend package, skills included."""

    digest = hashlib.sha256()
    package = materials.BACKEND / "gunther"
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".md", ".toml", ".json", ".txt", ".yaml"}:
            if "__pycache__" in path.parts:
                continue
            digest.update(str(path.relative_to(package)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def build_key(job: Job, model: str, effort: str | None, code: str, info: dict[str, Any]) -> str:
    brief = job.material.brief(job.label)
    text = json.dumps(
        [info.get("setHash"), brief.__dict__, job.method, model, effort, code], sort_keys=True
    )
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# One build --------------------------------------------------------------------------------


def exam_settings(target: Path, source: Path | None, method: str) -> Path:
    write_settings_file(target, source)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["developer"]["output_skills"] = True
    payload["developer"]["output_method"] = method
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def read_stream(
    client: TestClient, url: str, headers: dict, body: dict[str, Any], events: list[dict]
) -> tuple[str, dict[str, Any]]:
    name, last = "", ("error", {"status": 0, "detail": "No result came back."})
    with client.stream("POST", url, headers=headers, json=body) as response:
        if response.status_code != 200:
            response.read()
            return "error", {"status": response.status_code, "detail": response.text[:400]}
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                last = (name, data)
                if name != "text":
                    events.append(
                        {"event": name, **{k: v for k, v in data.items() if k != "content"}}
                    )
    return last


def studio_format_problem(label: str, content: str) -> str:
    marker = "design: v2" if label == "deck" else "<!-- report: v2 -->"
    if marker in content:
        return ""
    return (
        "The studio method did not produce the studio format (no "
        f"`{marker}` in the content): developer.output_method is probably not wired into the "
        "build path yet. Nothing was substituted."
    )


def citation_files(citations: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    short, full = [], []
    for number, cite in enumerate(citations, start=1):
        title = cite.get("sourceTitle") or cite.get("source_title") or ""
        locator = cite.get("locator") or ""
        short.append({"n": number, "title": title, "locator": locator})
        full.append({**short[-1], "quote": cite.get("quote") or "", "url": cite.get("url")})
    return short, full


def build_one(
    job: Job,
    template: Path,
    info: dict[str, Any],
    folder: Path,
    options: Options,
    model: str,
    factory: ClientFactory | None,
    overrides: dict[str, Any] | None,
    say: Callable[[str], None],
) -> dict[str, Any]:
    """Build one artifact through the real path and save everything about it in ``folder``."""

    folder.mkdir(parents=True, exist_ok=True)
    brief = job.material.brief(job.label)
    record: dict[str, Any] = {
        "set": job.material.id, "kind": brief.kind, "label": job.label, "method": job.method,
        "model": model, "dir": str(folder), "error": "", "errors": [],
    }  # fmt: skip
    from gunther.service_settings import OUTPUT_METHODS

    if job.method not in OUTPUT_METHODS:
        record["error"] = (
            f"This build has no output method {job.method!r} (known: {OUTPUT_METHODS})."
        )
        return record
    token = secrets.token_hex(32)
    headers = {"X-Gunther-Token": token}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="gunther-quality-") as scratch:
        copy = Path(scratch) / "library"
        shutil.copytree(template, copy)
        settings_file = exam_settings(
            Path(scratch) / "service-settings.json", options.settings, job.method
        )
        app = make_app(copy, settings_file, token, factory, {**(overrides or {}), "processing_worker_enabled": False})  # fmt: skip
        with TestClient(app) as client:
            workspace = client.get("/api/workspace/bootstrap", headers=headers).json()[
                "workspaceId"
            ]
            headers["X-Gunther-Workspace-Id"] = workspace
            service = client.app.state.knowledge_service
            if getattr(service, "output_method", None) != job.method:
                record["error"] = (
                    f"The app runs output_method={getattr(service, 'output_method', None)!r}, "
                    f"not {job.method!r}."
                )
                return record
            counter = Counter(client.app.state.models)
            body: dict[str, Any] = {
                "clientRequestId": f"quality_{secrets.token_hex(6)}",
                "kind": brief.kind, "audience": brief.audience, "brief": brief.text(),
                "scope": {"mode": "library"}, "model": model,
            }  # fmt: skip
            if brief.kind == "slides":
                body["use"] = brief.use or "talk"
            if options.effort:
                body["effort"] = options.effort
            events: list[dict] = []
            url = f"/api/knowledge-bases/{info['knowledgeBaseId']}/outputs/build/stream"
            say(f"  building {job.name} ...")
            with counter.window() as used:
                name, data = read_stream(client, url, headers, body, events)
            record["seconds"] = round(used.seconds, 1)
            record["calls"], record["tokens"] = used.calls, used.tokens
    (folder / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    record["wallSeconds"] = round(time.perf_counter() - started, 1)
    if name != "done":
        record["error"] = (
            f"HTTP {data.get('status')}: {str(data.get('detail', ''))[:400]}"
            if name == "error" else f"The build ended with {name}."
        )  # fmt: skip
        return record
    content = data["content"]
    short, full = citation_files(data.get("citations") or [])
    (folder / "content.md").write_text(content, encoding="utf-8")
    (folder / "citations.json").write_text(
        json.dumps(short, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (folder / "passages.json").write_text(
        json.dumps(full, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (folder / "outline.json").write_text(
        json.dumps(data.get("outline") or [], ensure_ascii=False, indent=1), encoding="utf-8"
    )  # noqa: E501
    sections = data.get("sections") or []
    (folder / "checks.json").write_text(
        json.dumps(sections, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    record["title"] = data.get("title")
    record["parts"] = len(split_document(content, brief.kind)[1])
    record["issues"] = sum(len(s.get("issues") or []) for s in sections)
    if job.method == "studio":
        problem = studio_format_problem(job.label, content)
        if problem:
            record["error"] = problem
    return record


# Render, lint -----------------------------------------------------------------------------


def render(folder: Path, label: str, say: Callable[[str], None]) -> dict[str, Any]:
    """PNGs and measure.json next to the content; a note when there is no renderer."""

    if not CAPTURE.exists():
        return {"ok": False, "note": f"{CAPTURE.relative_to(REPO)} does not exist; not rendered."}
    out = folder / "render"
    command = [
        "node", str(CAPTURE), "--input", str(folder / "content.md"),
        "--citations", str(folder / "citations.json"), "--out", str(out),
    ]  # fmt: skip
    if label == "report":
        command += ["--kind", "report"]
    try:
        done = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, timeout=RENDER_TIMEOUT_SECONDS,
            cwd=REPO / "apps" / "desktop", check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "note": f"render failed: {error}"}
    (folder / "render.log").write_text(done.stdout + "\n" + done.stderr, encoding="utf-8")
    if done.returncode != 0:
        return {
            "ok": False,
            "note": f"render exited {done.returncode}: {done.stderr.strip()[-300:]}",
        }
    return {"ok": True, "note": ""}


def pngs_in(folder: Path) -> list[Path]:
    return sorted(
        (folder / "render").glob("*.png"),
        key=lambda p: [
            int(t) if t.isdigit() else t for t in __import__("re").split(r"(\d+)", p.name)
        ],
    )  # noqa: E501


def measure_in(folder: Path) -> dict[str, Any] | None:
    path = folder / "render" / "measure.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def lint_build(record: dict[str, Any], material: materials.MaterialSet) -> None:
    folder = Path(record["dir"])
    brief = material.brief(record["label"])
    measure = measure_in(folder)
    defects = linting.lint(
        brief.kind, (folder / "content.md").read_text(encoding="utf-8"), brief.use, measure
    )
    record["measured"] = measure is not None
    record["defects"] = [d.as_dict() for d in defects]
    record["defectCounts"] = linting.counts(defects)
    record["pngs"] = [str(p.relative_to(folder)) for p in pngs_in(folder)]


# Judge ------------------------------------------------------------------------------------


def visual_build(record: dict[str, Any], gateway: Any, model: Any, meter: judging.Meter) -> None:
    """A vision model's score of the rendered slides, and a verdict for each slide."""

    folder = Path(record["dir"])
    with meter.bucket() as used:
        result, error = judging.judge_visual(
            gateway, model, title=record.get("title") or record["label"],
            pngs=[folder / p for p in record.get("pngs", [])],
        )  # fmt: skip
    record["judgeCalls"] = record.get("judgeCalls", 0) + used.calls
    record["judgeTokens"] = record.get("judgeTokens", 0) + used.tokens
    if result is None:
        record["visualJudge"] = {"error": error, "model": model.ref}
        return
    record["visualJudge"] = {**result.model_dump(), "model": model.ref}


def judge_build(
    record: dict[str, Any], material: materials.MaterialSet, gateway: Any, model: Any,
    meter: judging.Meter,
) -> None:  # fmt: skip
    folder = Path(record["dir"])
    brief = material.brief(record["label"])
    content = (folder / "content.md").read_text(encoding="utf-8")
    passages = json.loads((folder / "passages.json").read_text(encoding="utf-8"))
    with meter.bucket() as used:
        absolute, error = judging.judge_absolute(
            gateway, model, kind=brief.kind, brief=brief.text(), audience=brief.audience,
            use=brief.use, content=content, citations=passages,
            pngs=[folder / p for p in record.get("pngs", [])],
        )  # fmt: skip
        titles = judging.judge_titles_only(gateway, model, kind=brief.kind, content=content)
    record["judgeCalls"], record["judgeTokens"] = used.calls, used.tokens
    record["judgeError"] = error
    record["titlesOnly"] = titles
    record["imagesSeen"] = bool(model.vision and record.get("pngs"))
    if absolute is None:
        record["absolute"] = None
        record["verdicts"] = []
        return
    verdicts = judging.section_verdicts(absolute, record["parts"])
    record["absolute"] = absolute.model_dump(exclude={"sections"})
    record["verdicts"] = [v.model_dump() for v in verdicts]
    record["shipRate"] = judging.ship_rate([v.verdict for v in verdicts])


def judge_pairs(
    builds: list[dict[str, Any]], materials_by_id: dict[str, Any], gateway: Any, model: Any,
    meter: judging.Meter, jobs: int, say: Callable[[str], None],
) -> list[dict[str, Any]]:  # fmt: skip
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for build in builds:
        if not build["error"]:
            groups.setdefault((build["set"], build["label"]), {})[build["method"]] = build
    tasks = []
    for (set_id, label), methods in groups.items():
        if "skills" in methods and "studio" in methods:
            tasks.append((set_id, label, methods["skills"], methods["studio"]))

    def one(task: tuple) -> dict[str, Any]:
        set_id, label, first, second = task
        material = materials_by_id[set_id]
        brief = material.brief(label)
        docs = []
        for build in (first, second):
            folder = Path(build["dir"])
            docs.append({
                "content": (folder / "content.md").read_text(encoding="utf-8"),
                "citations": json.loads((folder / "passages.json").read_text(encoding="utf-8")),
            })  # fmt: skip
        with meter.bucket() as used:
            one_order, error_a = judging.judge_pair(
                gateway, model, kind=brief.kind, brief=brief.text(), first=docs[0], second=docs[1]
            )  # noqa: E501
            swapped, error_b = judging.judge_pair(
                gateway, model, kind=brief.kind, brief=brief.text(), first=docs[1], second=docs[0]
            )  # noqa: E501
        result: dict[str, Any] = {
            "set": set_id, "label": label, "first": first["method"], "second": second["method"],
            "errors": [e for e in (error_a, error_b) if e], "calls": used.calls, "tokens": used.tokens,
        }  # fmt: skip
        if one_order and swapped:
            result["criteria"] = {
                c: judging.combine_pair(getattr(one_order, c), getattr(swapped, c))
                for c in judging.PAIR_CRITERIA
            }
            result["reasons"] = [one_order.reason, swapped.reason]
        return result

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(one, tasks))


# The command ------------------------------------------------------------------------------


def find_reusable(root: Path, key: str) -> Path | None:
    for meta in sorted(root.glob("*-quality-*/builds/*/*/meta.json"), reverse=True):
        try:
            saved = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if (
            saved.get("key") == key
            and not saved.get("error")
            and (meta.parent / "content.md").exists()
        ):
            return meta.parent
    return None


def run_quality(
    options: Options,
    *,
    model_client_factory: ClientFactory | None = None,
    overrides: dict[str, Any] | None = None,
    results_root: Path = RESULTS,
    material_file: Path = materials.MATERIALS,
    say: Callable[[str], None] = print,
) -> Path:
    all_sets = materials.load_sets(material_file)
    unknown = set(options.sets) - {item.id for item in all_sets}
    if unknown:
        raise ExamError(f"Unknown sets: {', '.join(sorted(unknown))}")
    chosen = [item for item in all_sets if not options.sets or item.id in options.sets]
    bad = [m for m in options.methods if m not in METHODS] + [
        k for k in options.kinds if k not in KINDS
    ]
    if bad:
        raise ExamError(f"Unknown method or kind: {', '.join(bad)}")
    settings_source = options.settings
    if settings_source is None and os.environ.get("SERVICE_SETTINGS_FILE"):
        settings_source = Path(os.environ["SERVICE_SETTINGS_FILE"])
    options.settings = settings_source
    results_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gunther-quality-settings-") as scratch:
        settings_file = write_settings_file(
            Path(scratch) / "service-settings.json", settings_source
        )
        gateway = _probe_gateway(results_root, settings_file, model_client_factory, overrides)
        ready = {item.ref for item in gateway.models}
        default = gateway.for_role("outputs") or gateway.for_role("ask")
        model = options.model or (default[0].ref if default else None)
        if model is None:
            raise ExamError(NO_MODEL)
        judge_ref = options.judge or (DEFAULT_JUDGE if DEFAULT_JUDGE in ready else model)
        missing = [ref for ref in (model, judge_ref) if ref not in ready]
        if missing:
            raise ExamError(
                f"Not set up, or no key: {', '.join(missing)}. Ready: {', '.join(sorted(ready))}."
            )
        seeded: dict[str, tuple[Path, dict[str, Any]]] = {}
        for material in chosen:
            seeded[material.id] = materials.ensure_set(
                results_root, material, settings_file, model_client_factory, overrides, say, options.reseed
            )  # fmt: skip
    if options.seed_only:
        say(f"Seeded {len(seeded)} set(s); nothing built (--seed-only).")
        return results_root

    started = datetime.now()
    folder = results_root / f"{started:%Y%m%d-%H%M}-quality-{options.label}"
    folder.mkdir(parents=True, exist_ok=True)
    code = code_hash()
    jobs = [
        Job(m, label, method)
        for m in chosen
        for label in options.kinds
        for method in options.methods
    ]
    by_id = {m.id: m for m in chosen}

    def work(job: Job) -> dict[str, Any]:
        template, info = seeded[job.material.id]
        key = build_key(job, model, options.effort, code, info)
        target = folder / "builds" / job.material.id / f"{job.label}-{job.method}"
        reusable = find_reusable(results_root, key) if options.reuse else None
        if reusable is None:
            folders = [
                *options.builds_from,
                *(options.baseline_from if job.method == "skills" else ()),
            ]
            for name in folders:
                earlier = results_root / name / "builds" / job.material.id / target.name
                if (earlier / "content.md").exists():
                    reusable = earlier
                    break
        try:
            if reusable is not None:
                shutil.copytree(reusable, target, dirs_exist_ok=True)
                record = json.loads((target / "meta.json").read_text(encoding="utf-8"))
                record["dir"], record["reused"] = str(target), True
                say(f"  reused {job.name} from {reusable.parent.parent.parent.name}")
            else:
                record = build_one(
                    job,
                    template,
                    info,
                    target,
                    options,
                    model,
                    model_client_factory,
                    overrides,
                    say,
                )  # noqa: E501
                record["reused"] = False
            record["key"] = key
            if not record["error"]:
                if options.render:
                    record["render"] = render(target, job.label, say)
                lint_build(record, job.material)
        except Exception as error:  # noqa: BLE001
            record = {"set": job.material.id, "label": job.label, "method": job.method,
                      "kind": job.material.brief(job.label).kind, "dir": str(target),
                      "error": f"{type(error).__name__}: {error}", "key": key, "model": model}  # fmt: skip
        (target / "meta.json").parent.mkdir(parents=True, exist_ok=True)
        (target / "meta.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        say(f"  done {job.name}" + (f" - ERROR: {record['error']}" if record["error"] else ""))
        return record

    say(f"{len(jobs)} builds, {max(1, options.jobs)} at a time; model {model}, judge {judge_ref}.")
    with ThreadPoolExecutor(max_workers=max(1, options.jobs)) as pool:
        builds = list(pool.map(work, jobs))

    pairs: list[dict[str, Any]] = []
    judge_usage = {"calls": 0, "tokens": 0}
    judge_model = gateway.get(judge_ref)
    if options.do_judge and judge_model is not None:
        with tempfile.TemporaryDirectory(prefix="gunther-quality-judge-") as scratch:
            sf = write_settings_file(Path(scratch) / "service-settings.json", settings_source)
            probe_dir = Path(scratch) / "probe"
            probe_dir.mkdir()
            from evals.ask.run import make_app as _make

            with TestClient(
                _make(probe_dir, sf, "x" * 64, model_client_factory, overrides)
            ) as client:
                live = client.app.state.models
                live_judge = live.get(judge_ref)
                meter = judging.Meter(live)
                good = [b for b in builds if not b["error"]]
                say(f"Judging {len(good)} artifacts with {judge_ref} ...")

                def judged(build: dict[str, Any]) -> None:
                    try:
                        judge_build(build, by_id[build["set"]], live, live_judge, meter)
                    except Exception as error:  # noqa: BLE001
                        build["judgeError"] = f"{type(error).__name__}: {error}"
                    (Path(build["dir"]) / "meta.json").write_text(
                        json.dumps(build, ensure_ascii=False, indent=1), encoding="utf-8"
                    )  # noqa: E501

                with ThreadPoolExecutor(max_workers=max(1, options.jobs)) as pool:
                    list(pool.map(judged, good))
                if options.visual_judge:
                    looker = live.get(options.visual_judge)
                    say(f"Looking at the rendered decks with {options.visual_judge} ...")

                    def looked(build: dict[str, Any]) -> None:
                        if looker is None:
                            build["visualJudge"] = {"error": "model is not ready"}
                        else:
                            try:
                                visual_build(build, live, looker, meter)
                            except Exception as error:  # noqa: BLE001
                                build["visualJudge"] = {"error": f"{type(error).__name__}: {error}"}
                        (Path(build["dir"]) / "meta.json").write_text(
                            json.dumps(build, ensure_ascii=False, indent=1), encoding="utf-8"
                        )

                    with ThreadPoolExecutor(max_workers=max(1, options.jobs)) as pool:
                        list(pool.map(looked, [b for b in good if b.get("pngs")]))
                pairs = judge_pairs(builds, by_id, live, live_judge, meter, options.jobs, say)
        judge_usage = {
            "calls": sum(b.get("judgeCalls", 0) for b in builds) + sum(p["calls"] for p in pairs),
            "tokens": sum(b.get("judgeTokens", 0) for b in builds) + sum(p["tokens"] for p in pairs),
        }  # fmt: skip

    raw = {
        "label": options.label, "started": started.isoformat(timespec="seconds"),
        "model": model, "judge": judge_ref, "visualJudge": options.visual_judge, "judgeSeesImages": bool(judge_model and judge_model.vision),
        "methods": options.methods, "kinds": options.kinds, "codeHash": code,
        "sets": {
            m.id: {
                "title": m.title, "question": m.question, "language": m.language, "note": m.note,
                "sources": seeded[m.id][1]["sources"], "failedJobs": seeded[m.id][1]["failedJobs"],
                "deckBrief": m.deck.text(), "reportBrief": m.report.text(),
            } for m in chosen
        },
        "builds": builds, "pairwise": pairs, "judgeUsage": judge_usage,
    }  # fmt: skip
    (folder / "raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    text = report.write(folder)
    say(text.split("\n## Artifacts")[0])
    say(f"Written to {folder}")
    return folder


def parse(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(prog="python -m evals.quality.run", description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        help="a service-settings.json (else SERVICE_SETTINGS_FILE, else .env)",
    )  # noqa: E501
    parser.add_argument("--sets", default="", help="set ids, comma-separated (default: all)")
    parser.add_argument("--methods", default="skills", help="skills, studio or both")
    parser.add_argument("--kinds", default="deck,report")
    parser.add_argument("--model", help="writing model ref (default: the Outputs job's)")
    parser.add_argument("--judge", help=f"judge model ref (default: {DEFAULT_JUDGE})")
    parser.add_argument("--effort", help="writing effort (default: the Outputs job's)")
    parser.add_argument("--jobs", type=int, default=2, help="builds at the same time")
    parser.add_argument("--label", default="quality")
    parser.add_argument(
        "--seed-only", action="store_true", help="read the sets into their libraries and stop"
    )
    parser.add_argument(
        "--builds-from",
        default="",
        help="results folders (comma-separated) whose builds of any method are reused (to judge again)",
    )
    parser.add_argument(
        "--baseline-from",
        default="",
        help="results folders (comma-separated) whose skills builds are reused as they are",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="skip a build whose inputs, method, model and code already have a result",
    )  # noqa: E501
    parser.add_argument("--reseed", action="store_true", help="read the sets again")
    parser.add_argument(
        "--visual-judge",
        help="vision model ref (e.g. deepseek/deepseek-flash) that scores the rendered slides "
        "1-10 and gives each a verdict; fills the report's Visual column",
    )
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--no-judge", action="store_true", help="build and lint only")
    args = parser.parse_args(argv)
    split = lambda text: [p.strip() for p in text.split(",") if p.strip()]  # noqa: E731
    return Options(
        settings=args.settings, sets=split(args.sets), methods=split(args.methods),
        kinds=split(args.kinds), model=args.model, judge=args.judge, jobs=max(1, args.jobs),
        label=args.label, reuse=args.reuse,
        seed_only=args.seed_only,
        builds_from=tuple(n for n in args.builds_from.split(",") if n),
        baseline_from=tuple(n for n in args.baseline_from.split(",") if n), reseed=args.reseed, effort=args.effort,
        render=not args.no_render, do_judge=not args.no_judge, visual_judge=args.visual_judge,
    )  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    try:
        run_quality(parse(argv), say=lambda text: print(text, flush=True))
    except ExamError as error:
        print(f"The evaluation could not run: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
