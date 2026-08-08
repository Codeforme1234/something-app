"""Unit tests for app/pdf/answers.py -- splitting a paper into question blocks
and reading its printed answer key.

The regression that motivates most of this: a naive `[1-4]` search reads the
leading `1` of `Answer Key : 192` as "option 1". On a real JEE paper that turned
15 numerical-value questions into 11 phantom option answers.
"""

from app.pdf import answers

# Shape of a real prep-site answer-key paper: four printed options, then the key.
MCQ_BLOCK = """Q3.
Let a, b be such that the system has no solution. Then b/a is equal to :
(1) -4 (2) 4 (3) 8 (4) -8
MathonGo Answer Key : (2)
"""

# JEE "numerical value" question: no options at all, integer answer.
NUMERIC_BLOCK = """Q25.
If the sum equals 6n^3, then the value is _______.
MathonGo Answer Key : 192
"""


def test_splits_one_block_per_question():
    blocks = answers.split_question_blocks(MCQ_BLOCK + NUMERIC_BLOCK)

    assert [b.number for b in blocks] == [3, 25]


def test_reads_an_option_index_zero_based():
    (block,) = answers.split_question_blocks(MCQ_BLOCK)

    assert block.has_options is True
    assert block.correct_index == 1  # printed "(2)", stored 0-based
    assert block.numeric_answer is None


def test_multi_digit_numeric_answer_is_not_read_as_an_option_index():
    """The core regression: 192 must not become option 1."""
    (block,) = answers.split_question_blocks(NUMERIC_BLOCK)

    assert block.has_options is False
    assert block.correct_index is None
    assert block.numeric_answer == "192"


def test_single_digit_numeric_answer_is_still_not_an_option_index():
    """Even when the value happens to fall in 1-4, a question with no printed
    options cannot have an option answer."""
    (block,) = answers.split_question_blocks("Q21.\nFind the value _______.\nAnswer Key : 4\n")

    assert block.has_options is False
    assert block.correct_index is None
    assert block.numeric_answer == "4"


def test_option_style_question_with_an_out_of_range_key_is_not_trusted():
    text = "Q7.\nPick one:\n(1) a (2) b (3) c (4) d\nAnswer Key : 9\n"
    (block,) = answers.split_question_blocks(text)

    assert block.correct_index is None
    assert block.numeric_answer == "9"


def test_the_two_maps_never_overlap():
    blocks = answers.split_question_blocks(MCQ_BLOCK + NUMERIC_BLOCK)
    option_map = answers.answer_index_map(blocks)
    numeric_map = answers.numeric_answer_map(blocks)

    assert option_map == {3: 1}
    assert numeric_map == {25: "192"}
    assert not set(option_map) & set(numeric_map)


def test_answer_line_variants_are_all_recognised():
    for line in ("Answer Key : (2)", "Answer key- 2", "Answer: (2)", "MathonGo Answer Key : (2)"):
        text = f"Q1.\nPick one:\n(1) a (2) b (3) c (4) d\n{line}\n"
        (block,) = answers.split_question_blocks(text)
        assert block.correct_index == 1, line


def test_a_question_with_no_answer_line_reports_neither_answer():
    (block,) = answers.split_question_blocks("Q9.\nPick one:\n(1) a (2) b (3) c (4) d\n")

    assert block.correct_index is None
    assert block.numeric_answer is None


def test_an_answer_belongs_to_the_question_it_follows():
    """Blocks are sliced at the next header, so a key can never bleed backwards
    into the previous question."""
    blocks = answers.split_question_blocks(NUMERIC_BLOCK + MCQ_BLOCK)
    by_number = {b.number: b for b in blocks}

    assert by_number[25].numeric_answer == "192"
    assert by_number[3].correct_index == 1


def test_question_headers_carry_their_line_index():
    headers = answers.find_question_headers("intro\nQ4.\nsome text\nQ5.\n")

    assert headers == [(4, 1), (5, 3)]


# --- looks_like_answer_line, which protects these lines from boilerplate removal


def test_an_answer_annotation_is_recognised_as_one():
    assert answers.looks_like_answer_line("MathonGo Answer Key : (3)") is True


def test_a_stem_mentioning_answers_is_not_an_answer_line():
    stem = "Which of the following answers the question about 3 moles of gas at STP?"

    assert answers.looks_like_answer_line(stem) is False


def test_a_blank_line_is_not_an_answer_line():
    assert answers.looks_like_answer_line("   ") is False
