"""Unit tests for OpenAIMCQGenerator's repair-retry logic. The OpenAI client
is mocked throughout -- these tests never call the real OpenAI API (no key
exists in this environment, and LLM_MODE defaults to fake)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest
from pydantic import ValidationError

from app.core.exceptions import UpstreamError
from app.llm.generator import OpenAIMCQGenerator
from app.llm.schemas import GeneratedMCQ, GeneratedMCQSet
from app.models.test import Difficulty


def _mcq(stem: str, correct_index: int = 0) -> GeneratedMCQ:
    return GeneratedMCQ(stem=stem, options=["A", "B", "C", "D"], correct_index=correct_index)


def _valid_set(count: int) -> GeneratedMCQSet:
    return GeneratedMCQSet(questions=[_mcq(f"Question {i}?") for i in range(count)])


def _completion(parsed) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))])


def _duplicate_stem_validation_error() -> ValidationError:
    try:
        GeneratedMCQSet(questions=[_mcq("Same?"), _mcq("Same?")])
    except ValidationError as e:
        return e
    raise AssertionError("expected GeneratedMCQSet construction to fail")


def _generator_with(mock_parse: MagicMock) -> OpenAIMCQGenerator:
    client = MagicMock()
    client.chat.completions.parse = mock_parse
    return OpenAIMCQGenerator(client=client, model="gpt-test")


def test_repair_retry_succeeds_after_one_invalid_attempt():
    # First attempt is schema-valid but the wrong count for a count=3 request.
    mock_parse = MagicMock(side_effect=[_completion(_valid_set(2)), _completion(_valid_set(3))])
    generator = _generator_with(mock_parse)

    questions = generator.generate("topic", 3, Difficulty.medium)

    assert len(questions) == 3
    assert mock_parse.call_count == 2


def test_repair_retry_appends_validation_errors_to_the_user_message():
    mock_parse = MagicMock(side_effect=[_completion(_valid_set(2)), _completion(_valid_set(3))])
    generator = _generator_with(mock_parse)

    generator.generate("topic", 3, Difficulty.medium)

    first_messages = mock_parse.call_args_list[0].kwargs["messages"]
    second_messages = mock_parse.call_args_list[1].kwargs["messages"]
    assert len(first_messages) == 2
    assert len(second_messages) == 2  # still system + a single (repaired) user message
    assert second_messages[1]["content"].startswith(first_messages[1]["content"])
    assert "expected exactly 3" in second_messages[1]["content"]


def test_both_attempts_invalid_raises_upstream_error():
    mock_parse = MagicMock(side_effect=[_completion(_valid_set(2)), _completion(_valid_set(2))])
    generator = _generator_with(mock_parse)

    with pytest.raises(UpstreamError):
        generator.generate("topic", 3, Difficulty.medium)
    assert mock_parse.call_count == 2


def test_pydantic_validation_error_from_parse_triggers_repair_retry():
    mock_parse = MagicMock(
        side_effect=[_duplicate_stem_validation_error(), _completion(_valid_set(3))]
    )
    generator = _generator_with(mock_parse)

    questions = generator.generate("topic", 3, Difficulty.medium)
    assert len(questions) == 3


def test_refusal_with_no_parsed_content_triggers_repair_retry():
    mock_parse = MagicMock(side_effect=[_completion(None), _completion(_valid_set(3))])
    generator = _generator_with(mock_parse)

    questions = generator.generate("topic", 3, Difficulty.medium)
    assert len(questions) == 3


def test_sdk_exception_raises_upstream_error_without_retry():
    mock_parse = MagicMock(side_effect=openai.APIConnectionError(request=MagicMock()))
    generator = _generator_with(mock_parse)

    with pytest.raises(UpstreamError):
        generator.generate("topic", 3, Difficulty.medium)
    assert mock_parse.call_count == 1


def test_sdk_exception_on_repair_retry_raises_upstream_error():
    mock_parse = MagicMock(
        side_effect=[_completion(_valid_set(2)), openai.APIConnectionError(request=MagicMock())]
    )
    generator = _generator_with(mock_parse)

    with pytest.raises(UpstreamError):
        generator.generate("topic", 3, Difficulty.medium)
    assert mock_parse.call_count == 2
