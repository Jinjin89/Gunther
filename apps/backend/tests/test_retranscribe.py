"""A recording transcribed again from its saved audio, when the user presses the button.

The pure parts (windows, seams, paragraphs, the structured transcript) are tested with a
made-up ``hear``. The button goes through the real app with the Recording model replaced
by a function that answers from a timeline, and the Analysis model by a fake gateway.
"""

import asyncio
import dataclasses
import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther import retranscribe
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import (
    ContentBlock,
    RecordingSession,
    Source,
    SourceIndexHead,
    SourceRevision,
    utc_now,
)
from gunther.realtime import ListenError
from gunther.retranscribe import (
    Heard,
    Stretch,
    Window,
    clock_label,
    hear_stretch,
    hear_text_run,
    join_sentences,
    mend_seams,
    paragraphs_by_window,
    regroup,
    render,
    stretches,
    text_windows,
    transcript_section,
    transcript_state,
)
from tests.test_audio_decode import encode
from tests.test_listen_again import SIDECAR, settings_for, with_provider

REC = "rec_" + "a" * 24
OTHER = "rec_" + "b" * 24


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# The transcript in a source ----------------------------------------------------------------


CONTENT_HEAD = (
    "# Lecture 3\n\n"
    "Duration: 10:00 · Captured: Today · Local recording: " + REC + "\n\n"
    "## Summary\nGradient descent.\nTerms: gradient · loss function\n\n"
    "## Marked moments\n- 0:05 · The key step\n\n"
)


def test_the_transcript_section_is_split_off_and_everything_before_it_is_kept() -> None:
    content = CONTENT_HEAD + "## Transcript\n\n[00:00] one\n[00:03] two\n"
    head, heading, body = transcript_section(content)
    assert head == CONTENT_HEAD
    assert heading == "## Transcript"
    assert body == "[00:00] one\n[00:03] two"
    assert head + heading + "\n\n" + body + "\n" == content
    full = transcript_section("# T\n\n## Full transcript\n\n[00:00] x")
    assert full is not None and full[1] == "## Full transcript"
    assert transcript_section("# T\n\nNo transcript here.") is None
    # The first heading wins; a later one belongs to the transcript.
    nested = transcript_section("## Transcript\n[00:00] a\n## Transcript\n[00:01] b")
    assert nested is not None and nested[2].endswith("[00:01] b")


# Text ------------------------------------------------------------------------------------


def test_sentences_join_and_clocks_read_like_the_desktops() -> None:
    # The same examples as liveTranscript.test.ts.
    assert join_sentences(["今天讲梯度下降", "然后看学习率"]) == "今天讲梯度下降然后看学习率"
    assert (
        join_sentences(["好，我们开始。", "Today we use SGD."]) == "好，我们开始。Today we use SGD."
    )
    assert join_sentences(["We use Adam.", "Then we go on."]) == "We use Adam. Then we go on."
    assert join_sentences(["We use Adam", "然后继续"]) == "We use Adam 然后继续"
    assert join_sentences([" a ", "", "b"]) == "a b"
    assert join_sentences(["こんにちは", "カタカナ"]) == "こんにちはカタカナ"
    assert join_sentences(["wait…", "ok"]) == "wait…ok"
    assert clock_label(0) == "00:00"
    assert clock_label(65_999) == "01:05"
    assert clock_label(3_599_000) == "59:59"
    assert clock_label(3_725_000) == "01:02:05"
    assert clock_label(-5) == "00:00"


# The window chain --------------------------------------------------------------------


def timeline(count: int, offset: int = 5_000) -> list[tuple[int, int, str]]:
    return [
        (i * 10_000 + offset, i * 10_000 + offset + 9_000, f"Sentence {i}.") for i in range(count)
    ]


class Recorder:
    """A made-up Recording model: it hears a timeline, cutting what the range cuts."""

    def __init__(self, sentences: list[tuple[int, int, str]], word_times: bool = True) -> None:
        self.sentences = sentences
        self.word_times = word_times
        self.calls: list[tuple[int, int, str]] = []

    async def __call__(self, start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        self.calls.append((start_ms, end_ms, before))
        heard = []
        for begin, end, text in self.sentences:
            if end <= start_ms or begin >= end_ms:
                continue
            cut = begin < start_ms or end > end_ms
            heard.append(
                {
                    "start_ms": max(begin, start_ms),
                    "end_ms": min(end, end_ms),
                    "text": f"{text} (partial)" if cut else text,
                }
            )
        text = " ".join(item["text"] for item in heard)
        return {"text": text, "sentences": heard if self.word_times else []}


def test_each_window_holds_back_its_last_sentence_and_the_next_one_hears_it_again() -> None:
    recorder = Recorder(timeline(29))
    seen: list[int] = []

    async def progress(ms: int) -> None:
        seen.append(ms)

    stretch = run(hear_stretch(recorder, 0, 300_000, "Earlier talk.", progress))
    assert [item.text for item in stretch.sentences] == [f"Sentence {i}." for i in range(29)]
    # Windows of two minutes, each starting where the sentences kept before it end.
    assert recorder.calls[0][:2] == (0, 120_000)
    assert 100_000 < recorder.calls[1][0] < 120_000  # where the sentences kept so far end
    assert recorder.calls[-1][1] == 300_000
    assert all(end - start <= 180_000 for start, end, _ in recorder.calls)
    # Context: the old text at the start, then what was confirmed in this chain.
    assert recorder.calls[0][2] == "Earlier talk."
    assert recorder.calls[1][2].startswith("Earlier talk. Sentence 0. Sentence 1.")
    assert all(len(before) <= 400 for _, _, before in recorder.calls)
    assert seen == sorted(seen) and seen[-1] == 300_000
    assert [window.start_ms for window in stretch.windows][0] == 0
    assert {item.window for item in stretch.sentences} <= {w.id for w in stretch.windows}


def test_a_window_near_the_stretchs_end_runs_to_it() -> None:
    recorder = Recorder([])
    run(hear_stretch(recorder, 0, 140_000))
    assert [call[:2] for call in recorder.calls] == [(0, 140_000)]


def test_silence_moves_on_and_a_window_with_text_only_is_one_sentence() -> None:
    recorder = Recorder([])
    stretch = run(hear_stretch(recorder, 0, 300_000))
    assert stretch.sentences == []
    assert [call[:2] for call in recorder.calls] == [
        (0, 120_000),
        (120_000, 240_000),
        (240_000, 300_000),
    ]

    text_only = Recorder(timeline(30), word_times=False)
    stretch = run(hear_stretch(text_only, 0, 300_000))
    assert [(item.start_ms, item.end_ms) for item in stretch.sentences] == [
        (0, 120_000),
        (120_000, 240_000),
        (240_000, 300_000),
    ]


def test_a_one_sentence_answer_is_asked_again_with_a_longer_window_then_kept() -> None:
    answers = [
        [(0, 100_000, "A long sentence that fills the window.")],
        [(0, 100_000, "A long sentence that fills the window.")],
    ]
    calls: list[tuple[int, int]] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        calls.append((start_ms, end_ms))
        index = len(calls) - 1
        items = answers[index] if index < len(answers) else []
        return {
            "text": " ".join(text for _, _, text in items),
            "sentences": [{"start_ms": a, "end_ms": b, "text": t} for a, b, t in items],
        }

    stretch = run(hear_stretch(hear, 0, 600_000))
    assert calls[:3] == [(0, 120_000), (0, 180_000), (100_000, 220_000)]
    assert [item.text for item in stretch.sentences] == ["A long sentence that fills the window."]


def test_the_last_window_of_a_stretch_keeps_everything() -> None:
    recorder = Recorder(timeline(5))
    stretch = run(hear_stretch(recorder, 0, 60_000))
    assert len(recorder.calls) == 1
    assert [item.text for item in stretch.sentences] == [f"Sentence {i}." for i in range(5)]


# Seams -----------------------------------------------------------------------------------


def stretch_of(*sentences: tuple[int, int, str], window: str) -> Stretch:
    return Stretch(
        [Heard(a, b, text, window) for a, b, text in sentences],
        [Window(window, sentences[0][0], sentences[-1][1])],
    )


def test_a_seam_is_heard_as_one_request_and_replaces_the_two_edge_sentences() -> None:
    first = stretch_of((0, 5_000, "One."), (595_000, 600_000, "So the loss jumps"), window="w-0")
    second = stretch_of(
        (600_000, 603_000, "around and we lower the rate."),
        (604_000, 606_000, "Next."),
        window="w-600000",
    )
    calls: list[tuple[int, int, str]] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        calls.append((start_ms, end_ms, before))
        sentence = {
            "start_ms": 595_000,
            "end_ms": 603_000,
            "text": "So the loss jumps around and we lower the rate.",
        }
        return {"text": sentence["text"], "sentences": [sentence]}

    merged = run(mend_seams(hear, [first, second]))
    assert calls == [(595_000, 603_000, "One.")]
    assert [item.text for item in merged.sentences] == [
        "One.",
        "So the loss jumps around and we lower the rate.",
        "Next.",
    ]
    assert len(merged.windows) == 3  # two stretches' windows and the seam
    assert merged.sentences[1].window in {window.id for window in merged.windows}


def test_an_empty_seam_answer_keeps_both_and_a_long_seam_is_not_asked() -> None:
    first = stretch_of((0, 5_000, "One."), (595_000, 600_000, "So it"), window="w-0")
    second = stretch_of((600_000, 603_000, "goes on."), window="w-600000")

    async def silent(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        return {"text": "", "sentences": []}

    kept = run(mend_seams(silent, [first, second]))
    assert [item.text for item in kept.sentences] == ["One.", "So it", "goes on."]

    far = stretch_of((0, 5_000, "One."), (400_000, 600_000, "A very long one"), window="w-0")
    asked: list[Any] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        asked.append((start_ms, end_ms))
        return {"text": "x", "sentences": [{"start_ms": start_ms, "end_ms": end_ms, "text": "x"}]}

    left = run(mend_seams(hear, [far, second]))
    assert asked == []
    assert [item.text for item in left.sentences] == ["One.", "A very long one", "goes on."]


def test_a_stretch_with_nothing_heard_has_no_seam() -> None:
    first = stretch_of((0, 5_000, "One."), window="w-0")
    empty = Stretch()
    third = stretch_of((1_200_000, 1_205_000, "Later."), window="w-1200000")

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        raise AssertionError("no seam to hear")

    merged = run(mend_seams(hear, [first, empty, third]))
    assert [item.text for item in merged.sentences] == ["One.", "Later."]


def test_a_recording_is_cut_in_ten_minute_stretches_and_a_short_tail_joins() -> None:
    assert stretches(0) == []
    assert stretches(90_000) == [(0, 90_000)]
    assert stretches(1_500_000) == [(0, 600_000), (600_000, 1_200_000), (1_200_000, 1_500_000)]
    assert stretches(1_260_000) == [(0, 600_000), (600_000, 1_260_000)]
    assert stretches(720_000) == [(0, 600_000), (600_000, 720_000)]


# Providers that answer text only ----------------------------------------------------


def test_text_windows_start_and_end_at_pieces_and_are_a_minute_or_more() -> None:
    pieces = [(i * 15_000, i * 15_000 + 14_000) for i in range(10)]
    assert text_windows(pieces) == [(0, 74_000), (75_000, 149_000)]
    assert text_windows(pieces[:6]) == [(0, 74_000), (75_000, 89_000)]
    # A long silence is never inside a window.
    assert text_windows([(0, 14_000), (600_000, 614_000)]) == [(0, 14_000), (600_000, 614_000)]
    assert text_windows([]) == []


def test_each_text_window_is_one_sentence() -> None:
    texts = iter(["First part", "", "Third part"])
    calls: list[tuple[int, int, str]] = []

    async def hear(start_ms: int, end_ms: int, before: str) -> dict[str, Any]:
        calls.append((start_ms, end_ms, before))
        return {"text": next(texts), "sentences": []}

    stretch = run(hear_text_run(hear, [(0, 62_000), (70_000, 131_000), (140_000, 200_000)], "Old."))
    assert [(item.start_ms, item.end_ms, item.text) for item in stretch.sentences] == [
        (0, 62_000, "First part"),
        (140_000, 200_000, "Third part"),
    ]
    assert calls[0][2] == "Old." and calls[2][2] == "Old. First part"
    assert len(stretch.windows) == 3


# Paragraphs ------------------------------------------------------------------------------


def numbered(count: int, per_window: int = 10) -> list[Heard]:
    return [
        Heard(i * 1_000, i * 1_000 + 900, f"Sentence {i}.", f"w-{i // per_window}")
        for i in range(count)
    ]


def test_grouping_goes_in_chunks_of_at_most_300_sentences_and_applies_the_starts() -> None:
    sentences = numbered(700)
    seen: list[tuple[int, str]] = []

    def group(texts: list[str], before: str) -> list[int]:
        seen.append((len(texts), before))
        return list(range(0, len(texts), 5))

    paragraphs = regroup(sentences, group)
    assert [count for count, _ in seen] == [300, 300, 100]
    assert seen[0][1] == ""
    assert seen[1][1] == "Sentence 295. Sentence 296. Sentence 297. Sentence 298. Sentence 299."
    assert [len(p) for p in paragraphs] == [5] * 140
    assert [i for p in paragraphs for i in p] == list(range(700))


def test_unusable_starts_leave_one_paragraph_and_errors_are_not_hidden() -> None:
    sentences = numbered(12)
    assert regroup(sentences, lambda texts, before: [99, -3, "x"]) == [list(range(12))]
    one_chunk = regroup(sentences, lambda texts, before: [])
    assert [len(p) for p in one_chunk] == [12]

    def broken(texts: list[str], before: str) -> list[int]:
        raise RuntimeError("model said no")

    with pytest.raises(RuntimeError):
        regroup(sentences, broken)


def test_without_grouping_each_window_is_a_paragraph() -> None:
    assert [len(p) for p in paragraphs_by_window(numbered(25))] == [10, 10, 5]


def test_the_structured_transcript_has_the_shape_the_desktop_reads() -> None:
    sentences = numbered(6, per_window=3)
    windows = [Window("w-0", 0, 3_000), Window("w-1", 3_000, 6_000)]
    state = transcript_state(
        sentences,
        windows,
        [[0, 1, 2, 3], [4, 5]],
        model="m",
        at="2026-10-12T00:00:00+00:00",
        grouped=True,
    )
    assert state["version"] == 1
    assert state["draft"] is None and state["checking"] is None and state["live"] == []
    assert state["gaps"] == [] and state["grouping"] == {"state": "idle"}
    assert state["confirmedUntilMs"] == 5_900
    assert [s["id"] for s in state["sentences"]][:2] == ["s-0", "s-1000"]
    assert "changed" not in state["sentences"][0]
    assert all(p["frozen"] for p in state["paragraphs"])
    assert state["paragraphs"][0] == {
        "id": "p-0",
        "sentenceIds": ["s-0", "s-1000", "s-2000", "s-3000"],
        "frozen": True,
    }
    first = state["windows"][0]
    assert first["state"] == "checked" and first["pass"] == "minute" and first["model"] == "m"
    assert first["live"] == []
    assert render(state) == (
        "[00:00] Sentence 0. Sentence 1. Sentence 2. Sentence 3.\n\n[00:04] Sentence 4. Sentence 5."
    )
    off = transcript_state(
        sentences, windows, [[0, 1, 2], [3, 4, 5]], model="m", at="x", grouped=False
    )
    assert off["grouping"] == {"state": "off"}


# The button ------------------------------------------------------------------------------


class FakeGateway:
    """The Analysis model: paragraph starts at every sixth sentence, after an optional failure."""

    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.requests = 0
        self.model = SimpleNamespace(display="analysis-model")

    def for_role(self, role: str) -> Any:
        return (self.model, "low") if role == "analysis" else None

    def complete(self, model: Any, **request: Any) -> Any:
        self.requests += 1
        if self.fail:
            self.fail -= 1
            from gunther.llm import ModelError

            raise ModelError("The analysis model is down")
        count = len(re.findall(r"^\d+\. ", request["messages"], flags=re.MULTILINE))
        return SimpleNamespace(text=json.dumps({"starts": list(range(1, count + 1, 6))}))


class FakeRecording:
    """Stands in for ``relisten_audio``: answers from a timeline, or fails on demand."""

    def __init__(self, sentences: list[tuple[int, int, str]]) -> None:
        self.recorder = Recorder(sentences)
        self.fail: str | None = None
        # Only windows starting here or later fail, so earlier ones were answered.
        self.fail_from_ms = 0
        self.requests = 0

    async def __call__(self, choice: Any, pcm: bytes, **request: Any) -> dict[str, Any]:
        self.requests += 1
        if self.fail and request["start_ms"] >= self.fail_from_ms:
            raise ListenError(self.fail)
        assert len(pcm) > 0
        return await self.recorder(request["start_ms"], request["end_ms"], request["before"])


def make_app(tmp_path: Path) -> TestClient:
    client = TestClient(create_app(settings_for(tmp_path)))
    return client


def sessions_of(client: TestClient) -> Any:
    return client.app.state.knowledge_service.sessions


def content_of(recording_id: str, transcript: str = "Typed notes, not stamped.") -> str:
    return CONTENT_HEAD.replace(REC, recording_id) + "## Transcript\n\n" + transcript


def seed(
    client: TestClient,
    tmp_path: Path,
    recording_id: str = REC,
    *,
    seconds: float = 100.0,
    content: str | None = None,
    status: str = "completed",
    audio: bool = True,
    title: str = "Lecture",
) -> str:
    """A recording with its audio and a source holding its transcript; returns the source id."""

    directory = tmp_path / "recordings"
    directory.mkdir(exist_ok=True)
    name = f"{recording_id}.wav"
    if audio:
        encode(directory / name, "wav", "pcm_s16le", seconds, 8_000)
    with session_scope(sessions_of(client)) as session:
        session.add(
            RecordingSession(
                id=recording_id,
                title=title,
                status=status,
                file_name=name,
                content_type="audio/wav",
                transcript="Typed notes, not stamped.",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    created_source = client.post(
        "/api/sources",
        headers=SIDECAR,
        json={
            "title": title,
            "kind": "note",
            "content": content_of(recording_id) if content is None else content,
        },
    )
    assert created_source.status_code == 201
    return created_source.json()["source"]["id"]


@pytest.fixture
def heard(monkeypatch: pytest.MonkeyPatch) -> FakeRecording:
    fake = FakeRecording(timeline(9))
    monkeypatch.setattr(retranscribe, "relisten_audio", fake)
    return fake


def ready(client: TestClient, gateway: Any = None) -> None:
    with_provider(client, "qwen")
    client.app.state.models = gateway or FakeGateway()
    client.app.state.retranscriber.retry_waits = (0, 0, 0)


def press(client: TestClient, recording_id: str = REC) -> Any:
    return client.post(f"/api/recordings/{recording_id}/transcribe-again", headers=SIDECAR)


def status_of(client: TestClient, recording_id: str = REC) -> dict[str, Any]:
    return client.get(f"/api/recordings/{recording_id}/transcribe-again", headers=SIDECAR).json()


def finished(client: TestClient, recording_id: str = REC) -> dict[str, Any]:
    """The status once the job has ended."""

    for _ in range(1_000):
        status = status_of(client, recording_id)
        if status["state"] in ("done", "failed"):
            return status
        time.sleep(0.01)
    raise AssertionError("still running")


def snapshot(client: TestClient, source_id: str) -> tuple[str, str, str | None]:
    with session_scope(sessions_of(client)) as session:
        recording = session.get(RecordingSession, REC)
        return (
            session.get(Source, source_id).content,
            recording.transcript,
            recording.transcript_segments_json,
        )


def state_of(client: TestClient) -> dict[str, Any]:
    with session_scope(sessions_of(client)) as session:
        return json.loads(session.get(RecordingSession, REC).transcript_segments_json)


def test_the_button_hears_the_audio_again_and_replaces_the_transcript_everywhere(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path)
        ready(client)
        original = content_of(REC)
        assert status_of(client) == {"state": "idle", "heardMs": 0, "totalMs": 0, "error": None}
        started = press(client)
        assert started.status_code == 202 and started.json()["state"] in ("waiting", "running")
        done = finished(client)
        assert done["state"] == "done" and done["error"] is None
        assert done["heardMs"] == done["totalMs"] > 99_000

        with session_scope(sessions_of(client)) as session:
            source = session.get(Source, source_id)
            recording = session.get(RecordingSession, REC)
            state = json.loads(recording.transcript_segments_json)
            head, heading, _ = transcript_section(original)
            # Everything before the transcript is untouched; the transcript is the new text.
            assert source.content == head + heading + "\n\n" + recording.transcript
            assert "Terms: gradient · loss function" in source.content
            assert "Typed notes" not in source.content
            assert recording.transcript == render(state)
            assert recording.transcript.startswith("[00:05] Sentence 0. Sentence 1.")
            assert "\n\n[01:05] Sentence 6." in recording.transcript
            assert state["version"] == 1 and state["grouping"] == {"state": "idle"}
            assert len(state["sentences"]) == 9
            assert all(p["frozen"] for p in state["paragraphs"])
            # The earlier text is kept as the source's earlier revision.
            revisions = session.scalars(
                select(SourceRevision).where(SourceRevision.source_id == source_id)
            ).all()
            assert len(revisions) == 2
            head_revision = session.get(SourceIndexHead, source_id).revision_id
            old_revision = next(r for r in revisions if r.id != head_revision)
            for revision in revisions:
                blocks = session.scalar(
                    select(func.count())
                    .select_from(ContentBlock)
                    .where(ContentBlock.revision_id == revision.id)
                )
                assert blocks > 0
            old_text = " ".join(
                block.content
                for block in session.scalars(
                    select(ContentBlock).where(ContentBlock.revision_id == old_revision.id)
                )
            )
            assert "Typed notes" in old_text
        # The listen copy is gone.
        assert not list((tmp_path / "recordings").glob("*.listen.*"))

        # Pressed again, it hears it all again.
        before = heard.requests
        assert press(client).status_code == 202
        assert finished(client)["state"] == "done" and heard.requests > before


def test_pressing_again_while_it_runs_keeps_the_one_job(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        ready(client)
        retranscriber = client.app.state.retranscriber

        async def twice() -> tuple[dict[str, Any], dict[str, Any]]:
            first = await retranscriber.start(REC)
            second = await retranscriber.start(REC)
            await retranscriber.wait()
            return first, second

        first, second = run(twice())
        assert first["state"] == second["state"] == "waiting"
        assert len(retranscriber.jobs) == 1 and retranscriber.status(REC)["state"] == "done"
        # One run's worth of requests: a 100 s recording is one window.
        assert heard.requests == 1


def test_a_long_recording_is_heard_in_parallel_stretches_with_its_seams_mended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRecording(timeline(77))  # the last one ends at 775 s
    monkeypatch.setattr(retranscribe, "relisten_audio", fake)
    with make_app(tmp_path) as client:
        # 13 minutes: two stretches (0-10 min, 10-13 min) and one seam at 10:00.
        seed(client, tmp_path, seconds=780.0)
        ready(client)
        press(client)
        assert finished(client)["totalMs"] > 779_000
        texts = [s["text"] for s in state_of(client)["sentences"]]
        assert "(partial)" not in " ".join(texts)
        assert texts == [f"Sentence {i}." for i in range(77)]
        assert any(w["id"] == "w-595000" for w in state_of(client)["windows"])


def test_without_an_analysis_model_each_window_is_one_paragraph(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        ready(client, SimpleNamespace(for_role=lambda role: None))
        press(client)
        assert finished(client)["state"] == "done"
        state = state_of(client)
        assert state["grouping"] == {"state": "off"}
        assert len(state["paragraphs"]) == len(state["windows"]) == 1


def test_when_grouping_fails_what_was_heard_is_kept_one_paragraph_per_window(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        gateway = FakeGateway(fail=99)
        ready(client, gateway)
        press(client)
        assert finished(client)["state"] == "done"
        assert gateway.requests == 3  # tried three times
        state = state_of(client)
        assert state["grouping"] == {"state": "off"} and len(state["sentences"]) == 9


def test_a_provider_without_word_times_gives_one_paragraph_per_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def text_only(choice: Any, pcm: bytes, **request: Any) -> dict[str, Any]:
        return {"text": f"Heard {request['start_ms']}", "sentences": []}

    monkeypatch.setattr(retranscribe, "relisten_audio", text_only)
    monkeypatch.setattr(
        retranscribe,
        "speech_pieces",
        lambda directory, recording_id, total: [(1_000, 14_000), (20_000, 34_000)],
    )
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        with_provider(client, "compatible")
        client.app.state.models = SimpleNamespace(for_role=lambda role: None)
        press(client)
        assert finished(client)["state"] == "done"
        state = state_of(client)
        assert [s["text"] for s in state["sentences"]] == ["Heard 1000"]
        assert [(s["startMs"], s["endMs"]) for s in state["sentences"]] == [(1_000, 34_000)]


def test_a_source_without_a_transcript_heading_gets_one(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        bare = CONTENT_HEAD.rstrip()
        source_id = seed(client, tmp_path, content=bare)
        ready(client)
        press(client)
        assert finished(client)["state"] == "done"
        content = snapshot(client, source_id)[0]
        assert content.startswith(bare + "\n\n## Transcript\n\n[00:05] Sentence 0.")


# When it can't ----------------------------------------------------------------------------


def test_a_provider_failure_writes_nothing_and_the_button_works_again(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path)
        ready(client)
        before = snapshot(client, source_id)
        heard.fail = "Qwen answered 401: Invalid API key"
        press(client)
        failed = finished(client)
        assert failed["state"] == "failed" and "401" in failed["error"]
        assert heard.requests == 3  # tried three times
        assert snapshot(client, source_id) == before
        assert not list((tmp_path / "recordings").glob("*.listen.*"))

        heard.fail = None
        press(client)
        assert finished(client)["state"] == "done"
        assert snapshot(client, source_id)[0] != before[0]


def test_it_says_why_it_cant_start(tmp_path: Path, heard: FakeRecording) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        seed(client, tmp_path, OTHER, status="capturing")
        ready(client)
        assert press(client, "rec_" + "f" * 24).status_code == 404
        assert press(client, "bad id!").status_code in (404, 405)

        unfinished = press(client, OTHER)
        assert unfinished.status_code == 409
        assert unfinished.json()["detail"] == "This recording hasn't finished yet."

        (tmp_path / "recordings" / f"{REC}.wav").unlink()
        missing = press(client, REC)
        assert missing.status_code == 409
        assert missing.json()["detail"] == "The audio of this recording isn't saved."

        registry = client.app.state.speech_registry
        client.app.state.speech_registry = dataclasses.replace(registry, roles={})
        no_model = press(client, REC)
        assert no_model.status_code == 409
        assert "Recording model in Settings" in no_model.json()["detail"]
        assert heard.requests == 0 and status_of(client)["state"] == "idle"


def test_unreadable_audio_and_silence_fail_with_a_plain_message(
    tmp_path: Path, heard: FakeRecording
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path, audio=False)
        (tmp_path / "recordings" / f"{REC}.wav").write_bytes(b"not audio at all" * 100)
        ready(client)
        press(client)
        assert "couldn't be read" in finished(client)["error"]

        heard.recorder.sentences = []
        encode(tmp_path / "recordings" / f"{REC}.wav", "wav", "pcm_s16le", 20.0, 8_000)
        press(client)
        assert finished(client)["error"] == "Nothing was heard in the audio."


# The top bar's activity list -----------------------------------------------------------


class Held(FakeRecording):
    """Answers like ``FakeRecording``, but only ``allow`` requests; the rest wait for ``open``."""

    def __init__(self, sentences: list[tuple[int, int, str]], allow: int = 0) -> None:
        super().__init__(sentences)
        self.allow = allow
        self.open = asyncio.Event()

    async def __call__(self, choice: Any, pcm: bytes, **request: Any) -> dict[str, Any]:
        if self.requests >= self.allow:
            self.requests += 1
            await self.open.wait()
            self.requests -= 1
        return await super().__call__(choice, pcm, **request)


async def settle() -> None:
    await asyncio.sleep(0.1)


async def until(check: Any) -> None:
    for _ in range(500):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


def listed(client: TestClient) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in client.app.state.activity.snapshot()["items"]}


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> Held:
    fake = Held(timeline(9))
    monkeypatch.setattr(retranscribe, "relisten_audio", fake)
    return fake


def test_a_job_is_listed_waiting_then_running_with_progress_then_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Held(timeline(30), allow=1)
    monkeypatch.setattr(retranscribe, "relisten_audio", fake)
    with make_app(tmp_path) as client:
        source_id = seed(client, tmp_path, seconds=300, title="Week 3 lecture")
        ready(client)
        retranscriber = client.app.state.retranscriber

        async def scenario() -> tuple[dict, dict, dict]:
            await retranscriber.start(REC)
            waiting = dict(listed(client)[f"transcribe:{REC}"])
            await until(lambda: (listed(client)[f"transcribe:{REC}"]["progress"] or 0) > 0)
            running = dict(listed(client)[f"transcribe:{REC}"])
            fake.open.set()
            await retranscriber.wait()
            return waiting, running, dict(listed(client)[f"transcribe:{REC}"])

        waiting, running, done = run(scenario())
    assert (waiting["state"], waiting["title"]) == ("waiting", "Waiting to transcribe again")
    assert waiting["detail"] == "Waiting for another recording" and waiting["canStop"] is True
    assert waiting["about"] == "Week 3 lecture" and waiting["kind"] == "transcribe"
    assert waiting["open"] == {"view": "item", "itemType": "source", "id": source_id}
    assert (running["state"], running["title"]) == ("running", "Transcribing again")
    assert running["progress"] is not None and 0 < running["progress"] < 1
    assert re.fullmatch(r"\d+:\d\d of 5:00 heard", running["detail"])
    assert (done["state"], done["title"]) == ("done", "Transcript updated")
    assert done["endedAt"] and done["canStop"] is False and done["error"] is None


def test_a_failed_job_says_so_in_the_list(tmp_path: Path, heard: FakeRecording) -> None:
    heard.fail = "The model is down"
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        ready(client)
        assert press(client).status_code == 202
        finished(client)
        item = listed(client)[f"transcribe:{REC}"]
    assert (item["state"], item["title"]) == ("failed", "Transcribing again failed")
    assert item["error"] == "The model is down" and item["detail"] == ""


def test_two_recordings_started_together_one_runs_and_one_waits(
    tmp_path: Path, held: Held
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        seed(client, tmp_path, OTHER, title="Second")
        ready(client)
        retranscriber = client.app.state.retranscriber

        async def scenario() -> tuple[dict, dict]:
            await retranscriber.start(REC)
            await retranscriber.start(OTHER)
            await until(lambda: held.requests >= 1)
            both = {key: dict(value) for key, value in listed(client).items()}
            held.open.set()
            await retranscriber.wait()
            return both, listed(client)

        both, after = run(scenario())
    assert both[f"transcribe:{REC}"]["state"] == "running"
    assert both[f"transcribe:{OTHER}"]["state"] == "waiting"
    assert {item["state"] for item in after.values()} == {"done"} and len(after) == 2


def test_stopping_a_running_job_removes_it_and_the_next_one_runs(
    tmp_path: Path, held: Held
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        seed(client, tmp_path, OTHER, title="Second")
        ready(client)
        retranscriber = client.app.state.retranscriber

        async def scenario() -> dict[str, Any]:
            await retranscriber.start(REC)
            await retranscriber.start(OTHER)
            await until(lambda: held.requests >= 1)
            assert list((tmp_path / "recordings").glob(f"{REC}*.listen.*"))
            assert client.app.state.activity.stop(f"transcribe:{REC}") is True
            await until(lambda: retranscriber.jobs[OTHER].state == "running")
            await until(lambda: held.requests >= 2)
            between = dict(listed(client))
            held.open.set()
            await retranscriber.wait()
            return between

        between = run(scenario())
        assert status_of(client)["state"] == "stopped"
        assert status_of(client, OTHER)["state"] == "done"
        assert not list((tmp_path / "recordings").glob("*.listen.*"))
        # The stopped recording keeps its old transcript, and the button is offered again.
        with session_scope(sessions_of(client)) as session:
            assert session.get(RecordingSession, REC).transcript == "Typed notes, not stamped."
        assert press(client).status_code == 202
        assert finished(client)["state"] == "done"
    assert f"transcribe:{REC}" not in between
    assert between[f"transcribe:{OTHER}"]["state"] == "running"


def test_stopping_a_waiting_job_works_too(tmp_path: Path, held: Held) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        seed(client, tmp_path, OTHER, title="Second")
        ready(client)
        retranscriber = client.app.state.retranscriber

        async def scenario() -> dict[str, Any]:
            await retranscriber.start(REC)
            await retranscriber.start(OTHER)
            await until(lambda: held.requests >= 1)
            assert retranscriber.stop(OTHER) is True
            assert retranscriber.stop("rec_" + "c" * 24) is False
            await settle()
            between = dict(listed(client))
            held.open.set()
            await retranscriber.wait()
            return between

        between = run(scenario())
        assert status_of(client, OTHER)["state"] == "stopped"
        assert status_of(client)["state"] == "done"
    assert f"transcribe:{OTHER}" not in between
    assert between[f"transcribe:{REC}"]["state"] == "running"


def test_shutdown_still_cancels_a_job_and_does_not_call_it_stopped(
    tmp_path: Path, held: Held
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        ready(client)
        retranscriber = client.app.state.retranscriber

        async def scenario() -> asyncio.Task:
            await retranscriber.start(REC)
            await until(lambda: held.requests >= 1)
            task = retranscriber._task_of[REC]
            await retranscriber.close()
            return task

        task = run(scenario())
        assert task.cancelled()
        assert retranscriber.jobs[REC].state != "stopped"
        assert f"transcribe:{REC}" not in listed(client)


def test_a_job_that_is_saving_its_transcript_can_no_longer_be_stopped(
    tmp_path: Path, held: Held
) -> None:
    with make_app(tmp_path) as client:
        seed(client, tmp_path)
        ready(client)
        retranscriber = client.app.state.retranscriber
        write, writing, release = retranscriber._write, threading.Event(), threading.Event()

        def slow_write(recording_id: str, state: dict[str, Any]) -> None:
            writing.set()
            release.wait(5)
            write(recording_id, state)

        retranscriber._write = slow_write

        async def scenario() -> tuple[bool | None, dict[str, Any]]:
            held.open.set()
            await retranscriber.start(REC)
            await until(writing.is_set)
            refused = client.app.state.activity.stop(f"transcribe:{REC}")
            saving = dict(listed(client)[f"transcribe:{REC}"])
            release.set()
            await retranscriber.wait()
            return refused, saving

        refused, saving = run(scenario())
        assert refused is False
        assert saving["detail"] == "Saving the transcript"
        assert status_of(client)["state"] == "done"
        assert listed(client)[f"transcribe:{REC}"]["title"] == "Transcript updated"
