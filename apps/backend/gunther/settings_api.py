"""Service settings, changed from the desktop's Settings page (see service_settings)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from gunther.device_auth import AuthPrincipal
from gunther.schemas import ApiModel
from gunther.service_settings import (
    SERVICE_BY_ID,
    SERVICES,
    Service,
    ServiceSettingsError,
    ServiceSettingsStore,
    describe,
    draft_settings,
)

router = APIRouter()


class ServiceValuesIn(ApiModel):
    # A value to use, or null for the default. Secrets left out stay as they are.
    values: dict[str, Any] = {}


def _owner_only(request: Request) -> None:
    """Keys and endpoints are the desktop owner's; a paired phone never sees them."""

    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, "Service settings can only be changed on the desktop")


def _store(request: Request) -> ServiceSettingsStore:
    return request.app.state.service_settings


def _service(service_id: str) -> Service:
    service = SERVICE_BY_ID.get(service_id)
    if service is None:
        raise HTTPException(404, "There is no such service")
    return service


def _invalid(error: Exception) -> HTTPException:
    if isinstance(error, ServiceSettingsError):
        return HTTPException(422, str(error))
    return HTTPException(422, f"Unknown setting: {error}")


@router.get("/settings/services")
def list_services(request: Request) -> dict[str, Any]:
    _owner_only(request)
    store, base = _store(request), request.app.state.base_settings
    return {
        "persisted": store.persisted,
        "services": [
            describe(service, store, base, request.app.state.models) for service in SERVICES
        ],
    }


@router.put("/settings/services/{service_id}")
def save_service(service_id: str, payload: ServiceValuesIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    service = _service(service_id)
    unknown = set(payload.values) - set(service.keys())
    if unknown:
        raise _invalid(KeyError(", ".join(sorted(unknown))))
    try:
        _store(request).update(payload.values)
    except ServiceSettingsError as error:
        raise _invalid(error) from error
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error
    return describe(
        service, _store(request), request.app.state.base_settings, request.app.state.models
    )


@router.post("/settings/services/{service_id}/test")
async def test_service(
    service_id: str, payload: ServiceValuesIn, request: Request
) -> dict[str, Any]:
    """Try a connection with the values on screen, saved or not."""

    _owner_only(request)
    service = _service(service_id)
    if service.check is None:
        raise HTTPException(409, f"{service.title} has nothing to connect to")
    store, base = _store(request), request.app.state.base_settings
    try:
        settings = draft_settings(service, store, base, payload.values)
    except (ServiceSettingsError, KeyError) as error:
        raise _invalid(error) from error
    result = await service.check(settings)
    entry = store.record_check(service, settings, result)
    return {
        "ok": result.ok,
        "message": result.message,
        "warning": result.warning,
        "checkedAt": entry["checkedAt"],
        "service": describe(service, store, base, request.app.state.models),
    }
