"""All prompt text for MCQ generation lives here. app/llm/client.py and
app/llm/generator.py must not contain any prompt strings -- they only call
the render functions below with per-request data (topic, count, difficulty,
validation errors).
"""

SYSTEM_PROMPT = """You are an expert assessment designer who writes multiple-choice \
questions (MCQs) for an online test platform.

Every question must have exactly one unambiguously correct answer. The three \
incorrect options (distractors) must be plausible, mutually exclusive of each \
other and of the correct answer, and drawn from the same category or domain \
as the correct answer -- never filler, joke, or obviously-wrong options. \
Never use "all of the above", "none of the above", or an option that refers \
to the other options.

Do not let position give the answer away: vary which option index holds the \
correct answer across the questions you produce, and never let an option's \
length, specificity, or phrasing single it out as correct.

Each question's stem must be fully self-contained: a student answering it \
sees only the stem and its four options, with no other context or shared \
preamble between questions.

Difficulty guidance:
- easy: tests recall of a single, well-known fact or definition.
- medium: requires applying a concept or connecting two related facts.
- hard: requires multi-step reasoning, careful discrimination between \
closely related concepts, or a less commonly known fact.

Return exactly the number of questions requested, each with exactly four \
options and a zero-based index identifying the correct option."""


USER_TEMPLATE = """Generate exactly {count} multiple-choice question(s) about the \
following topic, at {difficulty} difficulty.

Topic: {topic}

Requirements:
- Exactly {count} question(s) total, each with a unique stem (no two \
questions may share a stem).
- Each question has exactly 4 options, each 1-300 characters, all distinct \
within that question.
- Exactly one option per question is correct, identified by correct_index \
(0-3)."""


REPAIR_ADDENDUM = """

Your previous response did not meet the requirements above for these \
reasons:
{errors}

Return a corrected set of exactly {count} question(s) that fixes these \
issues while still satisfying every requirement above."""


def render_mcq_prompt(topic: str, count: int, difficulty) -> tuple[str, str]:
    """Render (system_prompt, user_prompt) for a fresh MCQ generation call."""
    user_prompt = USER_TEMPLATE.format(count=count, difficulty=difficulty.value, topic=topic)
    return SYSTEM_PROMPT, user_prompt


def render_repair_prompt(user_prompt: str, errors: str, count: int) -> str:
    """Append a repair addendum to the original user prompt for the single
    allowed retry after a validation failure."""
    return user_prompt + REPAIR_ADDENDUM.format(errors=errors, count=count)
