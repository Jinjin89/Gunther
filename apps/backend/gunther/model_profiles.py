"""How each model thinks, and how one effort scale turns into its own parameters.

Gunther offers one scale everywhere: off, low, medium, high, max. Providers
spell thinking differently (DeepSeek: ``thinking`` + ``reasoning_effort``;
Qwen: ``enable_thinking`` + a token budget; GLM-5 and Kimi K3 always think;
Kimi K2.6 only switches it on or off). A *dialect* is one such spelling, and a
model's profile says which dialect it speaks and whether it can see images.

Built-in profiles are matched by provider and model name; a model added by
hand can be given a dialect in Settings. When a model cannot do the exact
level asked for, the nearest one it can do is used, and the answer says so.

Sources (September 2026): api-docs.deepseek.com (thinking mode, vision,
models), platform.kimi.ai (thinking models), docs.bigmodel.cn (GLM-5.3),
alibabacloud.com Model Studio (deep thinking).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

Effort = Literal["off", "low", "medium", "high", "max"]
EFFORTS: tuple[Effort, ...] = ("off", "low", "medium", "high", "max")
EFFORT_LABELS = {"off": "Off", "low": "Low", "medium": "Medium", "high": "High", "max": "Max"}

ProviderKind = Literal["deepseek", "kimi", "glm", "qwen", "openai", "compatible"]


@dataclass(frozen=True)
class Params:
    """What goes into the request for one effort level."""

    body: dict[str, Any] = field(default_factory=dict)  # sent as extra_body
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Dialect:
    id: str
    label: str
    # The levels this dialect honours exactly, in scale order.
    levels: tuple[Effort, ...]
    params: dict[Effort, Params]
    # Earlier reasoning must go back with the conversation (Kimi K2.7 Code).
    keeps_reasoning: bool = False
    # Some thinking models truncate their answer below this many output tokens.
    min_max_tokens: int | None = None
    # How a level is named for this model, e.g. "On" where thinking only switches.
    names: dict[str, str] = field(default_factory=dict)

    def name(self, level: Effort) -> str:
        return self.names.get(level, EFFORT_LABELS[level])

    @property
    def can_disable(self) -> bool:
        return "off" in self.levels

    def resolve(self, effort: Effort) -> tuple[Effort | None, Params]:
        """The level used for ``effort``, and its parameters.

        ``None`` means the model has no effort setting at all: it is sent nothing.
        """

        if not self.levels:
            return None, Params()
        # Asking for any thinking never turns thinking off.
        candidates = (
            self.levels
            if effort == "off"
            else tuple(level for level in self.levels if level != "off")
        )
        applied = effort if effort in candidates else nearest(effort, candidates)
        return applied, self.params[applied]


def nearest(effort: Effort, levels: tuple[Effort, ...]) -> Effort:
    """The closest level on the scale; between two, the stronger one."""

    wanted = EFFORTS.index(effort)
    return min(
        levels, key=lambda level: (abs(EFFORTS.index(level) - wanted), -EFFORTS.index(level))
    )


def _thinking(kind: str, effort: str | None = None) -> Params:
    return Params(body={"thinking": {"type": kind}}, reasoning_effort=effort)


DIALECTS: dict[str, Dialect] = {
    dialect.id: dialect
    for dialect in (
        Dialect(
            "deepseek",
            "Thinking on/off, effort low/high/max (DeepSeek)",
            ("off", "low", "high", "max"),
            {
                "off": _thinking("disabled"),
                "low": _thinking("enabled", "low"),
                "high": _thinking("enabled", "high"),
                "max": _thinking("enabled", "max"),
            },
        ),
        Dialect(
            "effort_always_on",
            "Always thinks, effort low/high/max (Kimi K3, GLM-5)",
            ("low", "high", "max"),
            {level: Params(reasoning_effort=level) for level in ("low", "high", "max")},
        ),
        Dialect(
            "thinking_switch",
            "Thinking on or off (Kimi K2.6, GLM-4.x)",
            ("off", "high"),
            {"off": _thinking("disabled"), "high": _thinking("enabled")},
            min_max_tokens=16_000,
            names={"high": "On"},
        ),
        Dialect(
            "qwen_budget",
            "Thinking on/off with a token budget (Qwen)",
            ("off", "low", "medium", "high", "max"),
            {
                "off": Params(body={"enable_thinking": False}),
                "low": Params(body={"enable_thinking": True, "thinking_budget": 2_048}),
                "medium": Params(body={"enable_thinking": True, "thinking_budget": 8_192}),
                "high": Params(body={"enable_thinking": True, "thinking_budget": 16_384}),
                "max": Params(body={"enable_thinking": True, "thinking_budget": 32_768}),
            },
        ),
        Dialect(
            "reasoning_effort",
            "Effort low/medium/high (OpenAI and most self-hosted servers)",
            ("low", "medium", "high"),
            {level: Params(reasoning_effort=level) for level in ("low", "medium", "high")},
        ),
        Dialect(
            "always_thinks",
            "Always thinks, no setting (Kimi K2.7 Code, reasoner models)",
            (),
            {},
            keeps_reasoning=True,
            min_max_tokens=16_000,
        ),
        Dialect("none", "No thinking setting", (), {}),
    )
}


@dataclass(frozen=True)
class Rule:
    kind: ProviderKind
    pattern: str
    dialect: str
    vision: bool = False


# First match wins. Unknown models speak no dialect and are shown text only,
# until someone says otherwise in Settings.
RULES: tuple[Rule, ...] = (
    Rule("deepseek", r"deepseek-reasoner", "always_thinks"),
    Rule("deepseek", r"deepseek-chat", "none"),
    Rule("deepseek", r"flash", "deepseek", vision=True),
    Rule("deepseek", r"deepseek", "deepseek"),
    Rule("kimi", r"k2\.7-code|k2-thinking", "always_thinks"),
    Rule("kimi", r"kimi-k2\.[56]", "thinking_switch", vision=True),
    Rule("kimi", r"kimi-k3", "effort_always_on", vision=True),
    Rule("kimi", r"vision", "none", vision=True),
    Rule("glm", r"glm-5\.\d+-flash", "effort_always_on", vision=True),
    Rule("glm", r"glm-5", "effort_always_on"),
    Rule("glm", r"glm-4\.\d+v", "thinking_switch", vision=True),
    Rule("glm", r"glm-4\.[5-9]", "thinking_switch"),
    Rule("qwen", r"thinking|qwq", "always_thinks"),
    Rule("qwen", r"vl|omni", "qwen_budget", vision=True),
    Rule("qwen", r"qwen3|qwen-(plus|max|flash|turbo)", "qwen_budget"),
    Rule("openai", r"^(gpt-5|o\d)", "reasoning_effort", vision=True),
    Rule("openai", r"^gpt-4", "none", vision=True),
)


@dataclass(frozen=True)
class ModelProfile:
    dialect: Dialect
    vision: bool


def profile_for(
    kind: str, model: str, *, dialect: str | None = None, vision: bool | None = None
) -> ModelProfile:
    """A model's profile: built in by name, with what Settings says on top."""

    rule = next(
        (
            rule
            for rule in RULES
            if rule.kind == kind and re.search(rule.pattern, model, re.IGNORECASE)
        ),
        None,
    )
    chosen = DIALECTS.get(dialect or "") or DIALECTS[rule.dialect if rule else "none"]
    return ModelProfile(chosen, vision if vision is not None else bool(rule and rule.vision))
