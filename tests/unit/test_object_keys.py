import pytest

from app.services.storage.keys import (
    CONTENT_TYPE_EXTENSIONS,
    belongs_to_test,
    content_type_for_key,
    new_question_image_key,
    question_image_prefix,
)


@pytest.mark.parametrize(("content_type", "extension"), CONTENT_TYPE_EXTENSIONS.items())
def test_new_question_image_key_uses_the_right_extension(content_type, extension):
    key = new_question_image_key("test-1", content_type)
    assert key.startswith(question_image_prefix("test-1"))
    assert key.endswith(f".{extension}")


def test_new_question_image_key_rejects_an_unsupported_content_type():
    with pytest.raises(ValueError, match="unsupported image content type"):
        new_question_image_key("test-1", "image/svg+xml")


def test_belongs_to_test_accepts_a_freshly_minted_key():
    key = new_question_image_key("test-1", "image/png")
    assert belongs_to_test(key, "test-1")


def test_belongs_to_test_rejects_a_different_test_id():
    key = new_question_image_key("test-1", "image/png")
    assert not belongs_to_test(key, "test-2")


def test_belongs_to_test_rejects_path_traversal():
    ulid = new_question_image_key("test-1", "image/png").rsplit("/", 1)[-1]
    traversal_key = f"tests/test-1/q/../../../etc/{ulid}"
    assert not belongs_to_test(traversal_key, "test-1")


def test_belongs_to_test_rejects_an_absolute_path():
    ulid = new_question_image_key("test-1", "image/png").rsplit("/", 1)[-1]
    assert not belongs_to_test(f"/tests/test-1/q/{ulid}", "test-1")


def test_belongs_to_test_rejects_an_unsupported_extension():
    ulid = new_question_image_key("test-1", "image/png").rsplit("/", 1)[-1].removesuffix(".png")
    assert not belongs_to_test(f"tests/test-1/q/{ulid}.svg", "test-1")


def test_belongs_to_test_rejects_a_double_extension():
    ulid = new_question_image_key("test-1", "image/png").rsplit("/", 1)[-1].removesuffix(".png")
    assert not belongs_to_test(f"tests/test-1/q/{ulid}.png.html", "test-1")


def test_belongs_to_test_rejects_an_empty_tail():
    assert not belongs_to_test(question_image_prefix("test-1"), "test-1")


def test_belongs_to_test_rejects_a_lowercase_ulid():
    key = new_question_image_key("test-1", "image/png")
    lowercased = key.lower()
    assert not belongs_to_test(lowercased, "test-1")


def test_belongs_to_test_rejects_a_query_string():
    key = new_question_image_key("test-1", "image/png")
    assert not belongs_to_test(f"{key}?x=1", "test-1")


@pytest.mark.parametrize(("content_type", "extension"), CONTENT_TYPE_EXTENSIONS.items())
def test_content_type_for_key_round_trips(content_type, extension):
    key = new_question_image_key("test-1", content_type)
    assert content_type_for_key(key) == content_type


def test_content_type_for_key_rejects_an_unknown_extension():
    with pytest.raises(ValueError, match="unknown image extension"):
        content_type_for_key("tests/test-1/q/some-file.svg")
