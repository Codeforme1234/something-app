"""OpenAI-backed question extraction from a PDF's text and page images.

Same shape as app/llm/generator.py: a private _ValidationFailed sentinel, one
repair retry, and every OpenAI error surfaced as UpstreamError. Prompt text
lives in app/llm/prompts/question_extraction.py (CLAUDE.md).

One deliberate difference from the generator: the per-request timeout.
OPENAI_TIMEOUT_SECONDS defaults to 60s, which is right for generating 20
questions and far too short for extracting 75 in a single call -- that would
fail on every real paper, deterministically. So this class overrides it per
request with OPENAI_EXTRACTION_TIMEOUT_SECONDS, rather than raising the shared
value for calls that do not need it.
"""

import base64
import logging
from collections.abc import Sequence

import openai
from openai import OpenAI

from app.core.config import get_settings
from app.core.exceptions import UpstreamError
from app.llm.client import get_openai_client
from app.llm.extraction_schemas import ExtractedQuestion, ExtractedQuestionSet, ExtractedQuestionSetWire
from app.llm.prompts.question_extraction import (
    render_extraction_prompt,
    render_extraction_repair_prompt,
    render_vision_prompt,
)

logger = logging.getLogger(__name__)


class _ValidationFailed(Exception):
    """The model answered, but not in a shape we can use. Repairable."""

    def __init__(self, errors: str):
        self.errors = errors
        super().__init__(errors)


class OpenAIQuestionExtractor:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        settings = get_settings()
        self._client = client or get_openai_client()
        # See the note in generator.py: Settings enforces this for the real
        # code path, the assert only covers direct instantiation.
        resolved = model or settings.openai_extraction_model
        assert resolved, "LLM_MODE=openai requires OPENAI_EXTRACTION_MODEL"
        self._model = resolved
        self._timeout = settings.openai_extraction_timeout_seconds

    def transcribe_page(self, page_png: bytes, page_number: int) -> str:
        """Read one rendered page, returning plain text with figures described.

        Free-form output on purpose: the transcription flows back into the
        document that the structured extraction call then reads.
        """
        system_prompt, user_prompt = render_vision_prompt(page_number)
        encoded = base64.b64encode(page_png).decode("ascii")
        try:
            completion = self._client.with_options(timeout=self._timeout).chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                        ],
                    },
                ],
            )
        except openai.OpenAIError as exc:
            logger.warning("vision transcription failed for page %d: %s", page_number, exc)
            raise UpstreamError("could not read a page of that PDF, please try again") from exc

        return completion.choices[0].message.content or ""

    def extract(
        self,
        document_text: str,
        *,
        expected_count: int,
        instruction: str | None = None,
        answer_key: str | None = None,
        only_numbers: Sequence[int] | None = None,
    ) -> list[ExtractedQuestion]:
        system_prompt, user_prompt = render_extraction_prompt(
            document_text,
            expected_count=expected_count,
            instruction=instruction,
            answer_key=answer_key,
            only_numbers=only_numbers,
        )

        try:
            return self._attempt(system_prompt, user_prompt)
        except _ValidationFailed as first_failure:
            logger.warning("extraction validation failed, repairing: %s", first_failure.errors)
            repaired = render_extraction_repair_prompt(user_prompt, first_failure.errors)
            try:
                return self._attempt(system_prompt, repaired)
            except _ValidationFailed as second_failure:
                logger.warning("extraction repair also failed: %s", second_failure.errors)
                raise UpstreamError("could not read questions from that PDF, please try again") from second_failure
            except openai.OpenAIError as exc:
                raise UpstreamError("the AI service is unavailable, please try again") from exc
        except openai.OpenAIError as exc:
            # No app-level retry on a transport failure; the SDK already retried.
            logger.warning("extraction call failed: %s", exc)
            raise UpstreamError("the AI service is unavailable, please try again") from exc

    def _attempt(self, system_prompt: str, user_prompt: str) -> list[ExtractedQuestion]:
        completion = self._client.with_options(timeout=self._timeout).chat.completions.parse(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ExtractedQuestionSetWire,
        )

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise _ValidationFailed("model returned no parseable content (possible refusal)")

        try:
            strict = ExtractedQuestionSet.model_validate(parsed.model_dump())
        except Exception as exc:
            raise _ValidationFailed(str(exc)) from exc

        if not strict.questions:
            raise _ValidationFailed("returned an empty question list")
        return strict.questions
