"""Thin wrapper building the OpenAI SDK client from Settings. No prompt text
lives here -- see app/llm/prompts/.

Timeout and retry count come from Settings rather than module constants: they
are the two knobs you reach for during an incident, and baking them in means a
deploy to change either.
"""

from functools import lru_cache

from openai import OpenAI

from app.core.config import get_settings


@lru_cache
def get_openai_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
