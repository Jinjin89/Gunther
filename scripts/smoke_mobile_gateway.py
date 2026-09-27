"""Exercise the desktop-to-mobile trust boundary against a disposable server.

The smoke reads the sidecar launch token from its protected file so secrets do
not appear in process arguments. It only accepts a loopback sidecar and the
private HTTPS gateway published by that sidecar. The target workspace is
mutated, so callers must opt in with ``--allow-write`` and use temporary data.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import ssl
import stat
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


class SmokeFailure(RuntimeError):
    pass


def _loopback_url(value: str) -> str:
    normalized = value.rstrip("/") + "/"
    parsed = urlsplit(normalized)
    if parsed.scheme != "http" or not parsed.hostname:
        raise argparse.ArgumentTypeError("sidecar URL must use loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("sidecar URL contains unsafe parts")
    try:
        is_loopback = (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname).is_loopback
        )
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise argparse.ArgumentTypeError("sidecar URL must be loopback-only")
    return normalized


def _private_gateway_url(value: str) -> str:
    normalized = value.rstrip("/") + "/"
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/api/")
    ):
        raise SmokeFailure("gateway published an unsafe connection address")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise SmokeFailure(
            "gateway did not publish a literal private address"
        ) from error
    if (
        not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    ):
        raise SmokeFailure("gateway address is not a private LAN address")
    return normalized


def _read_secret(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SmokeFailure("sidecar token file is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
            raise SmokeFailure("sidecar token file is not a bounded regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            token = stream.read(4097).strip()
    finally:
        os.close(descriptor)
    if not token or len(token) > 4096:
        raise SmokeFailure("sidecar token file is invalid")
    return token


def _json_request(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    context: ssl.SSLContext | None = None,
) -> tuple[int, dict[str, Any]]:
    url = urljoin(base_url, path.lstrip("/"))
    body = None if payload is None else json.dumps(payload).encode()
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=request_headers, method=method)
    handlers: list[Any] = [ProxyHandler({})]
    if context is not None:
        handlers.append(HTTPSHandler(context=context))
    opener = build_opener(*handlers)
    try:
        with opener.open(request, timeout=15) as response:
            status_code = response.status
            raw = response.read(512 * 1024 + 1)
    except HTTPError as error:
        status_code = error.code
        raw = error.read(512 * 1024 + 1)
    except (URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        error_number = getattr(reason, "errno", None)
        diagnostic = type(reason).__name__
        if isinstance(error_number, int):
            diagnostic = f"{diagnostic} errno={error_number}"
        raise SmokeFailure(
            f"{method} {path.split('?', 1)[0]} could not connect ({diagnostic})"
        ) from error
    if len(raw) > 512 * 1024:
        raise SmokeFailure(f"{method} {path} returned an oversized response")
    if not raw:
        return status_code, {}
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeFailure(f"{method} {path} returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {path} did not return an object")
    return status_code, decoded


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def run(sidecar_url: str, token_file: Path) -> dict[str, Any]:
    token = _read_secret(token_file)
    sidecar_headers = {"X-Gunther-Token": token}
    status_code, gateway_status = _json_request(
        sidecar_url,
        "GET",
        "mobile-gateway/status",
        headers=sidecar_headers,
    )
    _expect(status_code == 200, "sidecar could not read gateway status")
    _expect(gateway_status.get("running") is True, "HTTPS gateway is not running")
    gateway_url = _private_gateway_url(str(gateway_status.get("address", "")))
    ca_pem = gateway_status.get("caCertificatePem")
    fingerprint = str(gateway_status.get("caFingerprint", "")).replace(":", "").lower()
    _expect(isinstance(ca_pem, str), "gateway CA certificate is missing")
    try:
        ca_der = ssl.PEM_cert_to_DER_cert(ca_pem)
    except ValueError as error:
        raise SmokeFailure("gateway CA certificate is invalid") from error
    actual_fingerprint = hashlib.sha256(ca_der).hexdigest()
    _expect(actual_fingerprint == fingerprint, "gateway CA fingerprint does not match")
    tls_context = ssl.create_default_context(cadata=ca_pem)

    status_code, pairing = _json_request(
        sidecar_url,
        "POST",
        "pairing/sessions",
        payload={
            "scopes": ["api:access", "transcription:stream"],
            "expiresInSeconds": 120,
        },
        headers=sidecar_headers,
    )
    _expect(status_code == 201, "sidecar could not create a pairing session")
    exchange_payload = {
        "pairingId": pairing.get("pairingId"),
        "pairingCode": pairing.get("pairingCode"),
        "deviceName": "Gateway smoke phone",
        "platform": "smoke",
    }
    status_code, credential = _json_request(
        gateway_url,
        "POST",
        "pairing/exchange",
        payload=exchange_payload,
        context=tls_context,
    )
    _expect(status_code == 201, "gateway did not exchange the one-time code")
    access_token = credential.get("accessToken")
    _expect(
        isinstance(access_token, str) and access_token.startswith("gdt_"),
        "device token missing",
    )
    bearer = {"Authorization": f"Bearer {access_token}"}

    status_code, _ = _json_request(gateway_url, "GET", "health", context=tls_context)
    _expect(status_code == 401, "gateway allowed an unauthenticated API request")
    status_code, _ = _json_request(
        gateway_url,
        "GET",
        f"health?token={token}",
        context=tls_context,
    )
    _expect(status_code == 401, "gateway accepted the sidecar token in a URL")
    status_code, health = _json_request(
        gateway_url,
        "GET",
        "health",
        headers=bearer,
        context=tls_context,
    )
    _expect(
        status_code == 200 and health.get("status") == "ok",
        "device bearer was rejected",
    )
    status_code, bootstrap = _json_request(
        gateway_url,
        "GET",
        "workspace/bootstrap",
        headers=bearer,
        context=tls_context,
    )
    _expect(
        status_code == 200
        and bootstrap.get("authKind") == "device"
        and bootstrap.get("workspaceId") == credential.get("workspaceId"),
        "device bootstrap identity did not match the pairing result",
    )
    status_code, _ = _json_request(
        gateway_url,
        "GET",
        "mobile-gateway/status",
        headers=bearer,
        context=tls_context,
    )
    _expect(status_code == 403, "device could read sidecar-only gateway status")
    status_code, _ = _json_request(
        gateway_url,
        "POST",
        "pairing/exchange",
        payload=exchange_payload,
        context=tls_context,
    )
    _expect(status_code == 409, "pairing code was reusable")

    raw_device = credential.get("device")
    if not isinstance(raw_device, dict):
        raise SmokeFailure("pairing result did not include a device object")
    device_id = raw_device.get("id")
    _expect(isinstance(device_id, str), "paired device id is missing")
    status_code, _ = _json_request(
        sidecar_url,
        "POST",
        f"devices/{device_id}/revoke",
        headers=sidecar_headers,
    )
    _expect(status_code == 200, "sidecar could not revoke the paired device")
    status_code, _ = _json_request(
        gateway_url,
        "GET",
        "health",
        headers=bearer,
        context=tls_context,
    )
    _expect(status_code == 401, "revoked bearer remained active")

    return {
        "status": "passed",
        "checks": 12,
        "workspaceId": credential.get("workspaceId"),
        "deviceId": device_id,
        "gatewayAddress": gateway_url,
        "caSha256": actual_fingerprint,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar-url", type=_loopback_url, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="confirm the target is a disposable desktop workspace",
    )
    args = parser.parse_args()
    if not args.allow_write:
        parser.error("--allow-write is required for this synthetic flow")
    try:
        result = run(args.sidecar_url, args.token_file.expanduser().absolute())
    except SmokeFailure as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
