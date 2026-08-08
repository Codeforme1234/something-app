"""MCQGenerator Protocol + fake/OpenAI implementations, chosen once from
Settings. Same lazy-import shape as app.services.email.get_email_sender and
app.auth.dependencies.get_verifier, so a dev box never has to import the
openai package's network stack or hold an API key.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.llm.protocol import FeedbackGenerator, MCQGenerator, QuestionExtractor


@lru_cache
def get_mcq_generator() -> MCQGenerator:
    settings = get_settings()
    if settings.llm_mode == "openai":
        from app.llm.generator import OpenAIMCQGenerator

        return OpenAIMCQGenerator()
    # fake mode is only reachable in dev: Settings refuses fake modes in prod
    from app.llm.fake import FakeMCQGenerator

    return FakeMCQGenerator()


@lru_cache
def get_question_extractor() -> QuestionExtractor:
    """Reuses `llm_mode` rather than adding a switch of its own: it already means
    "are we talking to a real model", both implementations need the same API key,
    and Settings already refuses llm_mode=fake in prod -- so extraction inherits
    that guarantee with no new validator branch, and the nonsensical
    real-generator/fake-extractor combination stays unreachable."""
    settings = get_settings()
    if settings.llm_mode == "openai":
        from app.llm.extractor import OpenAIQuestionExtractor

        return OpenAIQuestionExtractor()
    # fake mode is only reachable in dev: Settings refuses fake modes in prod
    from app.llm.fake_extractor import FakeQuestionExtractor

    return FakeQuestionExtractor()


@lru_cache
def get_feedback_generator() -> FeedbackGenerator:
    """Reuses `llm_mode` rather than adding a switch of its own -- same
    reasoning as get_question_extractor: it already means "are we talking to
    a real model", both implementations need the same API key, and Settings
    already refuses llm_mode=fake in prod, so feedback generation inherits
    that guarantee with no new validator branch."""
    settings = get_settings()
    if settings.llm_mode == "openai":
        from app.llm.feedback import OpenAIFeedbackGenerator

        return OpenAIFeedbackGenerator()
    # fake mode is only reachable in dev: Settings refuses fake modes in prod
    from app.llm.fake_feedback import FakeFeedbackGenerator

    return FakeFeedbackGenerator()
