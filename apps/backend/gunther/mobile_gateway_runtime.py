"""Thread-safe runtime status for the optional desktop mobile gateway."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from gunther.device_auth import WORKSPACE_PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class MobileGatewaySnapshot:
    enabled: bool
    running: bool
    address: str | None
    ca_fingerprint: str | None
    ca_certificate_pem: str | None
    protocol_version: int
    error: str | None


class MobileGatewayRuntime:
    """Publish gateway readiness without sharing secrets or mutable state."""

    def __init__(self, *, enabled: bool) -> None:
        self._lock = Lock()
        self._snapshot = MobileGatewaySnapshot(
            enabled=enabled,
            running=False,
            address=None,
            ca_fingerprint=None,
            ca_certificate_pem=None,
            protocol_version=WORKSPACE_PROTOCOL_VERSION,
            error=None,
        )

    def prepared(
        self,
        *,
        address: str,
        ca_fingerprint: str,
        ca_certificate_pem: str,
    ) -> None:
        with self._lock:
            self._snapshot = MobileGatewaySnapshot(
                enabled=True,
                running=False,
                address=address,
                ca_fingerprint=ca_fingerprint,
                ca_certificate_pem=ca_certificate_pem,
                protocol_version=WORKSPACE_PROTOCOL_VERSION,
                error=None,
            )

    def started(self) -> None:
        with self._lock:
            current = self._snapshot
            self._snapshot = MobileGatewaySnapshot(
                enabled=current.enabled,
                running=True,
                address=current.address,
                ca_fingerprint=current.ca_fingerprint,
                ca_certificate_pem=current.ca_certificate_pem,
                protocol_version=current.protocol_version,
                error=None,
            )

    def stopped(self, error: str | None = None) -> None:
        with self._lock:
            current = self._snapshot
            self._snapshot = MobileGatewaySnapshot(
                enabled=current.enabled,
                running=False,
                address=current.address,
                ca_fingerprint=current.ca_fingerprint,
                ca_certificate_pem=current.ca_certificate_pem,
                protocol_version=current.protocol_version,
                error=error,
            )

    def snapshot(self) -> MobileGatewaySnapshot:
        with self._lock:
            return self._snapshot
