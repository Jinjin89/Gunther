"""Run the real service with a test model for the headless desktop journey."""

from fake_models import FakeProvider, agent_replies, cite_search_pool
from fastapi import FastAPI

from gunther.config import Settings
from gunther.main import create_app
from gunther.model_registry import ROLES, clean_provider
from gunther.service_settings import ServiceSettingsStore


def create_smoke_app() -> FastAPI:
    settings = Settings(_env_file=None, auth_token=None)
    provider = clean_provider({
        "kind": "compatible",
        "name": "Browser smoke provider with a long name",
        "baseUrl": "https://fixture.invalid/v1",
        "apiKey": "smoke-test-key",
        "models": [{"id": "smoke", "label": "A model with a long display name"}],
    })
    model_ref = f"{provider['id']}/smoke"
    store = ServiceSettingsStore(settings.service_settings_file)
    store.save_models(
        providers=[provider],
        roles={
            role.id: {"model": model_ref if role.id == "ask" else None, "effort": role.effort}
            for role in ROLES
        },
    )
    fake = FakeProvider(agent_replies(
        cite_search_pool,
        {"action": "search_library", "query": "BRCA1"},
        {"action": "answer"},
    ))
    return create_app(settings, service_settings=store, model_client_factory=fake.factory)
