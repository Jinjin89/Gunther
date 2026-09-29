import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import gunther.desktop_server as desktop_server
from gunther.config import Settings
from gunther.desktop_server import (
    READY_TOKEN_DIR_NAME,
    DesktopInstanceAlreadyRunningError,
    _acquire_instance_lock,
    _bind_loopback_socket,
    _ready_token_path,
    _secure_data_tree,
    _write_launch_token,
)
from gunther.main import create_app

TOKEN = "test-token-with-at-least-256-bits-of-placeholder-entropy-000000000000"
ALLOWED_ORIGIN = "http://tauri.localhost"
EVIL_ORIGIN = "https://evil.example"


def make_client(tmp_path: Path, auth_token: str | None = TOKEN) -> TestClient:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        openai_api_key=None,
        stt_provider="openai",
        auth_token=auth_token,
        cors_origins=[ALLOWED_ORIGIN],
    )
    return TestClient(create_app(settings))


def test_desktop_helper_rotates_a_private_256_bit_launch_token(tmp_path: Path) -> None:
    first_nonce = "a" * 64
    second_nonce = "b" * 64
    first = _write_launch_token(tmp_path, first_nonce)
    token_path = _ready_token_path(tmp_path, first_nonce)
    assert token_path.read_text(encoding="utf-8") == f"{first_nonce}\n{first}\n"
    assert len(first) >= 43
    assert token_path.stat().st_mode & 0o777 == 0o600

    second = _write_launch_token(tmp_path, second_nonce)
    assert second != first
    second_path = _ready_token_path(tmp_path, second_nonce)
    assert second_path.read_text(encoding="utf-8") == f"{second_nonce}\n{second}\n"
    assert token_path.read_text(encoding="utf-8") == f"{first_nonce}\n{first}\n"
    assert second_path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / READY_TOKEN_DIR_NAME).stat().st_mode & 0o777 == 0o700


def test_launch_token_never_overwrites_the_same_nonce_or_follows_a_symlink(
    tmp_path: Path,
) -> None:
    nonce = "c" * 64
    _write_launch_token(tmp_path, nonce, "A" * 43)
    with pytest.raises(FileExistsError):
        _write_launch_token(tmp_path, nonce, "B" * 43)
    assert _ready_token_path(tmp_path, nonce).read_text(encoding="utf-8") == (
        f"{nonce}\n{'A' * 43}\n"
    )

    symlink_nonce = "d" * 64
    target = tmp_path / "outside-token"
    target.write_text("untouched", encoding="utf-8")
    _ready_token_path(tmp_path, symlink_nonce).symlink_to(target)
    with pytest.raises(OSError):
        _write_launch_token(tmp_path, symlink_nonce, "C" * 43)
    assert target.read_text(encoding="utf-8") == "untouched"


def test_instance_lock_and_prebound_socket_fail_closed_without_touching_owner(
    tmp_path: Path,
) -> None:
    first_lock = _acquire_instance_lock(tmp_path)
    listener = _bind_loopback_socket(0)
    try:
        with pytest.raises(DesktopInstanceAlreadyRunningError):
            _acquire_instance_lock(tmp_path)
        port = listener.getsockname()[1]
        with pytest.raises(OSError):
            _bind_loopback_socket(port)
        assert listener.getsockname() == ("127.0.0.1", port)
    finally:
        listener.close()
        first_lock.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_private_data_tree_hardens_existing_history_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    assets = tmp_path / "assets" / "originals"
    recordings = tmp_path / "recordings"
    assets.mkdir(parents=True)
    recordings.mkdir()
    database = tmp_path / "gunther.sqlite"
    original = assets / "paper.pdf"
    recording = recordings / "lecture.wav"
    log = tmp_path / "backend.log"
    for path in (database, original, recording, log):
        path.write_bytes(b"private")
        path.chmod(0o666)
    assets.chmod(0o755)

    _secure_data_tree(tmp_path)

    for directory in (tmp_path, tmp_path / "assets", assets, recordings):
        assert directory.stat().st_mode & 0o777 == 0o700
    for path in (database, original, recording, log):
        assert path.stat().st_mode & 0o777 == 0o600

    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "linked"
    link.symlink_to(outside)
    with pytest.raises(RuntimeError, match="symlink"):
        _secure_data_tree(tmp_path)
    assert outside.read_text(encoding="utf-8") == "outside"


def test_main_does_not_publish_a_token_when_loopback_prebind_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gunther-backend",
            "--data-dir",
            str(tmp_path),
            "--port",
            "0",
            "--launch-nonce",
            "e" * 64,
            "--disable-mobile-gateway",
        ],
    )
    monkeypatch.setattr(
        desktop_server,
        "_bind_loopback_socket",
        lambda _port: (_ for _ in ()).throw(OSError("occupied")),
    )
    previous_umask = os.umask(0o077)
    os.umask(previous_umask)
    try:
        with pytest.raises(OSError, match="occupied"):
            desktop_server.main()
    finally:
        os.umask(previous_umask)
    assert not (tmp_path / READY_TOKEN_DIR_NAME).exists()


def test_real_helper_isolated_launch_does_not_disturb_first_instance(tmp_path: Path) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    probe.listen()
    port = probe.getsockname()[1]
    occupied_dir = tmp_path / "occupied"
    occupied_nonce = "0" * 64
    occupied = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gunther.desktop_server",
            "--data-dir",
            str(occupied_dir),
            "--port",
            str(port),
            "--disable-mobile-gateway",
            "--launch-nonce",
            occupied_nonce,
        ],
        cwd=Path(desktop_server.__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert occupied.wait(timeout=5) != 0
    assert not _ready_token_path(occupied_dir, occupied_nonce).exists()
    probe.settimeout(0.1)
    with pytest.raises(TimeoutError):
        probe.accept()
    probe.close()
    data_dir = tmp_path / "first"
    first_nonce = "1" * 64
    second_nonce = "2" * 64
    arguments = [
        sys.executable,
        "-m",
        "gunther.desktop_server",
        "--data-dir",
        str(data_dir),
        "--port",
        str(port),
        "--disable-mobile-gateway",
    ]
    first = subprocess.Popen(
        [*arguments, "--launch-nonce", first_nonce],
        cwd=Path(desktop_server.__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    first_token_path = _ready_token_path(data_dir, first_nonce)
    try:
        deadline = time.monotonic() + 10
        while not first_token_path.exists() and time.monotonic() < deadline:
            assert first.poll() is None
            time.sleep(0.05)
        lines = first_token_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == first_nonce
        token = lines[1]

        second = subprocess.Popen(
            [*arguments, "--launch-nonce", second_nonce],
            cwd=Path(desktop_server.__file__).resolve().parents[1],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert second.wait(timeout=5) != 0
        finally:
            if second.poll() is None:
                second.terminate()
                second.wait(timeout=5)

        assert not _ready_token_path(data_dir, second_nonce).exists()
        assert first_token_path.read_text(encoding="utf-8").splitlines()[1] == token
        response = httpx.get(
            f"http://127.0.0.1:{port}/api/health",
            headers={"X-Gunther-Token": token},
            timeout=3,
        )
        assert response.status_code == 200
        assert first.poll() is None
    finally:
        first.terminate()
        first.wait(timeout=10)


def test_helper_stops_when_the_desktop_shell_goes_away(tmp_path: Path) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    nonce = "3" * 64
    helper = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gunther.desktop_server",
            "--data-dir",
            str(tmp_path),
            "--port",
            str(port),
            "--disable-mobile-gateway",
            "--exit-when-stdin-closes",
            "--launch-nonce",
            nonce,
        ],
        cwd=Path(desktop_server.__file__).resolve().parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    token_path = _ready_token_path(tmp_path, nonce)
    try:
        deadline = time.monotonic() + 10
        while not token_path.exists() and time.monotonic() < deadline:
            assert helper.poll() is None
            time.sleep(0.05)
        assert token_path.exists()
        assert helper.stdin is not None
        helper.stdin.close()
        assert helper.wait(timeout=10) == 0
        assert not token_path.exists()
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=5)


@pytest.mark.skipif(
    not os.environ.get("GUNTHER_FROZEN_HELPER"),
    reason="set GUNTHER_FROZEN_HELPER to validate a release helper",
)
def test_frozen_helper_publishes_private_nonce_bound_readiness(tmp_path: Path) -> None:
    executable = Path(os.environ["GUNTHER_FROZEN_HELPER"])
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    nonce = "f" * 64
    process = subprocess.Popen(
        [
            str(executable),
            "--data-dir",
            str(tmp_path),
            "--port",
            str(port),
            "--disable-mobile-gateway",
            "--launch-nonce",
            nonce,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    token_path = _ready_token_path(tmp_path, nonce)
    try:
        deadline = time.monotonic() + 15
        while not token_path.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.05)
        token = token_path.read_text(encoding="utf-8").splitlines()[1]
        response = httpx.get(
            f"http://127.0.0.1:{port}/api/health",
            headers={"X-Gunther-Token": token},
            timeout=3,
        )
        assert response.status_code == 200
        if os.name != "nt":
            for root, directory_names, file_names in os.walk(tmp_path):
                root_path = Path(root)
                assert root_path.stat().st_mode & 0o777 == 0o700
                for name in directory_names:
                    assert (root_path / name).stat().st_mode & 0o777 == 0o700
                for name in file_names:
                    assert (root_path / name).stat().st_mode & 0o777 == 0o600
    finally:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
    assert not token_path.exists()


def test_configured_token_requires_constant_boundary_on_http(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/api/health").status_code == 401
        assert (
            client.get("/api/health", headers={"X-Gunther-Token": "wrong"}).status_code
            == 401
        )
        assert client.get("/api/health?token=wrong").status_code == 401

        response = client.get("/api/health", headers={"X-Gunther-Token": TOKEN})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert "token" not in response.text.lower()

        assert client.get(f"/api/health?token={TOKEN}").status_code == 200


def test_tokenless_service_rejects_an_unexpected_token(tmp_path: Path) -> None:
    with make_client(tmp_path, auth_token=None) as client:
        assert client.get("/api/health").status_code == 200
        assert (
            client.get("/api/health", headers={"X-Gunther-Token": TOKEN}).status_code
            == 401
        )
        assert client.get(f"/api/health?token={TOKEN}").status_code == 401


def test_origin_is_enforced_before_a_browser_can_write(tmp_path: Path) -> None:
    payload = {
        "title": "Must not be created",
        "question": "Can an untrusted web page write here?",
        "description": "Origin protection test",
    }
    with make_client(tmp_path) as client:
        rejected = client.post(
            "/api/knowledge-bases",
            json=payload,
            headers={"X-Gunther-Token": TOKEN, "Origin": EVIL_ORIGIN},
        )
        assert rejected.status_code == 403

        listed = client.get(
            "/api/knowledge-bases",
            headers={"X-Gunther-Token": TOKEN},
        )
        assert listed.status_code == 200
        assert listed.json() == []

        accepted = client.post(
            "/api/knowledge-bases",
            json={**payload, "title": "Allowed capture"},
            headers={"X-Gunther-Token": TOKEN, "Origin": ALLOWED_ORIGIN},
        )
        assert accepted.status_code == 201

        preflight = client.options(
            "/api/knowledge-bases",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Gunther-Token, Content-Type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

        evil_preflight = client.options(
            "/api/knowledge-bases",
            headers={"Origin": EVIL_ORIGIN, "Access-Control-Request-Method": "POST"},
        )
        assert evil_preflight.status_code == 403


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/api/recordings/live", {"Origin": ALLOWED_ORIGIN}),
        ("/api/recordings/live?token=wrong", {"Origin": ALLOWED_ORIGIN}),
        (f"/api/recordings/live?token={TOKEN}", {"Origin": EVIL_ORIGIN}),
    ],
)
def test_websocket_rejects_missing_or_wrong_token_and_origin(
    tmp_path: Path,
    path: str,
    headers: dict[str, str],
) -> None:
    with (
        make_client(tmp_path) as client,
        pytest.raises(WebSocketDisconnect) as rejected,
        client.websocket_connect(path, headers=headers),
    ):
        pass
    assert rejected.value.code == 1008


def test_websocket_accepts_query_token_and_allowed_origin(tmp_path: Path) -> None:
    with make_client(tmp_path) as client, client.websocket_connect(
        f"/api/recordings/live?token={TOKEN}",
        headers={"Origin": ALLOWED_ORIGIN},
    ) as websocket:
        event = websocket.receive_json()
        assert event["type"] == "service.error"
        assert event["code"] == "not_configured"


def test_tokenless_websocket_rejects_an_unexpected_token(tmp_path: Path) -> None:
    with (
        make_client(tmp_path, auth_token=None) as client,
        pytest.raises(WebSocketDisconnect) as rejected,
        client.websocket_connect(f"/api/recordings/live?token={TOKEN}"),
    ):
        pass
    assert rejected.value.code == 1008
