"""Render and attach audio to a sealed Studio script version."""

import asyncio
import json
import logging
from dataclasses import replace
from math import isclose
from uuid import uuid4

from fastapi import Request
from sqlalchemy.orm import Session

from gunther.answer_runs import Stopped, check_stop
from gunther.artifact_read import artifact_detail
from gunther.artifact_versions import ArtifactConflictError, output_library, output_version
from gunther.database import session_scope
from gunther.models import ArtifactAsset, ArtifactAudio, Asset
from gunther.output_shapes import SHAPES
from gunther.outputs_audio import SPEAKERS
from gunther.schemas import ArtifactOut, OutputVoicesOut, VoicePair
from gunther.tts_providers import TtsError, join_wav, wav_seconds
from gunther.tts_service import Resolved, TtsConfigError, clean_options, resolve

logger = logging.getLogger(__name__)
# Joining keeps the line buffers and the combined WAV in memory at the same time.
MAX_OVERVIEW_BYTES = 64 * 1024 * 1024


def _resolve(request: Request) -> tuple[Resolved | None, str | None]:
    state = request.app.state
    try:
        return resolve(state.service_settings.tts(), state.model_registry, state.speech_registry)
    except TtsConfigError:
        return None, "Choose a voice in Settings → Read aloud."


def _voice_option(resolved: Resolved):
    return next((option for option in resolved.provider.options if option.key == "voice"), None)


def _defaults(resolved: Resolved) -> VoicePair:
    option = _voice_option(resolved)
    if option is None:
        raise TtsError("The configured Read aloud provider has no voice setting.")
    first = resolved.config.options.get("voice", option.default)
    second = next((choice.value for choice in option.choices if choice.value != first), first)
    return first, second


def voice_options(request: Request) -> OutputVoicesOut:
    resolved, _ = _resolve(request)
    option = _voice_option(resolved) if resolved else None
    if not resolved or not option:
        return OutputVoicesOut(choices=[], defaults=None)
    defaults = _defaults(resolved)
    choices = {choice.value: choice.label for choice in option.choices}
    for voice in defaults:
        choices.setdefault(voice, voice)
    return OutputVoicesOut(
        choices=[{"value": value, "label": label} for value, label in choices.items()],
        defaults=defaults, allow_custom=option.allow_custom,
    )


def render_audio(
    request: Request, artifact: ArtifactOut, hear, *,
    retry: bool = False, voices: VoicePair | None = None,
) -> ArtifactOut:
    shape = SHAPES[artifact.kind]
    speak = shape.speech
    if speak is None:
        raise ValueError("This output does not support audio.")
    state = request.app.state
    sessions = state.knowledge_service.sessions
    attempt = uuid4().hex

    def live(session: Session):
        output_library(session, artifact.workspace_id, artifact.knowledge_base_id)
        current = output_version(
            session, artifact.workspace_id, artifact.knowledge_base_id, artifact.id, latest=False,
        )
        if current.content_hash != artifact.content_hash or current.content != artifact.content:
            raise ArtifactConflictError("The script changed before the recording was attached.")
        return current

    def owned(session: Session) -> ArtifactAudio:
        live(session)
        row = session.get(ArtifactAudio, artifact.id)
        if row is None or row.attempt != attempt or row.content_hash != artifact.content_hash:
            raise ArtifactConflictError("Another recording attempt replaced this one.")
        return row

    with session_scope(sessions) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        current = live(session)
        row = session.get(ArtifactAudio, artifact.id)
        if row is None:
            row = ArtifactAudio(artifact_id=artifact.id, content_hash=artifact.content_hash)
            session.add(row)
        elif not retry and row.status != "pending":
            return artifact_detail(session, current)
        elif row.content_hash == artifact.content_hash and voices is None:
            saved = json.loads(row.voices_json)
            voices = tuple(saved) if saved else None
        row.content_hash, row.attempt = artifact.content_hash, attempt
        row.status, row.error, row.asset_id = "pending", None, None
        row.voices_json, row.timings_json = json.dumps(voices), "[]"

    def progress(status: str, detail: str = "") -> None:
        hear({"type": "stage", "id": "audio", "label": "Recording audio",
              "state": status, "detail": detail})

    async def make(resolved: Resolved, selected: VoicePair) -> None:
        _, units = shape.split(artifact.content)
        configs = dict(zip(SPEAKERS, (
            replace(resolved, config=replace(resolved.config, options=clean_options(
                resolved.provider, {**resolved.config.options, "voice": voice},
            ))) for voice in selected
        ), strict=True))
        parts, timings = [], []
        end, size = 0.0, 0
        for n, unit in enumerate(units, 1):
            progress("running", f"line {n} of {len(units)}")
            speaker, text = speak(unit.text)
            if len(text) > resolved.provider.max_chars:
                raise TtsError(f"Line {n} is too long for the configured voice.")
            part = await state.tts.synthesize(configs[speaker], text)
            size += len(part)
            seconds = wav_seconds(part)
            if not seconds or size > MAX_OVERVIEW_BYTES:
                raise TtsError("The recording was empty or exceeded the audio size limit.")
            timings.append({"line": n, "speaker": speaker, "start": end, "end": end + seconds})
            end += seconds
            parts.append(part)
        wav = join_wav(parts)
        if not isclose(wav_seconds(wav), end, abs_tol=1e-6):
            raise TtsError("The voice provider returned incomplete WAV audio.")
        check_stop()

        def attach(session: Session, asset: Asset) -> None:
            row = owned(session)
            if session.get(ArtifactAsset, (artifact.id, asset.id)) is None:
                session.add(ArtifactAsset(artifact_id=artifact.id, asset_id=asset.id))
                session.flush()
            row.asset_id, row.status = asset.id, "ready"
            row.voices_json, row.timings_json = json.dumps(selected), json.dumps(timings)

        await state.processing_worker.assets.preserve_bytes(
            wav, file_name="audio-overview.wav", media_type="audio/wav", publish=attach,
        )

    problem = None
    status = "error"
    try:
        resolved, problem = _resolve(request)
        if resolved is None:
            status = "needs_tts"
        elif not artifact.sections or any(not section.checked for section in artifact.sections):
            problem = "Check the script before recording audio."
        else:
            selected = voices or _defaults(resolved)
            asyncio.run(make(resolved, selected))
            status = "ready"
    except (Stopped, ArtifactConflictError, LookupError):
        raise
    except (TtsError, TtsConfigError) as error:
        problem = str(error)
    except Exception:
        logger.exception("Saving a Studio audio overview failed")
        problem = "The audio overview could not be recorded."

    with session_scope(sessions) as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if status != "ready":
            row = owned(session)
            row.status, row.error = status, problem
        artifact = artifact_detail(session, live(session))
    progress("failed" if status == "error" else "done", problem or "")
    return artifact
