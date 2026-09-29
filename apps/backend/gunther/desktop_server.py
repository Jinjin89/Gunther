"""Frozen desktop entry point for Gunther's private local API."""

from __future__ import annotations

import argparse
import errno
import ipaddress
import logging
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import uvicorn

from gunther.config import Settings
from gunther.mobile_gateway_pki import ensure_mobile_gateway_pki
from gunther.mobile_gateway_runtime import MobileGatewayRuntime
from gunther.service_settings import ServiceSettingsStore

TOKEN_FILE_NAME = "backend-auth-token"
READY_TOKEN_DIR_NAME = "backend-ready"
INSTANCE_LOCK_FILE_NAME = "backend-instance.lock"
# API keys and service addresses set in the app's Settings; private like the database.
SERVICE_SETTINGS_FILE_NAME = "service-settings.json"
MOBILE_GATEWAY_PORT = 8788
MOBILE_GATEWAY_PKI_DIR_NAME = "mobile-gateway-pki"
_LAUNCH_NONCE = re.compile(r"^[a-f0-9]{64}$")
_AUTH_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class MobileGatewayNetworkUnavailableError(RuntimeError):
    """Raised when no phone-reachable private interface can be published."""


class DesktopInstanceAlreadyRunningError(RuntimeError):
    """Raised when another helper already owns this private data directory."""


class DesktopInstanceLock:
    """Hold a process-scoped OS lock until the loopback helper shuts down."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> DesktopInstanceLock:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class MobileGatewayInterface:
    name: str
    ipv4_address: str
    is_default_route: bool


class MobileGatewayHandle:
    """Own the isolated gateway thread without coupling it to loopback health."""

    def __init__(self, runtime: MobileGatewayRuntime) -> None:
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._runtime = runtime

    def attach_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._thread = thread

    def attach_server(self, server: uvicorn.Server) -> None:
        with self._lock:
            self._server = server
            if self._stopping:
                server.should_exit = True

    def is_stopping(self) -> bool:
        with self._lock:
            return self._stopping

    def mark_started(self) -> bool:
        with self._lock:
            if self._stopping:
                return False
            self._runtime.started()
            return True

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopping = True
            server = self._server
            thread = self._thread
        if server is not None:
            server.should_exit = True
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        if thread is not None and thread.is_alive():
            self._runtime.stopped("HTTPS mobile gateway shutdown timed out")
        else:
            self._runtime.stopped()


def _private_open_flags(flags: int) -> int:
    return flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _ensure_private_directory(path: Path) -> None:
    """Create a private directory and refuse a symlink at the managed path."""

    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    except FileExistsError as error:
        raise RuntimeError(f"Private data path is not a directory: {path.name}") from error
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Private data path is not a real directory: {path.name}")
    if os.name != "nt":
        os.chmod(path, 0o700, follow_symlinks=False)


def _secure_data_tree(data_dir: Path) -> None:
    """Harden sidecar data without traversing or accepting symlinks."""

    _ensure_private_directory(data_dir)
    for root, directory_names, file_names in os.walk(data_dir, followlinks=False):
        root_path = Path(root)
        root_metadata = root_path.lstat()
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            raise RuntimeError("Gunther's private data tree contains a symlink")
        if os.name != "nt":
            os.chmod(root_path, 0o700, follow_symlinks=False)
        for name in (*directory_names, *file_names):
            candidate = root_path / name
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError(
                    f"Gunther's private data tree contains a symlink: {candidate.name}"
                )
            is_directory = stat.S_ISDIR(metadata.st_mode)
            if not (is_directory or stat.S_ISREG(metadata.st_mode)):
                raise RuntimeError(
                    f"Gunther's private data tree contains an unsupported entry: {candidate.name}"
                )
            if os.name != "nt":
                os.chmod(
                    candidate,
                    0o700 if is_directory else 0o600,
                    follow_symlinks=False,
                )


# Libraries live in the home folder by default: visible, easy to back up, and
# outside Documents, which iCloud may sync while a recording is still growing.
DEFAULT_LIBRARY_ROOT = Path.home() / "Gunther"


def _desktop_settings(data_dir: Path, auth_token: str) -> Settings:
    """The sidecar's settings; LIBRARY_ROOT in the data folder's .env overrides the default."""

    base = {
        "_env_file": data_dir / ".env",
        "database_url": f"sqlite+pysqlite:///{data_dir / 'gunther.sqlite'}",
        # Originals kept here by earlier versions move into the library root once.
        "previous_assets_dir": data_dir / "assets",
        "previous_recordings_dir": data_dir / "recordings",
        "auth_token": auth_token,
        "seed_demo": False,
        "service_settings_file": data_dir / SERVICE_SETTINGS_FILE_NAME,
    }
    settings = Settings(**base)
    configured = settings.library_root
    # The private data folder refuses symlinks, and library aliases are symlinks.
    inside_data = configured is not None and configured.is_relative_to(data_dir.resolve())
    if configured is None or inside_data:
        if inside_data:
            logging.getLogger(__name__).warning(
                "LIBRARY_ROOT cannot be inside Gunther's data folder; using ~/Gunther"
            )
        settings = Settings(**base, library_root=DEFAULT_LIBRARY_ROOT)
    return settings


def _acquire_instance_lock(data_dir: Path) -> DesktopInstanceLock:
    lock_path = data_dir / INSTANCE_LOCK_FILE_NAME
    descriptor = os.open(
        lock_path,
        _private_open_flags(os.O_RDWR | os.O_CREAT),
        0o600,
    )
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise DesktopInstanceAlreadyRunningError(
                "Gunther's local knowledge service is already running"
            ) from error
        raise
    return DesktopInstanceLock(descriptor)


def _validate_launch_nonce(launch_nonce: str) -> str:
    if _LAUNCH_NONCE.fullmatch(launch_nonce) is None:
        raise ValueError("launch nonce must contain exactly 64 lowercase hexadecimal characters")
    return launch_nonce


def _ready_token_path(data_dir: Path, launch_nonce: str) -> Path:
    filename = f"{TOKEN_FILE_NAME}.{_validate_launch_nonce(launch_nonce)}"
    return data_dir / READY_TOKEN_DIR_NAME / filename


def _write_launch_token(data_dir: Path, launch_nonce: str, token: str | None = None) -> str:
    """Publish this launch's secret without sharing or replacing a path."""

    ready_dir = data_dir / READY_TOKEN_DIR_NAME
    _ensure_private_directory(ready_dir)
    launch_token = token or secrets.token_urlsafe(32)
    if _AUTH_TOKEN.fullmatch(launch_token) is None:
        raise ValueError("launch token must be a 256-bit URL-safe secret")
    destination = _ready_token_path(data_dir, launch_nonce)
    descriptor = os.open(
        destination,
        _private_open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"{launch_nonce}\n{launch_token}\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(destination, 0o600, follow_symlinks=False)
        directory_descriptor = os.open(
            ready_dir,
            _private_open_flags(os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return launch_token


def _bind_loopback_socket(port: int) -> socket.socket:
    if not 0 <= port <= 65535:
        raise ValueError("loopback port must be between 0 and 65535")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", port))
        # Queue connections before publishing readiness. Requests arriving in
        # the tiny gap before Server.run() are held by the kernel, not refused.
        listener.listen(socket.SOMAXCONN)
        listener.set_inheritable(False)
    except Exception:
        listener.close()
        raise
    return listener


def _local_mdns_hostname() -> str:
    hostname = socket.gethostname().strip().rstrip(".").lower()
    if hostname.endswith(".local"):
        hostname = hostname[: -len(".local")]
    hostname = hostname.split(".", 1)[0]
    label = re.sub(r"[^a-z0-9-]+", "-", hostname).strip("-")[:63]
    label = label.strip("-") or "gunther"
    return f"{label}.local"


_PHYSICAL_MAC_INTERFACE = re.compile(r"^en[0-9]+$")
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_IFCONFIG_MAX_OUTPUT_BYTES = 256 * 1024
_ROUTE_MAX_OUTPUT_BYTES = 16 * 1024


def _is_private_lan_ipv4(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return any(address in network for network in _RFC1918_NETWORKS)


def _run_bounded_readonly_command(
    arguments: list[str],
    *,
    max_output_bytes: int,
) -> str | None:
    with tempfile.TemporaryFile() as captured_output:
        try:
            result = subprocess.run(
                arguments,
                check=False,
                stderr=subprocess.DEVNULL,
                stdout=captured_output,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        output_size = captured_output.tell()
        if result.returncode != 0 or output_size > max_output_bytes:
            return None
        captured_output.seek(0)
        output = captured_output.read(max_output_bytes + 1)
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _default_route_interface() -> str | None:
    if sys.platform != "darwin":
        return None
    output = _run_bounded_readonly_command(
        ["/sbin/route", "-n", "get", "default"],
        max_output_bytes=_ROUTE_MAX_OUTPUT_BYTES,
    )
    if output is None:
        return None
    match = re.search(r"^\s*interface:\s*(\S+)\s*$", output, re.MULTILINE)
    return match.group(1) if match is not None else None


def _parse_physical_interface_addresses(output: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current_name: str | None = None
    for line in output.splitlines():
        header = re.match(r"^([^\s:]+):\s+flags=", line)
        if header is not None:
            current_name = header.group(1)
            blocks[current_name] = [line]
        elif current_name is not None:
            blocks[current_name].append(line)

    addresses: dict[str, str] = {}
    for name, lines in blocks.items():
        if _PHYSICAL_MAC_INTERFACE.fullmatch(name) is None:
            continue
        block = "\n".join(lines)
        if re.search(r"^\s*status:\s+active\s*$", block, re.MULTILINE) is None:
            continue
        candidates = {
            match.group(1)
            for match in re.finditer(r"^\s*inet\s+(\S+)", block, re.MULTILINE)
            if _is_private_lan_ipv4(match.group(1))
        }
        if len(candidates) == 1:
            addresses[name] = candidates.pop()
    return addresses


def _private_lan_interfaces() -> tuple[MobileGatewayInterface, ...]:
    if sys.platform != "darwin":
        return ()
    output = _run_bounded_readonly_command(
        ["/sbin/ifconfig", "-a"],
        max_output_bytes=_IFCONFIG_MAX_OUTPUT_BYTES,
    )
    if output is None:
        return ()
    default_route = _default_route_interface()
    return tuple(
        MobileGatewayInterface(
            name=name,
            ipv4_address=address,
            is_default_route=name == default_route,
        )
        for name, address in _parse_physical_interface_addresses(output).items()
    )


def _select_private_lan_interface(
    candidates: tuple[MobileGatewayInterface, ...],
) -> MobileGatewayInterface:
    eligible = tuple(
        candidate
        for candidate in candidates
        if _PHYSICAL_MAC_INTERFACE.fullmatch(candidate.name) is not None
        and _is_private_lan_ipv4(candidate.ipv4_address)
    )
    default_candidates = tuple(candidate for candidate in eligible if candidate.is_default_route)
    if len(default_candidates) == 1:
        return default_candidates[0]
    if len(default_candidates) > 1 or len(eligible) != 1:
        raise MobileGatewayNetworkUnavailableError(
            "HTTPS mobile gateway could not reliably choose one physical private IPv4 LAN interface"
        )
    return eligible[0]


def _discover_mobile_gateway_interface() -> MobileGatewayInterface:
    return _select_private_lan_interface(_private_lan_interfaces())


def _gateway_public_address(
    private_ipv4_address: str,
    port: int,
    api_prefix: str,
) -> str:
    if not _is_private_lan_ipv4(private_ipv4_address):
        raise MobileGatewayNetworkUnavailableError(
            "HTTPS mobile gateway could not find a private IPv4 LAN address"
        )
    prefix = api_prefix.rstrip("/")
    return f"https://{private_ipv4_address}:{port}{prefix}/"


def _bind_gateway_socket(private_ipv4_address: str, port: int) -> socket.socket:
    if not _is_private_lan_ipv4(private_ipv4_address):
        raise MobileGatewayNetworkUnavailableError(
            "HTTPS mobile gateway refused to bind a non-private interface"
        )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((private_ipv4_address, port))
        listener.set_inheritable(False)
    except Exception:
        listener.close()
        raise
    return listener


def _gateway_error_message(error: BaseException, port: int) -> str:
    if isinstance(error, MobileGatewayNetworkUnavailableError):
        return str(error)
    if isinstance(error, OSError):
        detail = error.strerror or "the address is unavailable"
        return f"HTTPS mobile gateway could not bind port {port}: {detail}"
    return f"HTTPS mobile gateway could not start: {type(error).__name__}"


def _run_mobile_gateway(
    data_dir: Path,
    settings: Settings,
    runtime: MobileGatewayRuntime,
    handle: MobileGatewayHandle,
    port: int,
    service_settings: ServiceSettingsStore | None = None,
) -> None:
    listener: socket.socket | None = None
    try:
        hostname = _local_mdns_hostname()
        network_interface = _discover_mobile_gateway_interface()
        private_addresses = (network_interface.ipv4_address,)
        address = _gateway_public_address(
            network_interface.ipv4_address,
            port,
            settings.api_prefix,
        )
        tls = ensure_mobile_gateway_pki(
            data_dir / MOBILE_GATEWAY_PKI_DIR_NAME,
            hostname=hostname,
            private_ip_addresses=private_addresses,
        )
        runtime.prepared(
            address=address,
            ca_fingerprint=tls.ca_sha256_fingerprint,
            ca_certificate_pem=tls.ca_certificate_pem,
        )

        # Import lazily so the frozen entry point creates no development app.
        from gunther.main import create_app

        gateway_app = create_app(
            settings,
            allow_sidecar_auth=False,
            mobile_gateway=runtime,
            service_settings=service_settings,
        )
        config = uvicorn.Config(
            gateway_app,
            host=network_interface.ipv4_address,
            port=port,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            server_header=False,
            ssl_context_factory=lambda _config, _default: tls.create_server_ssl_context(),
        )
        config.load()
        listener = _bind_gateway_socket(network_interface.ipv4_address, port)
        server = uvicorn.Server(config)
        handle.attach_server(server)
        if not handle.mark_started():
            return
        server.run(sockets=[listener])
        runtime.stopped(
            None if handle.is_stopping() else "HTTPS mobile gateway stopped unexpectedly"
        )
    except BaseException as error:
        runtime.stopped(_gateway_error_message(error, port))
    finally:
        if listener is not None:
            listener.close()


def _start_mobile_gateway(
    data_dir: Path,
    settings: Settings,
    runtime: MobileGatewayRuntime,
    port: int,
    service_settings: ServiceSettingsStore | None = None,
) -> MobileGatewayHandle:
    handle = MobileGatewayHandle(runtime)
    thread = threading.Thread(
        target=_run_mobile_gateway,
        args=(data_dir, settings, runtime, handle, port, service_settings),
        name="gunther-mobile-gateway",
        daemon=True,
    )
    handle.attach_thread(thread)
    thread.start()
    return handle


def _stop_when_stdin_closes(server: uvicorn.Server) -> None:
    """The desktop shell holds our stdin open; end of file means it exited, even by crashing."""

    def watch() -> None:
        with suppress(OSError):
            while os.read(0, 4096):
                pass
        server.should_exit = True

    threading.Thread(target=watch, name="gunther-parent-watch", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Gunther desktop knowledge service")
    parser.add_argument("--data-dir", type=Path, required=True)
    # Not 8787, so a development backend never blocks the installed app.
    parser.add_argument("--port", type=int, default=28787)
    parser.add_argument("--launch-nonce", required=True)
    parser.add_argument("--mobile-gateway-port", type=int, default=MOBILE_GATEWAY_PORT)
    parser.add_argument("--disable-mobile-gateway", action="store_true")
    parser.add_argument("--exit-when-stdin-closes", action="store_true")
    args = parser.parse_args()

    # SQLite, uploaded originals, recordings, OCR output, PKI, and token files
    # all inherit private POSIX modes. Windows uses its native ACL model.
    if os.name != "nt":
        os.umask(0o077)

    # Avoid constructing the development app while PyInstaller imports this
    # module. The desktop server owns its settings and creates exactly one app.
    os.environ["GUNTHER_DESKTOP_SIDECAR"] = "1"
    from gunther.main import create_app

    launch_nonce = _validate_launch_nonce(args.launch_nonce)
    # Do not resolve the final path: doing so would follow a data-dir symlink.
    data_dir = args.data_dir.expanduser().absolute()
    _ensure_private_directory(data_dir)
    listener: socket.socket | None = None
    ready_token_path: Path | None = None
    gateway = None
    try:
        with _acquire_instance_lock(data_dir):
            # A second instance or occupied port must fail before credentials
            # are published. The prebound socket is passed directly to Uvicorn.
            listener = _bind_loopback_socket(args.port)
            _secure_data_tree(data_dir)
            for private_dir in (data_dir / "assets", data_dir / "recordings"):
                _ensure_private_directory(private_dir)

            auth_token = secrets.token_urlsafe(32)
            settings = _desktop_settings(data_dir, auth_token)
            gateway_runtime = MobileGatewayRuntime(enabled=not args.disable_mobile_gateway)
            # One store for both apps, so a change saved in Settings reaches the phone too.
            service_settings = ServiceSettingsStore(settings.service_settings_file)
            sidecar_app = create_app(
                settings, mobile_gateway=gateway_runtime, service_settings=service_settings
            )
            config = uvicorn.Config(
                sidecar_app,
                host="127.0.0.1",
                port=args.port,
                log_level="warning",
                access_log=False,
                proxy_headers=False,
                server_header=False,
            )
            config.load()
            _secure_data_tree(data_dir)

            # Publish only after the listener, app, and database initialize.
            _write_launch_token(data_dir, launch_nonce, auth_token)
            ready_token_path = _ready_token_path(data_dir, launch_nonce)
            if not args.disable_mobile_gateway:
                gateway = _start_mobile_gateway(
                    data_dir,
                    settings,
                    gateway_runtime,
                    args.mobile_gateway_port,
                    service_settings,
                )
            server = uvicorn.Server(config)
            if args.exit_when_stdin_closes:
                _stop_when_stdin_closes(server)
            with suppress(KeyboardInterrupt):
                server.run(sockets=[listener])
    finally:
        if gateway is not None:
            try:
                gateway.stop()
            except KeyboardInterrupt:
                # The re-raised SIGINT can arrive while join() is waiting for
                # the gateway thread. A non-blocking second stop still marks
                # the runtime stopped and lets normal desktop shutdown finish.
                gateway.stop(timeout=0)
        if listener is not None:
            listener.close()
        if ready_token_path is not None:
            ready_token_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
