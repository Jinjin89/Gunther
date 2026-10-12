"""Old recordings, heard again and grouped into paragraphs, by themselves
(docs/TRANSCRIPT_UPGRADE_PLAN.md).

A recording made before the live-transcript rebuild has a transcript of fixed 3.2-second
lines, words broken at the cuts. The upgrader decodes the saved audio, has the Recording
job's model listen to it again the way the minute check does (a window of two minutes, the
text before as context, the last sentence held back and heard again with what follows),
and has the Analysis model say where paragraphs start. Only text from listening is
written; the grouping model never writes text.

The first half of this module is pure (no database, no network). ``TranscriptUpgrader``
below it is the background runner: one recording at a time, newest first, nothing written
until everything was heard and grouped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from gunther import listen_copy, speech_registry, vad
from gunther.audio_decode import AudioUnreadable, decode_to_copy
from gunther.database import session_scope
from gunther.knowledge_index import KnowledgeIndex
from gunther.models import RecordingSession, Source, TranscriptUpgrade, utc_now
from gunther.paragraphs import ParagraphsError, group_paragraphs
from gunther.qwen_stream import BYTES_PER_MS
from gunther.realtime import RELISTEN_PAD_MS, ListenError, relisten_audio
from gunther.source_identity import source_fingerprint

logger = logging.getLogger(__name__)

# How the audio is cut up (§2.4).
STRETCH_MS = 600_000
MIN_STRETCH_MS = 120_000
WINDOW_MS = 120_000
# A window that ends this close to the stretch's end simply runs to it.
TAIL_MS = 30_000
MAX_WINDOW_MS = 180_000
MAX_SEAM_MS = 180_000
PARALLEL_STRETCHES = 3
# Providers that answer text only: windows of at least this long, ending at a pause (§2.6).
TEXT_WINDOW_MS = 60_000
MAX_TEXT_WINDOW_MS = 150_000
# How much text is sent along as context (§2.5), and how far back the old text reaches.
CONTEXT_CHARS = 400
OLD_CONTEXT_MS = 60_000
# The most sentences one grouping request may hold (the endpoint's own limit).
GROUPING_MAX = 300

# A request is tried three times, waiting this many seconds before each (§2.8).
RETRY_WAITS = (0, 5, 20)
# After a failed upgrade: tried again by itself after these, then it waits for Retry.
AUTO_RETRY_AFTER = (timedelta(minutes=10), timedelta(hours=1))
# When the model never answered (offline, a wrong key, the service down), nothing else is
# tried for these, the last one repeating; a saved setting or a Retry ends the wait.
PAUSE_AFTER = (timedelta(minutes=10), timedelta(hours=1), timedelta(hours=6))

# Nobody waits on a background upgrade, so a slow answer is waited for, unlike the live
# check's minute: Qwen takes about 25 s for two minutes of audio even from a fast link.
HEAR_TIMEOUT = httpx.Timeout(300.0, connect=20.0)

FIRST_DELAY_SECONDS = 30
POLL_SECONDS = 60
# A recording still being captured and touched this recently comes first (§2.9).
LIVE_RECENT = timedelta(minutes=2)

RECORDING_REFERENCE = re.compile(r"Local recording:\s*(rec_[a-f0-9]{24})")
TRANSCRIPT_HEADING = re.compile(r"^##[ \t]+(?:Full transcript|Transcript)[ \t]*$", re.MULTILINE)
STAMP = re.compile(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?\]\s*(.*)$")


# The old transcript ---------------------------------------------------------------


def transcript_section(content: str) -> tuple[str, str, str] | None:
    """``(head, heading_line, body)`` of a recording's content, or None without a transcript.

    The transcript is everything after the first ``## Full transcript`` or ``## Transcript``
    heading, as the desktop reads it. ``head`` is everything before the heading, untouched.
    """

    match = TRANSCRIPT_HEADING.search(content)
    if match is None:
        return None
    return content[: match.start()], match.group(0), content[match.end() :].lstrip("\n").rstrip()


def is_old_transcript(text: str) -> bool:
    """Whether the old live recorder made this: not empty, every non-empty line time-stamped.

    A pasted or typed transcript has no stamps on its lines and is left alone.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and all(STAMP.match(line) for line in lines)


def old_lines(body: str, total_ms: int | None = None) -> list[tuple[int, int, str]]:
    """``(start_ms, end_ms, text)`` per stamped line; a line ends where the next begins."""

    found: list[tuple[int, str]] = []
    for raw in body.splitlines():
        match = STAMP.match(raw.strip())
        if not match:
            continue
        hours, minutes, seconds, text = match.groups()
        found.append((((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000, text))
    lines: list[tuple[int, int, str]] = []
    for index, (start, text) in enumerate(found):
        if index + 1 < len(found):
            end = max(start, found[index + 1][0])
        else:
            end = max(start, total_ms) if total_ms else start + 3_200
        lines.append((start, end, text.strip()))
    return lines


def recording_terms(head: str) -> list[str]:
    """The ``Terms:`` line a saved summary may carry (``parseRecordingContent`` reads the same)."""

    terms: list[str] = []
    for line in head.splitlines():
        match = re.match(r"^Terms:\s*(.+)$", line)
        if match:
            terms.extend(term.strip() for term in match.group(1).split("·") if term.strip())
    return terms


# Text ---------------------------------------------------------------------------------


def _in_ranges(character: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(character)
    return any(low <= code <= high for low, high in ranges)


# Han, Hiragana and Katakana, written out because ``re`` has no script classes.
_CJK_RANGES = (
    (0x2E80, 0x2FD5),
    (0x3005, 0x3005),
    (0x3007, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303B),
    (0x3041, 0x3096),
    (0x309D, 0x309F),
    (0x30A1, 0x30FA),
    (0x30FD, 0x30FF),
    (0x31F0, 0x31FF),
    (0x32D0, 0x32FE),
    (0x3300, 0x3357),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF66, 0xFF6F),
    (0xFF71, 0xFF9D),
    (0x1B000, 0x1B16F),
    (0x20000, 0x323AF),
)
# CJK and full-width punctuation, and the ellipsis.
_CJK_PUNCTUATION_RANGES = ((0x3000, 0x303F), (0xFF00, 0xFFEF), (0x2026, 0x2026))


def join_sentences(texts: list[str]) -> str:
    """Join sentence texts: no space between CJK characters or after CJK punctuation, else one.

    The same rule as ``joinSentences`` in the desktop's ``liveTranscript.ts``.
    """

    output = ""
    for raw in texts:
        text = raw.strip()
        if not text:
            continue
        if not output:
            output = text
            continue
        last, first = output[-1], text[0]
        tight = _in_ranges(last, _CJK_PUNCTUATION_RANGES) or (
            _in_ranges(last, _CJK_RANGES) and _in_ranges(first, _CJK_RANGES)
        )
        output += ("" if tight else " ") + text
    return output


def clock_label(ms: int) -> str:
    """``mm:ss``, or ``hh:mm:ss`` after an hour: the same as the desktop's ``clockLabel``."""

    total = max(0, ms // 1000)
    hours, minutes, seconds = total // 3600, total // 60 % 60, total % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{total // 60:02d}:{seconds:02d}"


# What was heard -----------------------------------------------------------------------


@dataclass
class Heard:
    """One sentence heard again, on the recording clock; ``window`` says which request."""

    start_ms: int
    end_ms: int
    text: str
    window: str


@dataclass
class Window:
    """One stretch of audio that was heard (a minute-check window, or a seam)."""

    id: str
    start_ms: int
    end_ms: int


@dataclass
class Stretch:
    """What hearing one stretch of the recording gave."""

    sentences: list[Heard] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)


# ``hear(start_ms, end_ms, before)`` answers like ``relisten_audio``: ``{"text", "sentences"}``,
# the sentences being ``{start_ms, end_ms, text}`` on the recording clock (none without word
# times). The runner supplies the model, the terms and the audio.
Hear = Callable[[int, int, str], Awaitable[dict[str, Any]]]


def stretches(total_ms: int) -> list[tuple[int, int]]:
    """The recording cut into stretches of 10 minutes; a last one under 2 joins the one before."""

    if total_ms <= 0:
        return []
    cuts = list(range(0, total_ms, STRETCH_MS))
    spans = [(start, min(start + STRETCH_MS, total_ms)) for start in cuts]
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < MIN_STRETCH_MS:
        last = spans.pop()
        spans[-1] = (spans[-1][0], last[1])
    return spans


def _tail(text: str) -> str:
    return text[-CONTEXT_CHARS:]


def _timed(
    answer: dict[str, Any], start_ms: int, end_ms: int, window: str
) -> tuple[list[Heard], bool]:
    """The sentences of an answer, and whether they carry word times.

    An answer with text but no sentences is one sentence that fills the window.
    """

    heard = [
        Heard(int(item["start_ms"]), int(item["end_ms"]), str(item["text"]).strip(), window)
        for item in answer.get("sentences") or []
        if str(item.get("text", "")).strip() and int(item["end_ms"]) > start_ms
    ]
    if heard:
        return sorted(heard, key=lambda item: item.start_ms), True
    text = str(answer.get("text") or "").strip()
    return ([Heard(start_ms, end_ms, text, window)] if text else []), False


async def hear_stretch(
    hear: Hear,
    start_ms: int,
    end_ms: int,
    context: str = "",
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> Stretch:
    """Windows of two minutes one after another, as the minute check does (§2.4-2.5).

    Each window is heard with the confirmed text before it as context. All its sentences
    but the last are kept and the next window starts where the kept ones end, so the last
    sentence is heard again with what follows it. The stretch's last window keeps
    everything. A one-sentence answer for a window that is not the last is asked once
    more with a longer window; if it is still one sentence it is kept. The confirmed point
    always moves forward. Silence (an empty answer) just moves on.
    """

    result = Stretch()
    confirmed = start_ms
    kept_text = context
    while confirmed < end_ms:
        window_end = min(confirmed + WINDOW_MS, end_ms)
        if end_ms - window_end < TAIL_MS:
            window_end = end_ms
        window_id = f"w-{confirmed}"
        answer = await hear(confirmed, window_end, _tail(kept_text))
        sentences, timed = _timed(answer, confirmed, window_end, window_id)
        if timed and len(sentences) == 1 and window_end < end_ms:
            longer = min(confirmed + MAX_WINDOW_MS, end_ms)
            if longer > window_end:
                window_end = longer
                answer = await hear(confirmed, window_end, _tail(kept_text))
                sentences, timed = _timed(answer, confirmed, window_end, window_id)
        result.windows.append(Window(window_id, confirmed, window_end))
        last = window_end >= end_ms
        if not sentences:
            confirmed = window_end
        else:
            kept = sentences if last or not timed or len(sentences) == 1 else sentences[:-1]
            result.sentences.extend(kept)
            kept_text = join_sentences([kept_text, *(item.text for item in kept)])
            confirmed = window_end if last else kept[-1].end_ms
            if confirmed <= result.windows[-1].start_ms:
                confirmed = window_end
        if progress:
            await progress(min(confirmed, end_ms) - start_ms)
    return result


async def mend_seams(hear: Hear, heard: list[Stretch]) -> Stretch:
    """Join the stretches, hearing each seam again (§2.4).

    The last sentence before a seam and the first after it are heard in one request, so the
    model alone decides whether the words around the cut are one sentence or two; no pause
    decides anything. The answer replaces the two. An empty answer keeps them; a seam
    longer than the listen-again limit is left alone.
    """

    merged = Stretch()
    for stretch in heard:
        merged.windows.extend(stretch.windows)
        incoming = list(stretch.sentences)
        if merged.sentences and incoming:
            before_seam, after_seam = merged.sentences[-1], incoming[0]
            start, end = before_seam.start_ms, after_seam.end_ms
            if 0 < end - start <= MAX_SEAM_MS:
                context = join_sentences([item.text for item in merged.sentences[:-1]])
                answer = await hear(start, end, _tail(context))
                taken = {window.id for window in merged.windows}
                window_id = f"w-{start}"
                while window_id in taken:
                    window_id += "+"
                fresh, timed = _timed(answer, start, end, window_id)
                if timed and fresh:
                    merged.windows.append(Window(window_id, start, end))
                    merged.sentences[-1:] = fresh
                    incoming = incoming[1:]
        merged.sentences.extend(incoming)
    merged.windows.sort(key=lambda window: (window.start_ms, window.end_ms))
    return merged


def text_windows(
    pieces: list[tuple[int, int]], min_ms: int = TEXT_WINDOW_MS
) -> list[tuple[int, int]]:
    """Windows for providers that answer text only (§2.6).

    ``pieces`` are the stretches of speech the pause detector found (ms). A window starts
    at its first piece and ends at a piece end once it is at least ``min_ms`` long, so it
    never ends inside a phrase. The last window may be shorter. A window never runs past
    the listen-again limit, so a long silence between two pieces ends the window before it.
    """

    windows: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for piece_start, piece_end in sorted(pieces):
        if piece_end <= piece_start:
            continue
        if start is not None and piece_end - start > MAX_TEXT_WINDOW_MS:
            windows.append((start, end))
            start = None
        if start is None:
            start = piece_start
            end = 0
        end = max(end, piece_end)
        if end - start >= min_ms:
            windows.append((start, end))
            start = None
    if start is not None:
        windows.append((start, end))
    return windows


async def hear_text_run(
    hear: Hear,
    windows: list[tuple[int, int]],
    context: str = "",
    progress: Callable[[int], Awaitable[None]] | None = None,
) -> Stretch:
    """Windows that were cut at pauses, heard one after another: each is one sentence."""

    result = Stretch()
    kept_text = context
    for start, end in windows:
        window_id = f"w-{start}"
        answer = await hear(start, end, _tail(kept_text))
        text = str(answer.get("text") or "").strip()
        result.windows.append(Window(window_id, start, end))
        if text:
            result.sentences.append(Heard(start, end, text, window_id))
            kept_text = join_sentences([kept_text, text])
        if progress:
            await progress(end - windows[0][0])
    return result


# Paragraphs ---------------------------------------------------------------------------

Group = Callable[[list[str], str], list[int]]


def _clean_starts(starts: object, count: int) -> list[int]:
    """Zero-based starts: whole numbers inside the range, sorted, no repeats, and 0."""

    picked = {0}
    if isinstance(starts, list):
        picked |= {
            value
            for value in starts
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count
        }
    return sorted(picked)


def paragraphs_by_window(sentences: list[Heard]) -> list[list[int]]:
    """One paragraph per run of sentences from the same window: where grouping starts."""

    paragraphs: list[list[int]] = []
    for index, sentence in enumerate(sentences):
        if paragraphs and sentences[paragraphs[-1][-1]].window == sentence.window:
            paragraphs[-1].append(index)
        else:
            paragraphs.append([index])
    return paragraphs


def regroup(
    sentences: list[Heard],
    group: Group,
    maximum: int = GROUPING_MAX,
) -> list[list[int]]:
    """Paragraphs as the Analysis model groups them, in chunks of up to ``maximum`` sentences.

    Starts from one paragraph per window and goes through the recording the way the
    desktop's ``nextRegroupChunk`` / ``applyRegroup`` do after Stop: each chunk begins at a
    paragraph start, holds whole paragraphs, and is split again where ``group`` says; the
    text before the chunk is sent as context. ``group(texts, before)`` returns zero-based
    paragraph starts. Paragraphs are lists of indexes into ``sentences``.
    """

    paragraphs = paragraphs_by_window(sentences)
    from_ms = 0
    while True:
        chunk = _next_chunk(sentences, paragraphs, from_ms, maximum)
        if chunk is None:
            return paragraphs
        first, count, indexes = chunk
        previous = (
            join_sentences([sentences[i].text for i in paragraphs[first - 1]]) if first else ""
        )
        starts = _clean_starts(
            group([sentences[i].text for i in indexes], _tail(previous)), len(indexes)
        )
        made = [
            indexes[start : starts[number + 1] if number + 1 < len(starts) else len(indexes)]
            for number, start in enumerate(starts)
        ]
        paragraphs[first : first + count] = made
        from_ms = sentences[indexes[-1]].start_ms + 1


def _next_chunk(
    sentences: list[Heard], paragraphs: list[list[int]], from_ms: int, maximum: int
) -> tuple[int, int, list[int]] | None:
    """``(first paragraph, paragraph count, sentence indexes)`` of the next run to group."""

    index = 0
    while index < len(paragraphs):
        own = paragraphs[index]
        if not own or sentences[own[0]].start_ms < from_ms or len(own) > maximum:
            index += 1
            continue
        end, count = index, 0
        while end < len(paragraphs) and count + len(paragraphs[end]) <= maximum:
            count += len(paragraphs[end])
            end += 1
        run = [i for paragraph in paragraphs[index:end] for i in paragraph]
        if len(run) < 2:
            index = max(end, index + 1)
            continue
        return index, end - index, run
    return None


# The structured transcript ---------------------------------------------------------------


def _unique(base: str, taken: set[str]) -> str:
    name = base
    while name in taken:
        name += "+"
    taken.add(name)
    return name


def transcript_state(
    sentences: list[Heard],
    windows: list[Window],
    paragraphs: list[list[int]],
    old: list[tuple[int, int, str]],
    *,
    model: str,
    at: str,
    grouped: bool,
) -> dict[str, Any]:
    """The ``TranscriptState`` JSON (version 1) the desktop reads (§4.4).

    Every window is ``checked``; its ``live`` is the old lines whose stamp falls in it.
    Every paragraph is frozen. ``grouped`` False means no Analysis model grouped them.
    """

    taken: set[str] = set()
    ids = [_unique(f"s-{sentence.start_ms}", taken) for sentence in sentences]
    taken = set()
    state_paragraphs = [
        {
            "id": _unique(f"p-{sentences[indexes[0]].start_ms}", taken),
            "sentenceIds": [ids[i] for i in indexes],
            "frozen": True,
        }
        for indexes in paragraphs
        if indexes
    ]
    return {
        "version": 1,
        "sentences": [
            {
                "id": ids[index],
                "startMs": sentence.start_ms,
                "endMs": sentence.end_ms,
                "text": sentence.text,
                "windowId": sentence.window,
            }
            for index, sentence in enumerate(sentences)
        ],
        "windows": [
            {
                "id": window.id,
                "startMs": window.start_ms,
                "endMs": window.end_ms,
                "live": [
                    {"id": f"old-{start}", "startMs": start, "endMs": end, "text": text}
                    for start, end, text in old
                    if window.start_ms <= start < window.end_ms
                ],
                "state": "checked",
                "pass": "minute",
                "model": model,
                "at": at,
            }
            for window in windows
        ],
        "paragraphs": state_paragraphs,
        "live": [],
        "draft": None,
        "confirmedUntilMs": max((sentence.end_ms for sentence in sentences), default=0),
        "checking": None,
        "grouping": {"state": "idle" if grouped else "off"},
        "gaps": [],
    }


def render(state: dict[str, Any]) -> str:
    """``[clock] paragraph`` blocks with a blank line between, like the desktop renderTranscript."""

    by_id = {sentence["id"]: sentence for sentence in state["sentences"]}
    blocks: list[str] = []
    for paragraph in state["paragraphs"]:
        members = [by_id[i] for i in paragraph["sentenceIds"] if i in by_id]
        text = re.sub(r"\s+", " ", join_sentences([m["text"] for m in members])).strip()
        if text:
            blocks.append(f"[{clock_label(members[0]['startMs'])}] {text}")
    return "\n\n".join(blocks)


# Pauses in the audio ----------------------------------------------------------------------


def speech_pieces(directory: Path, recording_id: str, total_ms: int) -> list[tuple[int, int]]:
    """Stretches of speech (ms) in the listen copy, cut at pauses by the pause detector.

    Without the detector's model, fixed pieces of a minute (the live fallback).
    """

    detector = vad.new_detector()
    segmenter: vad.Segmenter | vad.FixedBlocks
    segmenter = (
        vad.Segmenter(detector)
        if detector is not None
        else vad.FixedBlocks(TEXT_WINDOW_MS * BYTES_PER_MS)
    )
    pieces: list[vad.Piece] = []
    step = 1_000 * BYTES_PER_MS
    pcm_path, _ = listen_copy.copy_paths(directory, recording_id)
    with open(pcm_path, "rb") as handle:
        while block := handle.read(step):
            pieces.extend(segmenter.feed(block))
    pieces.extend(segmenter.flush())
    return [(start // BYTES_PER_MS, min(end // BYTES_PER_MS, total_ms)) for start, end in pieces]


# The runner ----------------------------------------------------------------------------------


class UpgradeFailed(Exception):
    """An upgrade could not be finished; the message is for the user."""


class UnknownRecording(LookupError):
    """No recording has this id."""


class NotFailed(ValueError):
    """The recording's upgrade has not failed, so there is nothing to retry."""


@dataclass
class Candidate:
    recording_id: str
    created_at: datetime
    source_ids: list[str]


def _iso(moment: datetime) -> str:
    return moment.replace(tzinfo=UTC).isoformat(timespec="seconds")


async def with_retries(call: Callable[[], Awaitable[Any]], waits: tuple[float, ...]) -> Any:
    """Try ``call`` after each wait in turn; the last ``ListenError`` is raised."""

    last: Exception | None = None
    for wait in waits:
        if wait:
            await asyncio.sleep(wait)
        try:
            return await call()
        except ListenError as error:
            last = error
    assert last is not None
    raise last


class TranscriptUpgrader:
    """Upgrades old recordings one at a time in the background (§2).

    ``state`` is the application's ``app.state``: the speech registry, the models and the
    knowledge index are read from it each time, because settings replace them live.
    """

    def __init__(
        self,
        state: Any,
        sessions: sessionmaker[Session],
        recordings_dir: Path,
    ) -> None:
        self.state = state
        self.sessions = sessions
        self.recordings_dir = recordings_dir
        self.retry_waits = RETRY_WAITS
        self.first_delay = FIRST_DELAY_SECONDS
        self.poll = POLL_SECONDS
        self.current: str | None = None
        self._front: list[str] = []
        # Answers from the models in the current attempt: none means they could not be reached.
        self._answers = 0
        self._paused_until: datetime | None = None
        self._pause_level = 0
        # Failed because no model answered; due again as soon as the wait ends.
        self._unanswered: set[str] = set()
        self._resume = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    # Waiting -------------------------------------------------------------------------

    def wake(self) -> None:
        """Look for work now instead of at the next poll, ending a wait for the models.

        Called when settings are saved and on Retry. Safe from any thread.
        """

        self._resume = True
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    async def _sleep(self, seconds: float) -> None:
        assert self._wake is not None
        with suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        self._wake.clear()

    async def run(self) -> None:
        """The loop started with the app: wait a little, then upgrade one after another."""

        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        # Start-up has its own work to do first; only the waits after this one can be cut short.
        await asyncio.sleep(self.first_delay)
        self._wake.clear()
        while True:
            worked = False
            try:
                worked = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("The transcript upgrader hit a problem", exc_info=True)
            if not worked:
                await self._sleep(self.poll)

    async def step(self) -> bool:
        """Upgrade the next waiting recording, if nothing holds it back. Returns whether one ran."""

        if self._resume:
            self._resume = False
            self._paused_until, self._pause_level = None, 0
            unanswered, self._unanswered = list(self._unanswered), set()
            if unanswered:
                await asyncio.to_thread(self._due_now, unanswered)
        if await asyncio.to_thread(self.holding_back) is not None:
            return False
        found = await asyncio.to_thread(self._eligible)
        if not found:
            return False
        await self.upgrade_one(found[0].recording_id)
        return True

    # What is waiting -------------------------------------------------------------------

    def holding_back(self) -> str | None:
        """Why nothing can start: ``model`` (no Recording model), ``service`` (the models did
        not answer lately) or ``recording`` (one is live)."""

        choice, _problem = speech_registry.resolve(self.state.speech_registry, "recording")
        if choice is None:
            return "model"
        if self._paused_until is not None and utc_now() < self._paused_until:
            return "service"
        with session_scope(self.sessions) as session:
            if self._capturing(session):
                return "recording"
        return None

    @staticmethod
    def _capturing(session: Session) -> bool:
        newest = session.scalar(
            select(RecordingSession.updated_at)
            .where(RecordingSession.status == "capturing")
            .order_by(RecordingSession.updated_at.desc())
            .limit(1)
        )
        return newest is not None and newest > utc_now() - LIVE_RECENT

    def old_recordings(self, session: Session) -> list[Candidate]:
        """Every recording that is old (see the plan), newest first, whatever its upgrade row says.

        Completed, no structured transcript, audio on disk and not empty, and at least one
        source in the library (not in Trash) whose transcript was made by the old recorder.
        """

        # Only recordings without a structured transcript can be old, and only their sources'
        # text is read; the rest are matched on the reference alone (near the top).
        unstructured = set(
            session.scalars(
                select(RecordingSession.id).where(
                    RecordingSession.status == "completed",
                    RecordingSession.transcript_segments_json.is_(None),
                )
            )
        )
        if not unstructured:
            return []
        marker = "Local recording: "
        reference = func.substr(
            Source.content, func.instr(Source.content, marker) + len(marker), 28
        )
        wanted = [
            source_id
            for source_id, recording_id in session.execute(
                select(Source.id, reference).where(
                    Source.trashed_at.is_(None), Source.content.contains(marker + "rec_")
                )
            )
            if recording_id in unstructured
        ]
        sources: dict[str, list[str]] = {}
        for source in session.scalars(
            select(Source).where(Source.id.in_(wanted)).order_by(Source.created_at, Source.id)
        ):
            match = RECORDING_REFERENCE.search(source.content)
            section = transcript_section(source.content)
            if match and section and is_old_transcript(section[2]):
                sources.setdefault(match.group(1), []).append(source.id)
        found: list[Candidate] = []
        for recording in session.scalars(
            select(RecordingSession)
            .where(
                RecordingSession.status == "completed",
                RecordingSession.transcript_segments_json.is_(None),
                RecordingSession.id.in_(list(sources)),
            )
            .order_by(RecordingSession.created_at.desc(), RecordingSession.id.desc())
        ):
            try:
                size = (self.recordings_dir / recording.file_name).stat().st_size
            except OSError:
                continue
            if size > 0:
                found.append(Candidate(recording.id, recording.created_at, sources[recording.id]))
        return found

    def candidates(self, session: Session) -> list[Candidate]:
        """The old recordings that may be started now, in the order they will be.

        Skips ``done`` ones, failed ones before their ``retry_at`` (or waiting for Retry),
        and the one running now. A row left ``running`` by a crash counts as not started.
        A recording the user asked to retry goes first.
        """

        now = utc_now()
        rows = {row.recording_id: row for row in session.scalars(select(TranscriptUpgrade))}
        eligible: list[Candidate] = []
        for candidate in self.old_recordings(session):
            row = rows.get(candidate.recording_id)
            if candidate.recording_id == self.current:
                continue
            if row is not None and row.state == "done":
                continue
            if (
                row is not None
                and row.state == "failed"
                and (row.retry_at is None or row.retry_at > now)
            ):
                continue
            eligible.append(candidate)
        eligible.sort(key=lambda item: item.recording_id not in self._front)
        return eligible

    def _eligible(self) -> list[Candidate]:
        with session_scope(self.sessions) as session:
            return self.candidates(session)

    # Status and Retry ------------------------------------------------------------------

    def status(self, recording_id: str) -> dict[str, Any]:
        """What the grey line says (§4.2)."""

        def out(state: str, **more: Any) -> dict[str, Any]:
            return {
                "state": state,
                "reason": more.get("reason"),
                "ahead": more.get("ahead", 0),
                "heardMs": more.get("heardMs", 0),
                "totalMs": more.get("totalMs", 0),
                "error": more.get("error"),
            }

        with session_scope(self.sessions) as session:
            row = session.get(TranscriptUpgrade, recording_id)
            if row is not None and row.state == "running" and self.current == recording_id:
                return out("running", heardMs=row.heard_ms, totalMs=row.total_ms)
            # Retry puts it first in line: it is waiting again, not still failed.
            if row is not None and row.state == "failed" and recording_id not in self._front:
                return out("failed", error=row.error or "The upgrade failed.", totalMs=row.total_ms)
            if row is not None and row.state == "done":
                return out("done", heardMs=row.total_ms, totalMs=row.total_ms)
            if not any(item.recording_id == recording_id for item in self.old_recordings(session)):
                return out("none")
            ids = [item.recording_id for item in self.candidates(session)]
            ahead = ids.index(recording_id) if recording_id in ids else 0
            ahead += 1 if self.current not in (None, recording_id) else 0
        reason = self.holding_back()
        if reason is None and ahead > 0:
            reason = "queue"
        return out("waiting", reason=reason, ahead=ahead)

    def retry(self, recording_id: str) -> None:
        """Try a failed recording next. Raises ``UnknownRecording`` or ``NotFailed``."""

        with session_scope(self.sessions) as session:
            if session.get(RecordingSession, recording_id) is None:
                raise UnknownRecording(recording_id)
            row = session.get(TranscriptUpgrade, recording_id)
            if row is None or row.state != "failed":
                raise NotFailed(recording_id)
            row.attempts = 0
            row.retry_at = utc_now()
            row.updated_at = utc_now()
        if recording_id not in self._front:
            self._front.append(recording_id)
        self.wake()

    # One recording -----------------------------------------------------------------------

    async def upgrade_one(self, recording_id: str) -> bool:
        """Hear one recording again, group it, and write it. Returns whether it was written.

        Nothing is written to the transcript or the sources unless everything was heard and
        grouped. A failure is kept in the upgrade row (with what was heard, so the next
        attempt only groups again) and the queue moves on.
        """

        self.current = recording_id
        self._answers = 0
        try:
            plan = await asyncio.to_thread(self._begin, recording_id)
            if plan is None:
                return False
            try:
                written = await self._upgrade(plan)
            except asyncio.CancelledError:
                raise
            except (ListenError, ParagraphsError) as error:
                # No answer at all means the models can't be reached now, not that this
                # recording is at fault: wait before trying anything, and don't count it.
                unanswered = self._answers == 0
                if unanswered:
                    self._pause()
                await asyncio.to_thread(self._fail, recording_id, str(error), unanswered)
                return False
            except (UpgradeFailed, AudioUnreadable) as error:
                await asyncio.to_thread(self._fail, recording_id, str(error))
                return False
            except Exception as error:
                logger.warning("Upgrading %s failed", recording_id, exc_info=True)
                await asyncio.to_thread(self._fail, recording_id, f"Unexpected problem: {error}")
                return False
            if written:
                self._pause_level = 0
            return written
        finally:
            self.current = None
            self._front = [item for item in self._front if item != recording_id]
            await asyncio.to_thread(listen_copy.remove_copy, self.recordings_dir, recording_id)

    def _begin(self, recording_id: str) -> dict[str, Any] | None:
        """Read what the upgrade needs and mark the row running (one short transaction)."""

        choice, _problem = speech_registry.resolve(self.state.speech_registry, "recording")
        if choice is None:
            return None
        with session_scope(self.sessions) as session:
            wanted = next(
                (
                    item
                    for item in self.old_recordings(session)
                    if item.recording_id == recording_id
                ),
                None,
            )
            recording = session.get(RecordingSession, recording_id)
            if wanted is None or recording is None:
                return None
            first = session.get(Source, wanted.source_ids[0])
            assert first is not None
            head, _heading, body = transcript_section(first.content) or ("", "", "")
            row = session.get(TranscriptUpgrade, recording_id)
            now = utc_now()
            if row is None:
                row = TranscriptUpgrade(recording_id=recording_id, started_at=now)
                session.add(row)
            row.state = "running"
            row.error = None
            row.retry_at = None
            row.heard_ms = 0
            row.old_transcript = body
            row.model = choice.model
            row.started_at = now
            row.finished_at = None
            row.updated_at = now
            saved = json.loads(row.sentences_json) if row.sentences_json else None
            return {
                "recording_id": recording_id,
                "path": self.recordings_dir / recording.file_name,
                "choice": choice,
                "terms": recording_terms(head),
                "body": body,
                "saved": saved,
            }

    def _progress(self, recording_id: str, heard_ms: int, total_ms: int | None = None) -> None:
        with session_scope(self.sessions) as session:
            row = session.get(TranscriptUpgrade, recording_id)
            if row is not None and row.state == "running":
                row.heard_ms = heard_ms
                if total_ms is not None:
                    row.total_ms = total_ms
                row.updated_at = utc_now()

    def _pause(self) -> None:
        """Try nothing for a while: the models did not answer (``PAUSE_AFTER``)."""

        wait = PAUSE_AFTER[min(self._pause_level, len(PAUSE_AFTER) - 1)]
        self._pause_level += 1
        self._paused_until = utc_now() + wait

    def _fail(self, recording_id: str, message: str, unanswered: bool = False) -> None:
        with session_scope(self.sessions) as session:
            row = session.get(TranscriptUpgrade, recording_id)
            if row is None:
                return
            now = utc_now()
            row.state = "failed"
            row.error = message
            row.finished_at = now
            row.updated_at = now
            if unanswered:
                # Not this recording's fault: it goes again when the wait ends.
                self._unanswered.add(recording_id)
                row.retry_at = self._paused_until or now
                return
            row.attempts += 1
            index = row.attempts - 1
            row.retry_at = now + AUTO_RETRY_AFTER[index] if index < len(AUTO_RETRY_AFTER) else None

    def _due_now(self, recording_ids: list[str]) -> None:
        """Failed ones that were only waiting for the models may go again now."""

        with session_scope(self.sessions) as session:
            for recording_id in recording_ids:
                row = session.get(TranscriptUpgrade, recording_id)
                if row is not None and row.state == "failed":
                    row.retry_at = utc_now()

    async def _upgrade(self, plan: dict[str, Any]) -> bool:
        recording_id = plan["recording_id"]
        saved = plan["saved"]
        if saved:
            total_ms = int(saved["totalMs"])
            sentences = [Heard(**item) for item in saved["sentences"]]
            windows = [Window(**item) for item in saved["windows"]]
        else:
            sentences, windows, total_ms = await self._listen(plan)
            await asyncio.to_thread(self._save_heard, recording_id, sentences, windows, total_ms)

        # Paragraphs. The Analysis model is read now: settings may have changed.
        chosen = self.state.models.for_role("analysis")
        if chosen is None:
            paragraphs = paragraphs_by_window(sentences)
        else:
            model, effort = chosen

            def group(texts: list[str], before: str) -> list[int]:
                return self._group_with_retries(model, effort, texts, before)

            paragraphs = await asyncio.to_thread(regroup, sentences, group)
        old = old_lines(plan["body"], total_ms)
        state = transcript_state(
            sentences,
            windows,
            paragraphs,
            old,
            model=plan["choice"].model,
            at=_iso(utc_now()),
            grouped=chosen is not None,
        )
        return await asyncio.to_thread(self._write, plan, state)

    def _group_with_retries(
        self, model: Any, effort: Any, texts: list[str], before: str
    ) -> list[int]:
        last: ParagraphsError | None = None
        for wait in self.retry_waits:
            if wait:
                time.sleep(wait)
            try:
                starts = group_paragraphs(self.state.models, model, effort, texts, before)
                self._answers += 1
                return starts
            except ParagraphsError as error:
                last = error
        assert last is not None
        raise last

    async def _listen(self, plan: dict[str, Any]) -> tuple[list[Heard], list[Window], int]:
        """Decode the audio and hear all of it again."""

        recording_id, choice = plan["recording_id"], plan["choice"]
        total_ms = await asyncio.to_thread(
            decode_to_copy, plan["path"], self.recordings_dir, recording_id
        )
        await asyncio.to_thread(self._progress, recording_id, 0, total_ms)
        old = old_lines(plan["body"], total_ms)

        async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
            end_ms = min(end_ms, total_ms)
            padded = max(0, start_ms - RELISTEN_PAD_MS)
            pcm, _held = await asyncio.to_thread(
                listen_copy.read_range,
                self.recordings_dir,
                recording_id,
                padded,
                end_ms + RELISTEN_PAD_MS,
            )

            async def once() -> dict[str, Any]:
                answer = await relisten_audio(
                    choice,
                    pcm,
                    audio_start_ms=padded,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    terms=plan["terms"],
                    before=before,
                    after="",
                    timeout=HEAR_TIMEOUT,
                )
                self._answers += 1
                return answer

            return await with_retries(once, tuple(self.retry_waits))

        def old_context(start_ms: int) -> str:
            return join_sentences(
                [text for start, _end, text in old if start_ms - OLD_CONTEXT_MS <= start < start_ms]
            )

        done: dict[int, int] = {}
        lock = asyncio.Lock()

        def reporter(key: int) -> Callable[[int], Awaitable[None]]:
            async def report(heard_ms: int) -> None:
                async with lock:
                    done[key] = heard_ms
                    await asyncio.to_thread(self._progress, recording_id, sum(done.values()))

            return report

        gate = asyncio.Semaphore(PARALLEL_STRETCHES)

        async def limited(key: int, work: Awaitable[Stretch]) -> Stretch:
            async with gate:
                return await work

        if choice.kind == "qwen":
            spans = stretches(total_ms)
            jobs = [
                asyncio.ensure_future(
                    limited(
                        number,
                        hear_stretch(hear, start, end, old_context(start), reporter(number)),
                    )
                )
                for number, (start, end) in enumerate(spans)
            ]
        else:
            pieces = await asyncio.to_thread(
                speech_pieces, self.recordings_dir, recording_id, total_ms
            )
            runs: list[list[tuple[int, int]]] = []
            for window in text_windows(pieces):
                if runs and window[0] - runs[-1][0][0] < STRETCH_MS:
                    runs[-1].append(window)
                else:
                    runs.append([window])
            jobs = [
                asyncio.ensure_future(
                    limited(
                        number,
                        hear_text_run(hear, run, old_context(run[0][0]), reporter(number)),
                    )
                )
                for number, run in enumerate(runs)
            ]
        try:
            heard = list(await asyncio.gather(*jobs))
        except BaseException:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            raise
        merged = await mend_seams(hear, heard) if choice.kind == "qwen" else _joined(heard)
        if not merged.sentences:
            raise UpgradeFailed("Nothing was heard in the audio.")
        merged.sentences.sort(key=lambda item: (item.start_ms, item.end_ms))
        return merged.sentences, merged.windows, total_ms

    def _save_heard(
        self, recording_id: str, sentences: list[Heard], windows: list[Window], total_ms: int
    ) -> None:
        """Keep what was heard, so a failed grouping can be tried again without listening."""

        with session_scope(self.sessions) as session:
            row = session.get(TranscriptUpgrade, recording_id)
            if row is None:
                return
            row.sentences_json = json.dumps(
                {
                    "totalMs": total_ms,
                    "sentences": [vars(item) for item in sentences],
                    "windows": [vars(item) for item in windows],
                },
                ensure_ascii=False,
            )
            row.heard_ms = row.total_ms = total_ms
            row.updated_at = utc_now()

    def _write(self, plan: dict[str, Any], state: dict[str, Any]) -> bool:
        """Everything at once, in one short transaction (§4.4)."""

        recording_id = plan["recording_id"]
        text = render(state)
        with session_scope(self.sessions) as session:
            wanted = next(
                (
                    item
                    for item in self.old_recordings(session)
                    if item.recording_id == recording_id
                ),
                None,
            )
            recording = session.get(RecordingSession, recording_id)
            row = session.get(TranscriptUpgrade, recording_id)
            if wanted is None or recording is None or row is None:
                # Trashed or replaced while it was being heard: nothing to improve any more.
                if row is not None:
                    session.delete(row)
                return False
            index: KnowledgeIndex = self.state.knowledge_service.index
            for source_id in wanted.source_ids:
                source = session.get(Source, source_id)
                section = transcript_section(source.content) if source else None
                if source is None or section is None:
                    continue
                head, heading, _body = section
                source.content = f"{head}{heading}\n\n{text}"
                source.content_hash = source_fingerprint(source.title, source.kind, source.content)
                session.flush()
                index.add_source(session, source)
            recording.transcript = text
            recording.transcript_segments_json = json.dumps(state, ensure_ascii=False)
            now = utc_now()
            row.state = "done"
            row.sentences_json = None
            row.error = None
            row.attempts = 0
            row.retry_at = None
            row.finished_at = now
            row.updated_at = now
        folders = getattr(self.state, "library_folders", None)
        if folders is not None:
            folders.request_sync()
        return True


def _joined(heard: list[Stretch]) -> Stretch:
    """Stretches put one after another with no seams to repair."""

    joined = Stretch()
    for stretch in heard:
        joined.sentences.extend(stretch.sentences)
        joined.windows.extend(stretch.windows)
    return joined
