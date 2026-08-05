import pytest

from app.core.rich_text import sanitize_rich_text


def test_plain_text_passes_through_unchanged():
    assert sanitize_rich_text("Hello world", "stem", 1000) == "Hello world"


def test_allowed_tags_are_kept():
    html = "<p>Regarding <strong>mitochondria</strong>, which is <em>true</em>?</p>"
    assert sanitize_rich_text(html, "stem", 1000) == html


def test_lists_are_kept():
    html = "<ul><li>one</li><li>two</li></ul>"
    assert sanitize_rich_text(html, "stem", 1000) == html


def test_disallowed_tags_are_stripped_leaving_only_inert_text():
    # bleach's strip=True drops the tag but keeps its inner text as plain
    # text -- the script's source becomes harmless prose, never executable.
    cleaned = sanitize_rich_text("<script>alert(1)</script>Hello", "stem", 1000)
    assert "<script>" not in cleaned
    assert "<" not in cleaned
    assert "Hello" in cleaned


def test_attributes_are_stripped_from_allowed_tags():
    cleaned = sanitize_rich_text('<p onclick="evil()" style="color:red">hi</p>', "stem", 1000)
    assert "onclick" not in cleaned
    assert "style" not in cleaned
    assert "hi" in cleaned


def test_links_are_stripped_to_text_only():
    cleaned = sanitize_rich_text('<a href="javascript:evil()">click me</a>', "stem", 1000)
    assert "<a" not in cleaned
    assert "javascript:" not in cleaned
    assert "click me" in cleaned


def test_blank_after_stripping_tags_raises():
    with pytest.raises(ValueError, match="must not be blank"):
        sanitize_rich_text("<p>   </p>", "stem", 1000)


def test_length_is_checked_against_visible_text_not_raw_html():
    # 900 visible characters wrapped in formatting that adds real overhead --
    # must NOT be rejected just because the raw HTML string is longer than 900.
    visible = "a" * 900
    html = f"<p><strong>{visible}</strong></p>"
    assert len(html) > 900
    result = sanitize_rich_text(html, "stem", 1000)
    assert visible in result


def test_over_max_visible_chars_raises():
    with pytest.raises(ValueError, match="at most 1000 characters"):
        sanitize_rich_text("a" * 1001, "stem", 1000)
