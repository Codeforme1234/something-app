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

import bleach

ALLOWED_TAGS = ["p", "strong", "em", "u", "ul", "ol", "li", "br"]


def sanitize_rich_text(value: str, field_name: str, max_visible_chars: int) -> str:
    cleaned_html = bleach.clean(value, tags=ALLOWED_TAGS, attributes={}, strip=True).strip()
    visible_text = bleach.clean(cleaned_html, tags=[], strip=True).strip()
    if not visible_text:
        raise ValueError(f"{field_name} must not be blank")
    if len(visible_text) > max_visible_chars:
        raise ValueError(f"{field_name} must be at most {max_visible_chars} characters")
    return cleaned_html
