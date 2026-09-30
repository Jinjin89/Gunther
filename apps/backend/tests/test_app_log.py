import io
import logging
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gunther import app_log, desktop_server
from gunther.tts_providers import describe_wav, join_wav


def _record(message: str, name: str = "gunther.tts_service", level: int = logging.INFO):
    return logging.LogRecord(name, level, __file__, 1, message, None, None)


def test_lines_go_to_one_file_per_day(tmp_path: Path) -> None:
    day = ["2026-09-30"]
    handler = app_log.DailyFileHandler(tmp_path, today=lambda: day[0])
    handler.setFormatter(app_log.LineFormatter())
    handler.emit(_record("first"))
    day[0] = "2026-10-01"
    handler.emit(_record("second"))
    handler.close()

    first = (tmp_path / "2026-09-30.backend.log").read_text(encoding="utf-8")
    second = (tmp_path / "2026-10-01.backend.log").read_text(encoding="utf-8")
    assert first.endswith(" INFO  backend/tts_service first\n")
    assert "second" in second and "second" not in first
    # The app's format: local date, time with milliseconds, offset.
    assert first[:10] == "2026-" + first[5:10] and first[23] in "+-Z"


def test_a_full_file_goes_on_in_the_next_part(tmp_path: Path) -> None:
    handler = app_log.DailyFileHandler(tmp_path, max_bytes=200, today=lambda: "2026-09-30")
    handler.setFormatter(app_log.LineFormatter())
    for index in range(6):
        handler.emit(_record(f"line {index} " + "x" * 40))
    handler.close()
    assert (tmp_path / "2026-09-30.backend.log").stat().st_size >= 200
    assert (tmp_path / "2026-09-30.backend.2.log").exists()


@pytest.mark.parametrize(
    ("text", "hidden"),
    [
        ("GET /api/recordings/1?token=abcDEF123", "abcDEF123"),
        ("Authorization: Bearer sk-live-1234567890", "sk-live-1234567890"),
        ('{"apiKey": "qwen-secret-key", "model": "x"}', "qwen-secret-key"),
        ("key sk-proj-ABCDEFGHIJK in a message", "sk-proj-ABCDEFGHIJK"),
        ("https://oss.example/a.wav?Expires=1&Signature=s1gn3d", "s1gn3d"),
    ],
)
def test_secrets_never_reach_a_file(text: str, hidden: str) -> None:
    line = app_log.LineFormatter().format(_record(text))
    assert hidden not in line


def test_sources_are_short() -> None:
    formatter = app_log.LineFormatter()
    assert " backend/desktop_server " in formatter.format(_record("x", "gunther.desktop_server"))
    assert " uvicorn " in formatter.format(_record("x", "uvicorn.error"))
    assert " WARN  " in formatter.format(_record("x", level=logging.WARNING))


def test_each_request_is_one_line_without_its_query(caplog: pytest.LogCaptureFixture) -> None:
    inner = FastAPI()

    @inner.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @inner.post("/api/things")
    def make() -> dict[str, str]:
        return {"made": "yes"}

    @inner.get("/api/broken")
    def broken() -> None:
        raise RuntimeError("boom")

    client = TestClient(app_log.RequestLog(inner), raise_server_exceptions=False)
    with caplog.at_level(logging.INFO, logger="api"):
        client.get("/api/health?token=secret")
        client.post("/api/things?token=secret")
        client.get("/api/missing")
        client.get("/api/broken")
    lines = [record.getMessage() for record in caplog.records if record.name == "api"]
    # A quick read is only kept at DEBUG; a change, a miss and a failure are kept.
    assert not any("/api/health" in line for line in lines)
    assert any(line.startswith("POST /api/things 200 ") for line in lines)
    assert any(line.startswith("GET /api/missing 404 ") for line in lines)
    assert any(line.startswith("GET /api/broken 500 ") for line in lines)
    assert not any("secret" in line for line in lines)


def _wav(seconds: float = 0.1, rate: int = 24000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\0\0" * int(rate * seconds))
    return out.getvalue()


def test_a_wav_header_is_checked_against_its_length() -> None:
    sound = _wav()
    text, fine = describe_wav(sound)
    assert fine and "24000 Hz, 16-bit, 1 channel(s)" in text and "0.1 s" in text

    # A streamed file whose header was never filled in: sizes left at their maximum.
    streamed = sound[:4] + b"\xff\xff\xff\xff" + sound[8:40] + b"\xff\xff\xff\xff" + sound[44:]
    text, fine = describe_wav(streamed)
    assert not fine and "data size says 4294967295 bytes" in text

    text, fine = describe_wav(b"<html>error</html>")
    assert not fine and "not a WAV" in text

    # Joined parts get a header of their own that agrees.
    assert describe_wav(join_wav([sound, sound]))[1]


def test_the_data_folder_check_survives_a_file_that_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "gunther.sqlite").write_bytes(b"x")
    vanishing = tmp_path / ".service-settings.tmp123.json"
    vanishing.write_text("{}", encoding="utf-8")
    real_lstat = Path.lstat

    def lstat(path: Path):
        if path == vanishing:
            # Renamed into place by another thread between listing and checking.
            vanishing.unlink(missing_ok=True)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert desktop_server._secure_data_tree(tmp_path) == 1


def test_qwens_two_gigabyte_header_is_repaired() -> None:
    from gunther.tts_providers import repair_wav

    sound = _wav(0.2)
    # What Qwen sends: both sizes left at about 2 GB, as a stream writer leaves them.
    qwen = (
        sound[:4]
        + (2147483591).to_bytes(4, "little")
        + sound[8:40]
        + (2147483547).to_bytes(4, "little")
        + sound[44:]
    )
    assert not describe_wav(qwen)[1]
    repaired = repair_wav(qwen)
    assert repaired == sound
    assert describe_wav(repaired)[1]
    # A good file, or something that is not a WAV, is left as it is.
    assert repair_wav(sound) is sound
    assert repair_wav(b"<html>") == b"<html>"
    with wave.open(io.BytesIO(repaired), "rb") as reader:
        assert reader.getnframes() == 4800
