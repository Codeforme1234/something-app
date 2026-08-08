"""Real feedback generator: calls OpenAI's structured-output parse API so the
response is schema-conformant by construction, then runs it through the same
validation layer as the fake generator (app.llm.feedback_schemas). A single
"repair retry" is allowed when that validation layer rejects the output --
the validation errors are appended to the user prompt and the model gets one
more try. No prompt text lives here -- see app/llm/prompts/feedback_generation.py.

Mirrors app/llm/generator.py's shape exactly; see that module for the
reasoning behind the two-attempt structure.
"""

import logging

import openai
import pydantic
from openai import OpenAI

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.llm.client import get_openai_client
from app.llm.feedback_schemas import FeedbackInput, GeneratedFeedback, GeneratedFeedbackWire
from app.llm.prompts.feedback_generation import render_feedback_prompt, render_repair_prompt

logger = logging.getLogger(__name__)


class _ValidationFailed(Exception):
    """Internal signal that the parsed output failed our validation layer
    (as opposed to an SDK/network failure) -- triggers the one allowed
    repair retry rather than an immediate UpstreamError."""


class OpenAIFeedbackGenerator:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        settings = get_settings()
        self._client = client if client is not None else get_openai_client()
        # Settings guarantees this in LLM_MODE=openai, which is the only mode
        # that constructs this class; the assert is for a direct instantiation
        # in a test or script, so the failure names the missing var.
        resolved = model or settings.openai_model
        assert resolved, "LLM_MODE=openai requires OPENAI_MODEL"
        self._model = resolved

    def generate(self, input: FeedbackInput) -> GeneratedFeedback:
        system_prompt, user_prompt = render_feedback_prompt(input)

        try:
            return self._attempt(system_prompt, user_prompt)
        except _ValidationFailed as first_failure:
            logger.warning(
                "feedback generation failed validation on first attempt (test=%r): %s",
                input.test_title,
                first_failure,
            )
            repair_prompt = render_repair_prompt(user_prompt, str(first_failure))
            try:
                return self._attempt(system_prompt, repair_prompt)
            except _ValidationFailed as second_failure:
                logger.error(
                    "feedback generation failed validation after repair retry (test=%r): %s",
                    input.test_title,
                    second_failure,
                )
                raise UpstreamError("feedback generation failed") from second_failure
            except openai.OpenAIError as sdk_error:
                logger.error(
                    "feedback generation SDK error on repair retry (test=%r): %s",
                    input.test_title,
                    sdk_error,
                )
                raise UpstreamError("feedback generation failed") from sdk_error
        except openai.OpenAIError as sdk_error:
            logger.error("feedback generation SDK error (test=%r): %s", input.test_title, sdk_error)
            raise UpstreamError("feedback generation failed") from sdk_error

    def _attempt(self, system_prompt: str, user_prompt: str) -> GeneratedFeedback:
        """One call + validate cycle. Raises _ValidationFailed for anything
        our validation layer rejects; lets openai.OpenAIError propagate
        untouched so the caller can tell the two failure modes apart."""
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                # The wire model carries structure only -- the strict caps are
                # applied below, because constraint keywords in the schema
                # would be rejected by OpenAI's strict mode on some models.
                response_format=GeneratedFeedbackWire,
            )
        except pydantic.ValidationError as e:
            # Structurally malformed output (missing keys, wrong types).
            raise _ValidationFailed(str(e)) from e

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise _ValidationFailed("model returned no parseable content (possible refusal)")

        try:
            return GeneratedFeedback.model_validate(parsed.model_dump())
        except pydantic.ValidationError as e:
            raise _ValidationFailed(str(e)) from e
