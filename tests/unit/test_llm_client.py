"""Unit tests for app/llm/client.py.

Timeout and max_retries were module constants until they moved to Settings.
These assert the values actually reach the SDK client, by reading them back off
a real `OpenAI` instance rather than off a mock's call kwargs -- a mock would
still pass if the constructor silently ignored the arguments.
"""

import pytest

from app.core.config import Settings
from app.llm import client as client_module


@pytest.fixture(autouse=True)
def _clear_client_cache():
    client_module.get_openai_client.cache_clear()
    yield
    client_module.get_openai_client.cache_clear()


def _settings(**overrides) -> Settings:
    fields = {
        "table_name": "quizdeck-test",
        "s3_bucket": "quizdeck-media-test",
        "openai_api_key": "sk-unit-test",
    } | overrides
    return Settings(_env_file=None, **fields)


def test_timeout_and_retries_come_from_settings(monkeypatch):
    monkeypatch.setattr(
        client_module,
        "get_settings",
        lambda: _settings(openai_timeout_seconds=12.5, openai_max_retries=4),
    )

    built = client_module.get_openai_client()

    assert built.timeout == 12.5
    assert built.max_retries == 4


def test_the_defaults_are_the_old_module_constants(monkeypatch):
    """Moving these into Settings must not have changed behaviour for anyone
    who does not set them."""
    monkeypatch.setattr(client_module, "get_settings", _settings)

    built = client_module.get_openai_client()

    assert built.timeout == 60.0
    assert built.max_retries == 1
