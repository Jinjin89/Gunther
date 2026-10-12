import pytest

from gunther.config import Settings


@pytest.fixture(autouse=True)
def _ignore_the_developers_library_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read a developer's .env or LIBRARY_ROOT, and run keyword-only.

    A test app pointed at a real library root would claim it for a throwaway
    workspace; tests that need a root pass one explicitly.
    """

    monkeypatch.delenv("LIBRARY_ROOT", raising=False)
    # Loading the real embedding model is slow; tests that want it opt in.
    monkeypatch.setenv("SEMANTIC_SEARCH", "false")
    # Live pieces are cut at pauses with a model file a checkout may or may not have;
    # tests that want it opt in, the rest hear fixed blocks.
    monkeypatch.setattr("gunther.vad.default_model_path", lambda: None)
    # A developer's multi-GB parser bundle must not turn unit fixtures into
    # subprocess integration tests. Parser integration tests opt in explicitly.
    monkeypatch.setattr("gunther.document_parser.DoclingParser.bundled", lambda: None)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
