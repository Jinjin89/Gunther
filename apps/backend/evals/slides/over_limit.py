"""Seed an overlong candidate and exercise real model review and evidence-preserving repair."""

import argparse
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
from gunther.skill_runner import Approach, Card, PlannedStructure, SkillAgents
from gunther.skillbook import skill_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    packet = json.loads(Path(__file__).with_name("packets.json").read_text())[0]
    baseline = json.loads((args.directory / "baseline/outlines.json").read_text())
    initial = next(p["outline"] for p in baseline["packets"] if p["id"] == packet["id"])
    sections = [dict(page) for page in initial]
    sections.insert(
        3,
        {
            "heading": "The quality limitation must remain in the decision",
            "cards": [3, 4],
            "goal": "Retain limits",
        },
    )
    for i, page in enumerate(sections, 1):
        page["id"] = f"p{i}"
        page["requires"] = []
    base = Settings()
    store = ServiceSettingsStore(
        base.service_settings_file or PROJECT_ROOT / "data/service-settings.json"
    )
    settings = store.apply(base)
    gateway = build_gateway(effective(*store.models(), settings), settings)
    model, effort = gateway.for_role("outputs") or gateway.for_role("ask")
    agents = SkillAgents(gateway, skill_for("slides"), use=packet["use"])
    agents.cards = [Card.model_validate(card) for card in packet["cards"]]
    agents.approach = Approach(
        packet["brief"],
        "Compare the measured time and unmeasured quality",
        "Decide from the available evidence",
        "which",
    )
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
    calls = [0]

    def admit():
        calls[0] += 1
        if calls[0] > 4:
            raise ModelError("Over-limit probe reached its four-request budget")

    token = REQUEST_ADMISSION.set(admit)
    start = time.monotonic()
    try:
        title, planned = agents._review_outline(
            model,
            effort,
            Spec("slides", "scientist", brief=packet["brief"]),
            pool,
            lambda _: None,
            PlannedStructure(title=packet["title"], sections=sections),
            ["Brief: " + packet["brief"], "Evidence cards:", *agents._cards_text()],
            "Return the complete outline as JSON: title, sections. Exactly 6 slides. "
            "Retain every assigned card, including limits, by merging or moving support. "
            "Use stable ids and supported messages. Layouts must be supported by the skill.",
        )
    finally:
        REQUEST_ADMISSION.reset(token)
    protected = {card for page in sections for card in page["cards"]}
    retained = {card for page in planned for card in page.cards}
    assert protected <= retained and len(planned) == 6
    result = {
        "kind": "Seeded seven-page candidate; live model review and repair",
        "model": model.display,
        "before": sections,
        "after": [asdict(page) for page in planned],
        "title": title,
        "calls": calls[0],
        "seconds": round(time.monotonic() - start, 2),
        "allAssignedCardsRetained": True,
        "review": agents.record(),
    }
    (args.directory / "over-limit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "before": len(sections),
                "after": len(planned),
                "cardsRetained": len(retained),
                "calls": calls[0],
            }
        )
    )


if __name__ == "__main__":
    main()
