"""Access to the desktop owner's settings at the HTTP boundary."""

from fastapi import HTTPException, Request

from gunther.device_auth import AuthPrincipal
from gunther.service_settings import ServiceSettingsStore


def require_desktop_owner(request: Request, detail: str) -> None:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, detail)


def service_settings(request: Request) -> ServiceSettingsStore:
    return request.app.state.service_settings
