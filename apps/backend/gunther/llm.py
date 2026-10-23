"""One way to talk to every language model: OpenAI-style Chat Completions.

DeepSeek, Kimi, GLM, Qwen, OpenAI and self-hosted servers (vLLM, Ollama, LM
Studio) all speak Chat Completions, so every feature asks its model through
here: ``complete`` for prose, ``complete_json`` for structured output checked
against a schema. The model's profile (see model_profiles) turns the requested
effort into that provider's own parameters.

A conversation is kept in a neutral form (``Turn``) and rebuilt for whichever
model answers next, so switching models halfway through just works. A model's
own reasoning is sent back only to that same model, and only when it needs it.

Failures raise ``ModelError`` with a sentence a person can act on. Nothing here
invents an answer when a model fails.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

import httpx
import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from gunther import trace, usage
from gunther.model_profiles import EFFORT_LABELS, Effort, ModelProfile, Params

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
REQUEST_TIMEOUT_SECONDS = 300.0
# Keys a self-hosted server ignores; the SDK insists on one.
NO_KEY = "not-needed"
# A run may bound actual requests, including the JSON repair attempt. Context-local
# so concurrent conversations and background brief maintenance stay independent.
REQUEST_ADMISSION: ContextVar[Callable[[], None] | None] = ContextVar(
    "gunther_request_admission", default=None
)


@dataclass(frozen=True)
class ModelInfo:
    """One model of one provider, ready to be asked."""

    provider_id: str
    provider_name: str
    provider_kind: str
    model_id: str
    label: str
    base_url: str
    api_key: str | None
    profile: ModelProfile

    @property
    def ref(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    @property
    def display(self) -> str:
        return f"{self.provider_name} · {self.label}"

    @property
    def method(self) -> str:
        """What a stored output says wrote it, e.g. ``DeepSeek:deepseek-flash``."""

        return f"{self.provider_name}:{self.model_id}"[:120]

    @property
    def vision(self) -> bool:
        return self.profile.vision


@dataclass(frozen=True)
class Image:
    media_type: str
    data: bytes

    @classmethod
    def from_path(cls, path: Path, media_type: str) -> Image:
        return cls(media_type, path.read_bytes())

    def url(self) -> str:
        return f"data:{self.media_type};base64,{base64.b64encode(self.data).decode()}"


@dataclass(frozen=True)
class Turn:
    """One message of a conversation, as Gunther keeps it."""

    role: Literal["user", "assistant"]
    content: str
    # Which model wrote an assistant turn, and its reasoning, kept apart.
    model_ref: str | None = None
    reasoning: str | None = None


@dataclass(frozen=True)
class Completion:
    text: str
    model: ModelInfo
    reasoning: str | None = None
    effort: Effort | None = None  # asked for
    effort_applied: Effort | None = None  # what the model was sent
    # Anything the answer should admit to: a level it could not do, images it could not see.
    notes: tuple[str, ...] = ()

    @property
    def effort_label(self) -> str | None:
        if self.effort_applied is None:
            return None
        return self.model.profile.dialect.name(self.effort_applied)


class ModelError(RuntimeError):
    """A model could not answer. The message says why, in plain words."""


ClientFactory = Callable[[ModelInfo], Any]


def _host(url: str) -> str:
    return urlsplit(url).netloc or url


def _openai_client(model: ModelInfo) -> OpenAI:
    return OpenAI(
        api_key=model.api_key or NO_KEY,
        base_url=model.base_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=1,
    )


class ModelGateway:
    def __init__(
        self,
        models: Sequence[ModelInfo],
        roles: dict[str, tuple[str, Effort]] | None = None,
        *,
        client_factory: ClientFactory = _openai_client,
        recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._models = {model.ref: model for model in models}
        self._roles = roles or {}
        self._client_factory = client_factory
        self._recorder = recorder
        self._without_usage: set[str] = set()
        self._clients: dict[tuple[str, str | None], Any] = {}
        self._lock = threading.Lock()

    # Which model --------------------------------------------------------------

    @property
    def models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get(self, ref: str | None) -> ModelInfo | None:
        return self._models.get(ref or "")

    def for_role(self, role: str) -> tuple[ModelInfo, Effort] | None:
        """The model and effort a job uses, or None when it has none ready."""

        choice = self._roles.get(role)
        if choice is None:
            return None
        model = self.get(choice[0])
        return (model, choice[1]) if model else None

    def _client(self, model: ModelInfo) -> Any:
        key = (model.base_url, model.api_key)
        with self._lock:
            if key not in self._clients:
                self._clients[key] = self._client_factory(model)
            return self._clients[key]

    # Asking -------------------------------------------------------------------

    def complete(
        self,
        model: ModelInfo,
        *,
        system: str,
        messages: Sequence[Turn] | str,
        images: Sequence[Image] = (),
        effort: Effort | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion:
        """Ask once. ``on_text`` hears the answer's text as it is written."""

        admit = REQUEST_ADMISSION.get()
        if admit is not None:
            admit()
        turns = [Turn("user", messages)] if isinstance(messages, str) else list(messages)
        if trace.current() is None:
            return self._complete(
                model, system, turns, images, effort, json_mode, max_tokens, on_text
            )
        # Settings → Developer keeps how answers are made: the exact request and reply.
        with trace.step(
            "model",
            model.display,
            model=model.ref,
            effort=effort,
            json=json_mode,
            system=system,
            messages=[{"role": turn.role, "content": turn.content} for turn in turns],
            images=len(images),
        ) as call:
            completion = self._complete(
                model, system, turns, images, effort, json_mode, max_tokens, on_text, call
            )
            trace.update(
                call,
                reply=completion.text,
                reasoning=completion.reasoning,
                effortApplied=completion.effort_applied,
                notes=list(completion.notes),
            )
            return completion

    def _complete(
        self,
        model: ModelInfo,
        system: str,
        turns: list[Turn],
        images: Sequence[Image],
        effort: Effort | None,
        json_mode: bool,
        max_tokens: int | None,
        on_text: Callable[[str], None] | None,
        call: dict[str, Any] | None = None,
    ) -> Completion:
        notes: list[str] = []
        if images and not model.vision:
            notes.append(f"{model.label} cannot see images; it read their text instead.")
            images = ()
        applied, params = model.profile.dialect.resolve(effort) if effort else (None, Params())
        if effort and applied and applied != effort:
            notes.append(
                f"{model.label} has no {EFFORT_LABELS[effort]} setting; "
                f"used {model.profile.dialect.name(applied)}."
            )
        payload = self._messages(model, system, turns, images)
        minimum = model.profile.dialect.min_max_tokens
        if minimum and (max_tokens is None or max_tokens < minimum):
            max_tokens = minimum

        send_effort, send_json = params, json_mode
        broke = emptied = False
        with self._lock:
            send_usage = model.ref not in self._without_usage
        while True:
            try:
                counts = _usage(None)
                at = datetime.now(UTC).replace(tzinfo=None)
                started = time.perf_counter()
                status = "error"
                try:
                    text, reasoning, finish, _ = self._stream(
                        model,
                        payload,
                        send_effort,
                        send_json,
                        max_tokens,
                        on_text,
                        include_usage=send_usage,
                        counts=counts,
                    )
                    if text.strip():
                        status = (
                            "ok" if any(v is not None for v in counts.values()) else "unreported"
                        )
                        if not send_usage:
                            with self._lock:
                                self._without_usage.add(model.ref)
                    if call is not None:
                        trace.update(call, finish=finish)
                finally:
                    entry = {
                        "id": uuid.uuid4().hex,
                        "at": at,
                        "provider_id": model.provider_id,
                        "model_id": model.model_id,
                        **usage.current(),
                        **counts,
                        "status": status,
                        "ms": round((time.perf_counter() - started) * 1000),
                    }
                    if call is not None:
                        kept = {**entry, "at": at.replace(tzinfo=UTC).isoformat()}
                        trace.update(
                            call,
                            usage=kept,
                            usageAttempts=[*call.get("usageAttempts", []), kept],
                        )
                    if self._recorder is not None:
                        try:
                            self._recorder(entry)
                        except Exception:
                            logger.warning("Could not record model usage", exc_info=True)
                if not text.strip() and finish != "length" and not emptied:
                    # An empty answer that did not run out of room is asked for once more.
                    emptied = True
                    logger.info("%s returned an empty answer, asking again", model.ref)
                    if admit := REQUEST_ADMISSION.get():
                        admit()
                    continue
                break
            except openai.BadRequestError as error:
                # Prefer the named setting; otherwise drop settings one at a time.
                detail = _detail(error).lower()
                if send_usage and ("stream_options" in detail or "include_usage" in detail):
                    send_usage = False
                    with self._lock:
                        self._without_usage.add(model.ref)
                    if admit := REQUEST_ADMISSION.get():
                        admit()
                    continue
                # A setting this server does not know: once without it, and say so.
                if send_effort.body or send_effort.reasoning_effort:
                    logger.info("%s refused the effort setting: %s", model.ref, error)
                    send_effort, applied = Params(), None
                    notes.append(f"{model.label} refused the effort setting; used its default.")
                    if admit := REQUEST_ADMISSION.get():
                        admit()
                    continue
                if send_json:
                    send_json = False
                    if admit := REQUEST_ADMISSION.get():
                        admit()
                    continue
                if send_usage:
                    send_usage = False
                    if admit := REQUEST_ADMISSION.get():
                        admit()
                    continue
                raise ModelError(
                    f"{model.provider_name} refused the request: {_detail(error)}"
                ) from error
            except openai.APIError as error:
                raise ModelError(_explain(model, error)) from error
            except httpx.HTTPError as error:
                # The connection broke while the answer streamed in. A request whose text no
                # one has seen yet is sent once more; otherwise it is a model error like others.
                if on_text is None and not broke:
                    broke = True
                    logger.info("%s: the connection broke, sending again: %s", model.ref, error)
                    if admit := REQUEST_ADMISSION.get():
                        admit()
                    continue
                what = str(error) or type(error).__name__
                raise ModelError(
                    f"The connection to {model.provider_name} broke: {what}"
                ) from error
        if not text.strip():
            raise ModelError(
                f"{model.display} ran out of room while thinking. Try a lower effort."
                if finish == "length"
                else f"{model.display} returned an empty answer."
            )
        return Completion(
            text=text.strip(),
            model=model,
            reasoning=reasoning or None,
            effort=effort,
            effort_applied=applied,
            notes=tuple(notes),
        )

    def complete_json(
        self,
        model: ModelInfo,
        schema: type[T],
        *,
        system: str,
        prompt: str,
        images: Sequence[Image] = (),
        history: Sequence[Turn] = (),
        schema_at: Literal["system", "end"] = "system",
        effort: Effort | None = None,
    ) -> tuple[T, Completion]:
        """Structured output, checked against ``schema``; one retry with the error shown."""

        instructions = (
            "Reply with one JSON object and nothing else. It must match this "
            f"JSON schema:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
        )
        if schema_at == "end":
            prompt += "\n\n" + instructions
        else:
            system += "\n\n" + instructions
        turns = [*history, Turn("user", prompt)]
        for attempt in range(2):
            completion = self.complete(
                model,
                system=system,
                messages=turns,
                images=images,
                effort=effort,
                json_mode=True,
            )
            try:
                return schema.model_validate_json(_json_text(completion.text)), completion
            except ValidationError as error:
                if attempt:
                    raise ModelError(
                        f"{model.display} answered in a shape Gunther could not read."
                    ) from error
                turns = [
                    *turns,
                    Turn("assistant", completion.text, model.ref),
                    Turn(
                        "user",
                        "That was not valid JSON for the schema "
                        f"({error.error_count()} problems, first: {error.errors()[0]['msg']}). "
                        "Reply again with the JSON object only.",
                    ),
                ]
        raise AssertionError("unreachable")

    # Wire format --------------------------------------------------------------

    @staticmethod
    def _messages(
        model: ModelInfo, system: str, turns: list[Turn], images: Sequence[Image]
    ) -> list[dict[str, Any]]:
        keeps = model.profile.dialect.keeps_reasoning
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for index, turn in enumerate(turns):
            message: dict[str, Any] = {"role": turn.role, "content": turn.content}
            if (
                turn.role == "assistant"
                and keeps
                and turn.reasoning
                and turn.model_ref == model.ref
            ):
                # Only the model that thought it gets its thinking back.
                message["reasoning_content"] = turn.reasoning
            if images and index == len(turns) - 1 and turn.role == "user":
                message["content"] = [
                    {"type": "text", "text": turn.content},
                    *({"type": "image_url", "image_url": {"url": image.url()}} for image in images),
                ]
            messages.append(message)
        return messages

    def _stream(
        self,
        model: ModelInfo,
        messages: list[dict[str, Any]],
        params: Params,
        json_mode: bool,
        max_tokens: int | None,
        on_text: Callable[[str], None] | None = None,
        *,
        include_usage: bool = True,
        counts: dict[str, int | None] | None = None,
    ) -> tuple[str, str, str | None, dict[str, int | None]]:
        request: dict[str, Any] = {"model": model.model_id, "messages": messages, "stream": True}
        if include_usage:
            request["stream_options"] = {"include_usage": True}
        if params.reasoning_effort:
            request["reasoning_effort"] = params.reasoning_effort
        if params.body:
            request["extra_body"] = params.body
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        if max_tokens:
            request["max_tokens"] = max_tokens
        text: list[str] = []
        reasoning: list[str] = []
        finish: str | None = None
        counts = counts if counts is not None else _usage(None)
        client = self._client(model)
        if REQUEST_ADMISSION.get() is not None and isinstance(client, OpenAI):
            client = client.with_options(max_retries=0)
        for chunk in client.chat.completions.create(**request):
            counted = getattr(chunk, "usage", None)
            if counted is not None:
                counts.update(
                    {key: value for key, value in _usage(counted).items() if value is not None}
                )
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            finish = choice.finish_reason or finish
            if delta is None:
                continue
            if delta.content:
                text.append(delta.content)
                if on_text:
                    on_text(delta.content)
            extra = getattr(delta, "model_extra", None) or {}
            thought = extra.get("reasoning_content") or extra.get("reasoning")
            if isinstance(thought, str):
                reasoning.append(thought)
        return "".join(text), "".join(reasoning), finish, counts


def _usage(counted: Any) -> dict[str, int | None]:
    """Provider-reported counts only; cached and reasoning are subsets, not extras."""

    def field(value: Any, key: str) -> Any:
        return value.get(key) if isinstance(value, dict) else getattr(value, key, None)

    def count(*values: Any) -> int | None:
        return next((v for v in values if type(v) is int and v >= 0), None)

    return {
        "input": count(field(counted, "prompt_tokens")),
        "cached": count(
            field(counted, "prompt_cache_hit_tokens"),
            field(field(counted, "prompt_tokens_details"), "cached_tokens"),
            field(counted, "cached_tokens"),
        ),
        "output": count(field(counted, "completion_tokens")),
        "reasoning": count(field(field(counted, "completion_tokens_details"), "reasoning_tokens")),
    }


def _json_text(text: str) -> str:
    """The JSON object inside a reply that may wrap it in a code fence or a sentence."""

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _detail(error: openai.APIStatusError) -> str:
    body = error.body if isinstance(error.body, dict) else {}
    inner = body.get("error") if isinstance(body.get("error"), dict) else body
    message = (inner or {}).get("message") or error.message or ""
    return str(message).strip()[:240] or f"status {error.status_code}"


def _explain(model: ModelInfo, error: openai.APIError) -> str:
    name = model.provider_name
    if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
        return f"{name} did not accept the API key."
    if isinstance(error, openai.NotFoundError):
        return f"{name} has no model called {model.model_id}, or the base URL is wrong."
    if isinstance(error, openai.RateLimitError):
        return f"{name} is busy or out of credit ({_detail(error)}). Try again shortly."
    if isinstance(error, openai.APITimeoutError):
        return f"{name} did not answer in time."
    if isinstance(error, openai.APIConnectionError):
        return f"Could not reach {_host(model.base_url)}."
    if isinstance(error, openai.APIStatusError):
        return f"{name} had a problem ({error.status_code}): {_detail(error)}"
    return f"{name} could not answer: {type(error).__name__}"
