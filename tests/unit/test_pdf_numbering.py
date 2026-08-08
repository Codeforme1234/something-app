"""Unit tests for app/pdf/numbering.py and app/pdf/classify.py.

expected_count is what makes "store all the questions irrespective" enforceable,
so the failure mode that matters is INFLATING it: a numbered option list looks
exactly like a bare question number, and counting both would make the pipeline
chase questions that do not exist.
"""

from app.pdf import classify, numbering
from app.pdf.models import PageKind


def test_prefixed_question_numbers_are_read_in_order():
    text = "Q1.\nfirst\nQ2.\nsecond\nQ10.\ntenth"

    assert numbering.find_question_numbers(text) == [1, 2, 10]


def test_numbered_options_do_not_inflate_the_count_when_headers_are_prefixed():
    """`(1) a (2) b` on its own line is an option list, not four questions."""
    text = "Q1.\nPick one:\n1. alpha\n2. beta\n3. gamma\n4. delta\nQ2.\nNext question"

    assert numbering.find_question_numbers(text) == [1, 2]
    assert numbering.expected_count(numbering.find_question_numbers(text)) == 2


def test_bare_numbering_is_used_only_when_nothing_is_prefixed():
    text = "1. first question\n2. second question\n3. third question"

    assert numbering.find_question_numbers(text) == [1, 2, 3]


def test_question_word_form_is_recognised():
    text = "Question 4.\nsomething\nQuestion 5)\nsomething else"

    assert numbering.find_question_numbers(text) == [4, 5]


def test_expected_count_is_distinct_numbers():
    assert numbering.expected_count([1, 2, 2, 3]) == 3


def test_unreadable_numbering_yields_zero_rather_than_raising():
    """Callers treat 0 as "unknown" and skip the count check -- refusing to
    extract would be the one outcome the product rules out."""
    assert numbering.expected_count(numbering.find_question_numbers("no numbers here")) == 0


# --- missing_ranges -----------------------------------------------------------


def test_missing_numbers_are_grouped_into_contiguous_ranges():
    assert numbering.missing_ranges([1, 2, 3, 4, 5, 6], [1, 6]) == [(2, 5)]


def test_separate_gaps_stay_separate():
    assert numbering.missing_ranges([1, 2, 3, 4, 5], [1, 3, 5]) == [(2, 2), (4, 4)]


def test_nothing_missing_is_an_empty_list():
    assert numbering.missing_ranges([1, 2, 3], [1, 2, 3]) == []


def test_extra_extracted_numbers_do_not_create_ranges():
    assert numbering.missing_ranges([1, 2], [1, 2, 99]) == []


# --- page classification ------------------------------------------------------


def test_a_text_dense_page_with_no_figure_is_a_text_page():
    text = "x" * (classify.TEXT_DENSITY_THRESHOLD + 1)

    assert classify.classify_page(text, figure_count=0) is PageKind.text


def test_a_sparse_page_needs_vision():
    text = "x" * (classify.TEXT_DENSITY_THRESHOLD - 1)

    assert classify.classify_page(text, figure_count=0) is PageKind.vision


def test_the_threshold_boundary_is_inclusive_of_text():
    at_threshold = "x" * classify.TEXT_DENSITY_THRESHOLD

    assert classify.classify_page(at_threshold, figure_count=0) is PageKind.text


def test_a_figure_forces_vision_even_on_a_text_dense_page():
    text = "x" * 500

    assert classify.classify_page(text, figure_count=1) is PageKind.vision
