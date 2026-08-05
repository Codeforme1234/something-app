"""MCQGenerator Protocol + fake/OpenAI implementations, chosen once from
Settings. Same lazy-import shape as app.services.email.get_email_sender and
app.auth.dependencies.get_verifier, so a dev box never has to import the
openai package's network stack or hold an API key.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.llm.protocol import MCQGenerator


@lru_cache
def get_mcq_generator() -> MCQGenerator:
    settings = get_settings()
    if settings.llm_mode == "openai":
        from app.llm.generator import OpenAIMCQGenerator

        return OpenAIMCQGenerator()
    # fake mode is only reachable in dev: Settings refuses fake modes in prod
    from app.llm.fake import FakeMCQGenerator

    return FakeMCQGenerator()
