from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from gunther.database import session_scope
from gunther.models import (
    DevicePairingSession,
    PairedDevice,
    WorkspaceIdentity,
    utc_now,
)

WORKSPACE_PROTOCOL_VERSION = 1
DEVICE_SCOPES = frozenset({"api:access", "transcription:stream"})
DEFAULT_DEVICE_SCOPES = ("api:access", "transcription:stream")
_DEVICE_TOKEN_PATTERN = re.compile(r"^gdt_[A-Za-z0-9_-]{43}$")


class DeviceAuthError(RuntimeError):
    """Base error for the device trust boundary."""


class PairingNotFoundError(DeviceAuthError):
    pass


class PairingCodeInvalidError(DeviceAuthError):
    pass


class PairingExpiredError(DeviceAuthError):
    pass


class PairingAlreadyUsedError(DeviceAuthError):
    pass


class PairedDeviceNotFoundError(DeviceAuthError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceIdentityView:
    workspace_id: str
    display_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PairedDeviceView:
    id: str
    workspace_id: str
    name: str
    platform: str
    scopes: tuple[str, ...]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class PairingSessionCredential:
    pairing_id: str
    pairing_code: str
    workspace: WorkspaceIdentityView
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class DeviceCredential:
    access_token: str
    device: PairedDeviceView
    workspace: WorkspaceIdentityView


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    kind: Literal["sidecar", "device", "development"]
    device_id: str | None = None
    scopes: tuple[str, ...] = ()

    def has_scope(self, scope: str) -> bool:
        return self.kind != "device" or scope in self.scopes


class DeviceAuthService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def workspace(self) -> WorkspaceIdentityView:
        with session_scope(self.sessions) as session:
            workspace = session.scalar(
                select(WorkspaceIdentity).where(
                    WorkspaceIdentity.singleton_key == "primary"
                )
            )
            if workspace is None:
                raise DeviceAuthError("Workspace identity is missing")
            return _workspace_view(workspace)

    def create_pairing_session(
        self,
        scopes: tuple[str, ...],
        ttl_seconds: int,
    ) -> PairingSessionCredential:
        normalized_scopes = _normalize_scopes(scopes)
        now = utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        pairing_id = f"pair_{secrets.token_hex(12)}"
        pairing_code = secrets.token_urlsafe(32)
        with session_scope(self.sessions) as session:
            workspace = session.scalar(
                select(WorkspaceIdentity).where(
                    WorkspaceIdentity.singleton_key == "primary"
                )
            )
            if workspace is None:
                raise DeviceAuthError("Workspace identity is missing")
            session.add(
                DevicePairingSession(
                    id=pairing_id,
                    workspace_id=workspace.workspace_id,
                    code_hash=_pairing_code_hash(pairing_id, pairing_code),
                    scopes_json=json.dumps(normalized_scopes),
                    created_at=now,
                    expires_at=expires_at,
                )
            )
            workspace_view = _workspace_view(workspace)
        return PairingSessionCredential(
            pairing_id=pairing_id,
            pairing_code=pairing_code,
            workspace=workspace_view,
            scopes=normalized_scopes,
            created_at=now,
            expires_at=expires_at,
        )

    def exchange_pairing_code(
        self,
        pairing_id: str,
        pairing_code: str,
        device_name: str,
        platform: str,
    ) -> DeviceCredential:
        now = utc_now()
        candidate_hash = _pairing_code_hash(pairing_id, pairing_code)
        with session_scope(self.sessions) as session:
            # The conditional UPDATE is deliberately the first database
            # statement. SQLite can then serialize simultaneous exchanges
            # before either caller observes and attempts to claim the code.
            claimed = session.execute(
                update(DevicePairingSession)
                .where(
                    DevicePairingSession.id == pairing_id,
                    DevicePairingSession.code_hash == candidate_hash,
                    DevicePairingSession.used_at.is_(None),
                    DevicePairingSession.expires_at > now,
                )
                .values(used_at=now)
            )
            if claimed.rowcount != 1:
                pairing = session.get(DevicePairingSession, pairing_id)
                if pairing is None:
                    raise PairingNotFoundError("Pairing session was not found")
                if not secrets.compare_digest(candidate_hash, pairing.code_hash):
                    raise PairingCodeInvalidError("Pairing code is invalid")
                if pairing.used_at is not None:
                    raise PairingAlreadyUsedError("Pairing code was already used")
                if pairing.expires_at <= now:
                    raise PairingExpiredError("Pairing code has expired")
                raise PairingAlreadyUsedError("Pairing code is no longer available")

            pairing = session.get(DevicePairingSession, pairing_id)
            if pairing is None:
                raise PairingNotFoundError("Pairing session was not found")

            access_token = f"gdt_{secrets.token_urlsafe(32)}"
            device = PairedDevice(
                id=f"dev_{secrets.token_hex(12)}",
                workspace_id=pairing.workspace_id,
                name=device_name,
                platform=platform,
                token_hash=_device_token_hash(access_token),
                scopes_json=pairing.scopes_json,
                created_at=now,
            )
            session.add(device)
            session.flush()
            workspace = session.scalar(
                select(WorkspaceIdentity).where(
                    WorkspaceIdentity.workspace_id == pairing.workspace_id
                )
            )
            if workspace is None:
                raise DeviceAuthError("Workspace identity is missing")
            credential = DeviceCredential(
                access_token=access_token,
                device=_device_view(device),
                workspace=_workspace_view(workspace),
            )
        return credential

    def authenticate_device(self, access_token: str) -> AuthPrincipal | None:
        if not _DEVICE_TOKEN_PATTERN.fullmatch(access_token):
            return None
        token_hash = _device_token_hash(access_token)
        now = utc_now()
        with session_scope(self.sessions) as session:
            device = session.scalar(
                select(PairedDevice).where(
                    PairedDevice.token_hash == token_hash,
                    PairedDevice.revoked_at.is_(None),
                )
            )
            if device is None:
                return None
            if device.last_used_at is None or device.last_used_at <= now - timedelta(
                seconds=60
            ):
                device.last_used_at = now
            return AuthPrincipal(
                kind="device",
                device_id=device.id,
                scopes=_decode_scopes(device.scopes_json),
            )

    def list_devices(self) -> list[PairedDeviceView]:
        with session_scope(self.sessions) as session:
            devices = session.scalars(
                select(PairedDevice).order_by(PairedDevice.created_at.desc())
            ).all()
            return [_device_view(device) for device in devices]

    def revoke_device(self, device_id: str) -> PairedDeviceView:
        with session_scope(self.sessions) as session:
            device = session.get(PairedDevice, device_id)
            if device is None:
                raise PairedDeviceNotFoundError("Paired device was not found")
            if device.revoked_at is None:
                device.revoked_at = utc_now()
            session.flush()
            return _device_view(device)


def parse_bearer_authorization(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, credential = value.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not credential:
        return None
    if credential != credential.strip() or len(credential) > 128:
        return None
    return credential


def _normalize_scopes(scopes: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(scopes)))
    if not normalized or any(scope not in DEVICE_SCOPES for scope in normalized):
        raise ValueError("Device scopes are invalid")
    return normalized


def _decode_scopes(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as error:
        raise DeviceAuthError("Stored device scopes are invalid") from error
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise DeviceAuthError("Stored device scopes are invalid")
    return _normalize_scopes(tuple(decoded))


def _workspace_view(workspace: WorkspaceIdentity) -> WorkspaceIdentityView:
    return WorkspaceIdentityView(
        workspace_id=workspace.workspace_id,
        display_name=workspace.display_name,
        created_at=workspace.created_at,
    )


def _device_view(device: PairedDevice) -> PairedDeviceView:
    return PairedDeviceView(
        id=device.id,
        workspace_id=device.workspace_id,
        name=device.name,
        platform=device.platform,
        scopes=_decode_scopes(device.scopes_json),
        created_at=device.created_at,
        last_used_at=device.last_used_at,
        revoked_at=device.revoked_at,
    )


def _device_token_hash(token: str) -> str:
    return hashlib.sha256(f"gunther-device-v1\0{token}".encode()).hexdigest()


def _pairing_code_hash(pairing_id: str, code: str) -> str:
    return hashlib.sha256(f"gunther-pairing-v1\0{pairing_id}\0{code}".encode()).hexdigest()
