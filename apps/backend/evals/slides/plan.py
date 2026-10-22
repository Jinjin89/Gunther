"""Bounded live outline comparison using synthetic sources, never the user's library."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from gunther.agent import Evidence
from gunther.config import PROJECT_ROOT, Settings
from gunther.llm import REQUEST_ADMISSION, ModelError
from gunther.model_registry import build_gateway, effective
from gunther.outputs import Pool, Spec
from gunther.service_settings import ServiceSettingsStore
from gunther.skill_runner import Approach, Card, SkillAgents
from gunther.skillbook import load_skill, skill_for


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--protocol", type=Path, help="Archived baseline OUTLINE/SHAPES protocol")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    packets = json.loads(Path(__file__).with_name("packets.json").read_text())
    base = Settings()
    store = ServiceSettingsStore(
        base.service_settings_file or PROJECT_ROOT / "data/service-settings.json"
    )
    settings = store.apply(base)
    gateway = build_gateway(effective(*store.models(), settings), settings)
    chosen = gateway.for_role("outputs") or gateway.for_role("ask")
    if chosen is None:
        raise SystemExit("No configured Outputs model")
    model, effort = chosen
    skill = load_skill(args.skill_root) if args.skill_root else skill_for("slides")
    if args.protocol:
        from gunther import skill_runner

        protocol = json.loads(args.protocol.read_text())
        skill_runner.OUTLINE = protocol["OUTLINE"]
        skill_runner.SHAPES = protocol["SHAPES"]
    result = {
        "model": model.display,
        "effort": effort,
        "skillVersion": skill.version,
        "synthetic": True,
        "scope": (
            "Outline planning from fixed source-backed cards, not a full extraction or writing run."
        ),
        "packets": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for packet in packets:
        if args.only and packet["id"] not in args.only.split(","):
            continue
        pool = Pool(40)
        for source in packet["sources"]:
            pool.adopt(
                Evidence(
                    kind="library",
                    title=source["title"],
                    text=source["text"],
                    locator=source["locator"],
                )
            )
        agents = SkillAgents(gateway, skill, use=packet["use"])
        agents.cards = [Card.model_validate(c) for c in packet["cards"]]
        agents.approach = Approach(
            packet["brief"],
            packet["sources"][0]["text"],
            "Explain the available evidence and its limits to the stated audience.",
            "what",
            use=packet["use"],
            pages=6,
        )
        spec = Spec(
            "slides",
            "student"
            if packet["id"] in {"numeric", "reading-zh", "process", "figure"}
            else "scientist",
            brief=packet["brief"],
        )
        calls = [0]

        def admit(calls=calls) -> None:
            calls[0] += 1
            if calls[0] > 6:
                raise ModelError("Outline probe reached its six-request budget")

        token = REQUEST_ADMISSION.set(admit)
        entry = {
            "id": packet["id"],
            "inputHash": hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest(),
            "sources": packet["sources"],
            "cards": packet["cards"],
        }
        start = time.monotonic()
        try:
            title, pages = agents.structure(model, effort, spec, lambda _: None, pool=pool)
            entry.update(title=title, outline=[asdict(p) for p in pages], review=agents.record())
        except ModelError as error:
            entry["error"] = str(error)
        finally:
            REQUEST_ADMISSION.reset(token)
            entry.update(calls=calls[0], seconds=round(time.monotonic() - start, 2))
            result["packets"].append(entry)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "id": packet["id"],
                    "pages": len(entry.get("outline", [])),
                    "calls": calls[0],
                    "error": entry.get("error"),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
