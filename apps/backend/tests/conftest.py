import pytest

from gunther.config import Settings


@pytest.fixture(autouse=True)
def _ignore_the_developers_library_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read a developer's .env or LIBRARY_ROOT.

    A test app pointed at a real library root would claim it for a throwaway
    workspace; tests that need a root pass one explicitly.
    """

    monkeypatch.delenv("LIBRARY_ROOT", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
