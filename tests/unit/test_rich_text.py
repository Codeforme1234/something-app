import pytest

from app.core.rich_text import rich_text_to_plain, sanitize_rich_text


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


# --- rich_text_to_plain: what a model actually receives ----------------------
#
# A naive tag strip would flatten a bulleted list into one run-on instruction,
# which reads to a model as a single garbled sentence.


def test_a_bulleted_list_becomes_dash_lines():
    html = "<ul><li>Avoid trick questions</li><li>Use SI units</li></ul>"

    assert rich_text_to_plain(html) == "- Avoid trick questions\n- Use SI units"


def test_tiptaps_nested_paragraph_inside_a_list_item_stays_on_one_line():
    """Tiptap emits <li><p>text</p></li>. bleach inserts a newline where it
    strips a block tag, so leaving the <p> to bleach would split every bullet
    from its own text ("-\\nAvoid trick questions")."""
    html = "<ul><li><p>Avoid trick questions</p></li><li><p>Use SI units</p></li></ul>"

    assert rich_text_to_plain(html) == "- Avoid trick questions\n- Use SI units"


def test_paragraphs_are_separated_by_a_blank_line():
    html = "<p>First rule.</p><p>Second rule.</p>"

    assert rich_text_to_plain(html) == "First rule.\n\nSecond rule."


def test_a_mixed_document_keeps_its_structure():
    html = "<p>Rules:</p><ul><li><p>No trick questions</p></li><li><p>SI units</p></li></ul><p>Keep stems short.</p>"

    assert rich_text_to_plain(html) == (
        "Rules:\n- No trick questions\n- SI units\n\nKeep stems short."
    )


def test_inline_marks_survive_as_their_text():
    assert rich_text_to_plain("<p>Use <strong>SI</strong> units</p>") == "Use SI units"


def test_a_line_break_becomes_a_newline():
    assert rich_text_to_plain("<p>a<br>b</p>") == "a\nb"


def test_entities_are_decoded_so_the_model_sees_real_characters():
    assert rich_text_to_plain("<p>a &lt; b &amp; c</p>") == "a < b & c"


def test_an_empty_editor_flattens_to_nothing():
    """Tiptap posts "<p></p>" for an untouched editor, which optional
    rich-text fields treat as absent."""
    assert rich_text_to_plain("<p></p>") == ""
