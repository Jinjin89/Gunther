from __future__ import annotations

import socket
import ssl
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gunther.desktop_server as desktop_server
from gunther.config import Settings
from gunther.main import create_app
from gunther.mobile_gateway_runtime import MobileGatewayRuntime

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        stt_provider="compatible",
        auth_token=SIDECAR_TOKEN,
        cors_origins=["http://tauri.localhost"],
    )


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("mobile gateway did not reach the expected state")


def reserve_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def test_gateway_address_prefers_a_private_ipv4_and_never_has_a_token() -> None:
    address = desktop_server._gateway_public_address(
        "192.168.1.20",
        8788,
        "/api",
    )

    assert address == "https://192.168.1.20:8788/api/"
    assert "token" not in address.lower()


def test_gateway_uses_the_normalized_configured_api_prefix() -> None:
    settings = Settings(api_prefix="/v1/")

    assert settings.api_prefix == "/v1"
    assert (
        desktop_server._gateway_public_address("192.168.1.20", 8788, settings.api_prefix)
        == "https://192.168.1.20:8788/v1/"
    )


def test_gateway_api_prefix_rejects_unsafe_or_ambiguous_paths() -> None:
    assert Settings(api_prefix="/").api_prefix == ""
    for value in ("api", "//api", "/../api", "/api?token=x", "/api#x", "/api%2Fv1", "/api\\v1"):
        with pytest.raises(ValueError):
            Settings(api_prefix=value)


def test_gateway_rejects_non_private_addresses() -> None:
    try:
        desktop_server._gateway_public_address(
            "8.8.8.8",
            8788,
            "/api",
        )
    except desktop_server.MobileGatewayNetworkUnavailableError as error:
        assert "private IPv4 LAN address" in str(error)
    else:
        raise AssertionError("an IPv6-only hostname was incorrectly published on IPv4")


def test_gateway_ignores_vpn_and_virtual_interfaces_and_prefers_default_en() -> None:
    selected = desktop_server._select_private_lan_interface(
        (
            desktop_server.MobileGatewayInterface("utun4", "10.8.0.2", True),
            desktop_server.MobileGatewayInterface("bridge0", "192.168.64.1", False),
            desktop_server.MobileGatewayInterface("en0", "192.168.1.44", False),
        )
    )
    assert selected.name == "en0"
    assert selected.ipv4_address == "192.168.1.44"

    default_selected = desktop_server._select_private_lan_interface(
        (
            desktop_server.MobileGatewayInterface("en0", "192.168.1.44", False),
            desktop_server.MobileGatewayInterface("en5", "10.20.1.8", True),
        )
    )
    assert default_selected.name == "en5"


def test_interface_discovery_uses_only_local_physical_en_addresses(monkeypatch) -> None:
    monkeypatch.setattr(desktop_server.sys, "platform", "darwin")
    monkeypatch.setattr(desktop_server, "_default_route_interface", lambda: "en0")
    ifconfig_output = """en0: flags=8863<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST>
\tinet 192.168.1.12 netmask 0xffffff00 broadcast 192.168.1.255
\tstatus: active
feth961: flags=8963<UP,BROADCAST,SMART,RUNNING,PROMISC,SIMPLEX,MULTICAST>
\tinet 192.168.196.166 netmask 0xffffff00 broadcast 192.168.196.255
\tstatus: active
utun0: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST>
\tinet 10.20.11.90 --> 10.20.11.90 netmask 0xffffffff
awdl0: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST>
\tinet 192.168.1.4 netmask 0xffffff00 broadcast 192.168.1.255
\tstatus: active
bridge0: flags=8863<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST>
\tinet 192.168.1.15 netmask 0xffffff00 broadcast 192.168.1.255
\tstatus: active
"""
    monkeypatch.setattr(
        desktop_server,
        "_run_bounded_readonly_command",
        lambda arguments, max_output_bytes: (
            ifconfig_output if arguments == ["/sbin/ifconfig", "-a"] else None
        ),
    )
    monkeypatch.setattr(
        desktop_server.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("hostname peers must never become gateway candidates")
        ),
    )

    discovered = desktop_server._private_lan_interfaces()

    assert discovered == (desktop_server.MobileGatewayInterface("en0", "192.168.1.12", True),)


def test_interface_command_output_is_bounded_and_fails_closed(monkeypatch) -> None:
    class SuccessfulResult:
        returncode = 0

    def oversized_command(*_args, **kwargs):
        kwargs["stdout"].write(b"x" * 17)
        return SuccessfulResult()

    monkeypatch.setattr(
        desktop_server.subprocess,
        "run",
        oversized_command,
    )

    assert (
        desktop_server._run_bounded_readonly_command(
            ["/sbin/ifconfig", "-a"],
            max_output_bytes=16,
        )
        is None
    )


def test_gateway_fails_closed_when_physical_interfaces_are_ambiguous() -> None:
    try:
        desktop_server._select_private_lan_interface(
            (
                desktop_server.MobileGatewayInterface("en0", "192.168.1.44", False),
                desktop_server.MobileGatewayInterface("en5", "10.20.1.8", False),
            )
        )
    except desktop_server.MobileGatewayNetworkUnavailableError as error:
        assert "reliably choose" in str(error)
    else:
        raise AssertionError("an ambiguous interface set did not fail closed")


def test_gateway_socket_binds_only_the_selected_private_address(monkeypatch) -> None:
    class FakeSocket:
        bound_to: tuple[str, int] | None = None
        inheritable: bool | None = None

        def setsockopt(self, *_args) -> None:
            pass

        def bind(self, target: tuple[str, int]) -> None:
            self.bound_to = target

        def set_inheritable(self, value: bool) -> None:
            self.inheritable = value

        def close(self) -> None:
            pass

    fake = FakeSocket()
    monkeypatch.setattr(desktop_server.socket, "socket", lambda *_args: fake)

    listener = desktop_server._bind_gateway_socket("192.168.1.44", 18443)

    assert listener is fake
    assert fake.bound_to == ("192.168.1.44", 18443)
    assert fake.inheritable is False


def test_https_gateway_serves_tls_and_rejects_unauthenticated_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(desktop_server, "_local_mdns_hostname", lambda: "gunther-test.local")
    monkeypatch.setattr(
        desktop_server,
        "_discover_mobile_gateway_interface",
        lambda: desktop_server.MobileGatewayInterface("en0", "192.168.50.8", True),
    )

    def bind_test_loopback(_address: str, selected_port: int) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", selected_port))
        listener.set_inheritable(False)
        return listener

    monkeypatch.setattr(desktop_server, "_bind_gateway_socket", bind_test_loopback)
    runtime = MobileGatewayRuntime(enabled=True)
    port = reserve_port()
    handle = desktop_server._start_mobile_gateway(
        tmp_path,
        settings_for(tmp_path),
        runtime,
        port,
    )
    try:
        wait_until(lambda: runtime.snapshot().running or runtime.snapshot().error is not None)
        snapshot = runtime.snapshot()
        assert snapshot.error is None
        assert snapshot.running is True
        assert snapshot.address == f"https://192.168.50.8:{port}/api/"
        assert snapshot.ca_certificate_pem is not None

        context = ssl.create_default_context(cadata=snapshot.ca_certificate_pem)
        response = b""

        def gateway_responded() -> bool:
            nonlocal response
            try:
                with (
                    socket.create_connection(("127.0.0.1", port), timeout=0.5) as tcp,
                    context.wrap_socket(
                        tcp,
                        server_hostname="gunther-test.local",
                    ) as tls,
                ):
                    tls.sendall(
                        b"GET /api/health HTTP/1.1\r\n"
                        b"Host: gunther-test.local\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    response = tls.recv(512)
                    return bool(response)
            except OSError:
                return False

        wait_until(gateway_responded)
        assert b" 401 " in response
    finally:
        handle.stop()
    assert runtime.snapshot().running is False
    assert runtime.snapshot().error is None


def test_gateway_bind_failure_is_reported_while_loopback_app_stays_healthy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(desktop_server, "_local_mdns_hostname", lambda: "gunther-test.local")
    monkeypatch.setattr(
        desktop_server,
        "_discover_mobile_gateway_interface",
        lambda: desktop_server.MobileGatewayInterface("en0", "192.168.50.8", True),
    )
    port = reserve_port()
    monkeypatch.setattr(
        desktop_server,
        "_bind_gateway_socket",
        lambda _address, _port: (_ for _ in ()).throw(OSError(48, "Address already in use")),
    )
    runtime = MobileGatewayRuntime(enabled=True)
    settings = settings_for(tmp_path)
    sidecar_app = create_app(settings, mobile_gateway=runtime)
    handle = desktop_server._start_mobile_gateway(
        tmp_path,
        settings,
        runtime,
        port,
    )
    try:
        wait_until(lambda: runtime.snapshot().error is not None)
        with TestClient(sidecar_app) as client:
            health = client.get(
                "/api/health",
                headers={"X-Gunther-Token": SIDECAR_TOKEN},
            )
            gateway_status = client.get(
                "/api/mobile-gateway/status",
                headers={"X-Gunther-Token": SIDECAR_TOKEN},
            )

        assert health.status_code == 200
        assert gateway_status.status_code == 200
        assert gateway_status.json()["running"] is False
        assert f"port {port}" in gateway_status.json()["error"]
    finally:
        handle.stop()


def test_gateway_stop_timeout_is_visible_and_never_reports_running() -> None:
    runtime = MobileGatewayRuntime(enabled=True)
    runtime.started()
    handle = desktop_server.MobileGatewayHandle(runtime)
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    handle.attach_thread(thread)
    thread.start()
    try:
        handle.stop(timeout=0.01)
        snapshot = runtime.snapshot()
        assert snapshot.running is False
        assert snapshot.error == "HTTPS mobile gateway shutdown timed out"
    finally:
        release.set()
        thread.join(timeout=1)
