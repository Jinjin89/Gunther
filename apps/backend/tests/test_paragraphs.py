"""Paragraphs by content: the Analysis model says where paragraphs start, and only that.

No network: the model is a fake provider.
"""

import json
from pathlib import Path
from typing import Any

import openai
import pytest
from fake_models import FakeProvider, api_error, gateway
from fastapi.testclient import TestClient

from gunther.main import create_app
from gunther.paragraphs import PARAGRAPHS_PROMPT
from tests.test_listen_again import (
    SIDECAR,
    answer_text,
    live_url,
    settings_for,
    start_recording,
    with_provider,
)

SENTENCES = ["好，我们开始上课。", "今天讲梯度下降。", "它是一种优化方法。", "有问题吗？", "没有。"]


def reply(starts: Any) -> str:
    return json.dumps({"starts": starts})


def client_with(tmp_path: Path, fake: FakeProvider | None) -> TestClient:
    client = TestClient(create_app(settings_for(tmp_path)))
    client.__enter__()
    client.app.state.models = gateway(fake) if fake else gateway(FakeProvider(), roles={})
    return client


def ask(client: TestClient, **body: Any) -> Any:
    body.setdefault("sentences", SENTENCES)
    body.setdefault("before", "")
    body.setdefault("pass", "live")
    return client.post("/api/recordings/rec_1/paragraphs", headers=SIDECAR, json=body)


def test_numbers_become_zero_based_starts_with_the_prompt_and_message_as_planned(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(reply([1, 4]))
    client = client_with(tmp_path, fake)
    answer = ask(client, before="上一段的结尾。")
    assert answer.status_code == 200
    assert answer.json()["starts"] == [0, 3]
    request = fake.requests[0]
    assert request["messages"][0]["content"] == PARAGRAPHS_PROMPT
    assert request["messages"][1]["content"] == (
        "Before (context only, already grouped):\n上一段的结尾。\n\nSentences:\n"
        "1. 好，我们开始上课。\n2. 今天讲梯度下降。\n3. 它是一种优化方法。\n"
        "4. 有问题吗？\n5. 没有。"
    )
    # Without a "before", the message has only the sentences.
    ask(client, sentences=["a", "b"])
    assert fake.requests[1]["messages"][1]["content"] == "Sentences:\n1. a\n2. b"
    client.__exit__(None, None, None)


def test_bad_numbers_are_dropped_zero_is_kept_and_an_empty_answer_is_a_502(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        reply([4, 4, 2, 9, 0, -1, 2.5, "3", True, None]),
        reply([2]),
        reply([9, "x"]),
        reply("nope"),
        "not json at all",
    )
    client = client_with(tmp_path, fake)
    assert ask(client).json()["starts"] == [0, 1, 3]
    assert ask(client).json()["starts"] == [0, 1]
    for _ in range(3):
        bad = ask(client)
        assert bad.status_code == 502
        assert bad.json()["detail"] == "The model didn't say where paragraphs start"
    client.__exit__(None, None, None)


def test_no_analysis_model_is_a_424_and_a_model_error_is_a_502(tmp_path: Path) -> None:
    client = client_with(tmp_path, None)
    assert ask(client).status_code == 424
    client.__exit__(None, None, None)
    failing = FakeProvider(api_error(openai.InternalServerError, 500, "provider unavailable"))
    client = client_with(tmp_path, failing)
    broken = ask(client)
    assert broken.status_code == 502 and "provider unavailable" in broken.json()["detail"]
    client.__exit__(None, None, None)


def test_the_request_is_limited(tmp_path: Path) -> None:
    client = client_with(tmp_path, FakeProvider(reply([1])))
    assert ask(client, sentences=["x"] * 301).status_code == 422
    assert ask(client, sentences=["x" * 2001]).status_code == 422
    assert ask(client, sentences=[]).status_code == 422
    assert ask(client, **{"pass": "other"}).status_code == 422
    client.__exit__(None, None, None)


def test_service_ready_says_whether_paragraphs_can_be_grouped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer_text(monkeypatch)
    for fake, expected in ((FakeProvider(reply([1])), True), (None, False)):
        client = client_with(tmp_path, fake)
        with_provider(client)  # saving settings rebuilds the models; set ours again
        client.app.state.models = gateway(fake) if fake else gateway(FakeProvider(), roles={})
        recording_id = start_recording(client)
        with client.websocket_connect(live_url(recording_id), headers=SIDECAR) as socket:
            ready = socket.receive_json()
        assert ready["group_paragraphs"] is expected
        client.__exit__(None, None, None)
