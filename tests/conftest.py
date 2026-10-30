import pytest


@pytest.fixture(autouse=True)
def no_real_llm_key(monkeypatch):
    """curator.py loads .env when imported, which would put a developer's real ANTHROPIC_API_KEY into the
    environment of every test. Tests that need an LLM pass a fake client explicitly; none may reach the API."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
