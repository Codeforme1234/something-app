"""Real MCQ generator: calls OpenAI's structured-output parse API so the
response is schema-conformant by construction, then runs it through the same
validation layer as every other generator (app.llm.schemas). A single
"repair retry" is allowed when that validation layer rejects the output --
the validation errors are appended to the user prompt and the model gets one
more try. No prompt text lives here -- see app/llm/prompts/.
"""

import logging

import openai
import pydantic
from openai import OpenAI

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.llm.client import get_openai_client
from app.llm.prompts.mcq_generation import render_mcq_prompt, render_repair_prompt
from app.llm.schemas import GeneratedMCQ, GeneratedMCQSet, GeneratedMCQSetWire
from app.models.test import Difficulty

logger = logging.getLogger(__name__)


class _ValidationFailed(Exception):
    """Internal signal that the parsed output failed our validation layer
    (as opposed to an SDK/network failure) -- triggers the one allowed
    repair retry rather than an immediate UpstreamError."""


class OpenAIMCQGenerator:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        settings = get_settings()
        self._client = client if client is not None else get_openai_client()
        self._model = model or settings.openai_model

    def generate(self, topic: str, count: int, difficulty: Difficulty) -> list[GeneratedMCQ]:
        system_prompt, user_prompt = render_mcq_prompt(topic, count, difficulty)

        try:
            return self._attempt(system_prompt, user_prompt, count)
        except _ValidationFailed as first_failure:
            logger.warning(
                "mcq generation failed validation on first attempt (topic=%r, count=%d): %s",
                topic,
                count,
                first_failure,
            )
            repair_prompt = render_repair_prompt(user_prompt, str(first_failure), count)
            try:
                return self._attempt(system_prompt, repair_prompt, count)
            except _ValidationFailed as second_failure:
                logger.error(
                    "mcq generation failed validation after repair retry (topic=%r, count=%d): %s",
                    topic,
                    count,
                    second_failure,
                )
                raise UpstreamError("question generation failed, please try again") from second_failure
            except openai.OpenAIError as sdk_error:
                logger.error(
                    "mcq generation SDK error on repair retry (topic=%r, count=%d): %s",
                    topic,
                    count,
                    sdk_error,
                )
                raise UpstreamError("question generation failed, please try again") from sdk_error
        except openai.OpenAIError as sdk_error:
            logger.error(
                "mcq generation SDK error (topic=%r, count=%d): %s", topic, count, sdk_error
            )
            raise UpstreamError("question generation failed, please try again") from sdk_error

    def _attempt(self, system_prompt: str, user_prompt: str, count: int) -> list[GeneratedMCQ]:
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
                # The wire model carries structure only — the strict caps are
                # applied below, because constraint keywords in the schema
                # would be rejected by OpenAI's strict mode on some models.
                response_format=GeneratedMCQSetWire,
            )
        except pydantic.ValidationError as e:
            # Structurally malformed output (missing keys, wrong types).
            raise _ValidationFailed(str(e)) from e

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise _ValidationFailed("model returned no parseable content (possible refusal)")

        try:
            strict = GeneratedMCQSet.model_validate(parsed.model_dump())
            strict.validate_count(count)
        except (pydantic.ValidationError, ValueError) as e:
            raise _ValidationFailed(str(e)) from e

        return strict.questions
