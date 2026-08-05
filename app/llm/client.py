"""Thin wrapper building the OpenAI SDK client from Settings. No prompt text
lives here -- see app/llm/prompts/.
"""

from functools import lru_cache

from openai import OpenAI

from app.core.config import get_settings

_TIMEOUT_SECONDS = 60.0
_MAX_RETRIES = 1


@lru_cache
def get_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=_TIMEOUT_SECONDS,
        max_retries=_MAX_RETRIES,
    )
