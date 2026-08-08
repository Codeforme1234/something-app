"""Shared sanitizer for rich-text fields (currently: question stems).

The question stem editor is a Tiptap instance that only exposes bold,
italic, underline, and lists -- ALLOWED_TAGS is exactly that set, with no
attributes permitted at all (no style/onclick/href). This is a real trust
boundary, not just UI cleanup: the stem is later rendered with
dangerouslySetInnerHTML on the student take page and the teacher's review
page, so a client that bypasses the editor and posts raw HTML directly to
the API must still come out clean on the other side.

Length is checked against the VISIBLE text (tags stripped), not the raw
HTML, so formatting markup never eats into a teacher's character budget.
"""

import re
from html import unescape

import bleach

ALLOWED_TAGS = ["p", "strong", "em", "u", "ul", "ol", "li", "br"]


def rich_text_to_plain(value: str) -> str:
    """Flatten a sanitized fragment into plain text for an LLM prompt.

    Not the same job as stripping tags: a naive strip turns

        <ul><li>Avoid trick questions</li><li>Use SI units</li></ul>

    into one run-on line, which reads to a model as a single garbled
    instruction. Paragraphs become blank-line-separated blocks and list items
    become "- " lines, so the structure the teacher typed survives into the
    prompt.

    Assumes `value` has already been through sanitize_rich_text, so the only
    tags present are ALLOWED_TAGS.
    """
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|ul|ol)>", "\n\n", text)
    # Opening block tags carry no information once their closers have become
    # newlines, and they must be removed HERE rather than left to bleach: bleach
    # inserts a newline where it strips a block-level tag, and Tiptap nests a <p>
    # inside every <li>, so leaving them would split each bullet from its own
    # text ("-\nAvoid trick questions").
    text = re.sub(r"(?i)<(p|ul|ol)[^>]*>", "", text)
    # Whatever is left is an inline tag with no textual meaning of its own.
    text = bleach.clean(text, tags=[], strip=True)
    text = unescape(text)
    # Collapse the blank-line runs the substitutions above leave behind.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Keep list items tight. Tiptap's <li><p> nesting closes a paragraph inside
    # every item, which would otherwise blank-line-separate a list that the
    # teacher wrote as a single block.
    text = re.sub(r"\n\n+(?=- )", "\n", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def sanitize_rich_text(value: str, field_name: str, max_visible_chars: int) -> str:
    cleaned_html = bleach.clean(value, tags=ALLOWED_TAGS, attributes={}, strip=True).strip()
    visible_text = bleach.clean(cleaned_html, tags=[], strip=True).strip()
    if not visible_text:
        raise ValueError(f"{field_name} must not be blank")
    if len(visible_text) > max_visible_chars:
        raise ValueError(f"{field_name} must be at most {max_visible_chars} characters")
    return cleaned_html
