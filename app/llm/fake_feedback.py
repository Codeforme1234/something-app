"""Dev-only feedback generator: deterministic, no network, no API key. Mirrors
app.llm.fake.FakeMCQGenerator -- always returns output that passes
app.llm.feedback_schemas.GeneratedFeedback validation, for every mix of
correct/wrong/unanswered results a real attempt can produce (all-correct,
all-wrong, all-unanswered, and mixed), including the edge cases where a list
is legitimately empty (a perfect score has no improvement_areas; an all-wrong
attempt has no strengths).

Has no real topic-inference or subject-matter knowledge to draw on -- unlike
the real generator, it derives a "topic" mechanically (the first few words of
a question's stem) rather than by understanding the material. That is enough
to produce schema-valid, input-derived, deterministic output; it must not be
mistaken for a stand-in on quality.
"""

from app.llm.feedback_schemas import (
    FeedbackInput,
    FeedbackQuestionResult,
    GeneratedFeedback,
    GeneratedImprovementArea,
    GeneratedTopicMastery,
)

#: How many words of a stem become its mechanical "topic" -- just enough to
#: read as a topic name once title-cased, not a restated question.
_TOPIC_WORDS = 3
#: Keeps every list short and specific, matching the strict schema's own caps.
_MAX_STRENGTHS = 5
_MAX_IMPROVEMENT_AREAS = 5
#: Leaves room for one exam-technique line under the schema's cap of 6.
_MAX_STUDY_TOPICS = 5
#: A rushed attempt: less than this fraction of the allowed time used.
_RUSHED_TIME_FRACTION = 0.4


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _topic_for(stem: str) -> str:
    words = stem.strip().split()[:_TOPIC_WORDS]
    topic = " ".join(w.capitalize() for w in words)
    return _truncate(topic, 100) or "General"


def _is_correct(result: FeedbackQuestionResult) -> bool:
    return result.chosen_index is not None and result.chosen_index == result.correct_index


class FakeFeedbackGenerator:
    def generate(self, input: FeedbackInput) -> GeneratedFeedback:
        results = input.results
        topics = {r.order: _topic_for(r.stem) for r in results}

        # Plain dict, not a set: insertion order must stay deterministic (a
        # set's iteration order depends on hashing, not first-seen order),
        # and this dict IS the tally, so there's no separate mutation step.
        tallies: dict[str, list[int]] = {}
        for r in results:
            topic = topics[r.order]
            if topic not in tallies:
                tallies[topic] = [0, 0]
            tallies[topic][1] += 1
            if _is_correct(r):
                tallies[topic][0] += 1
        topic_breakdown = [
            GeneratedTopicMastery(topic=t, correct=c, total=total)
            for t, (c, total) in list(tallies.items())[:6]
        ]

        strengths: list[str] = []
        seen_strength_topics: list[str] = []
        for r in results:
            if not _is_correct(r):
                continue
            topic = topics[r.order]
            if topic in seen_strength_topics:
                continue
            seen_strength_topics.append(topic)
            strengths.append(topic)
            if len(strengths) >= _MAX_STRENGTHS:
                break

        improvement_areas: list[GeneratedImprovementArea] = []
        wrong_topics: list[str] = []
        for r in results:
            if _is_correct(r):
                continue
            topic = topics[r.order]
            if topic in wrong_topics:
                continue
            wrong_topics.append(topic)
            if r.chosen_index is None:
                diagnosis = (
                    f'Left this "{topic}" question unanswered; the correct choice was '
                    f'"{_truncate(r.options[r.correct_index], 100)}".'
                )
            else:
                diagnosis = (
                    f'Chose "{_truncate(r.options[r.chosen_index], 100)}" over the correct '
                    f'"{_truncate(r.options[r.correct_index], 100)}" on "{topic}".'
                )
            improvement_areas.append(
                GeneratedImprovementArea(
                    topic=topic,
                    diagnosis=diagnosis,
                    action=f"Review {topic} and retry similar questions.",
                )
            )
            if len(improvement_areas) >= _MAX_IMPROVEMENT_AREAS:
                break

        study_plan = [f"Revisit {t}." for t in wrong_topics[:_MAX_STUDY_TOPICS]]
        any_unanswered = any(r.chosen_index is None for r in results)
        rushed = (
            input.elapsed_seconds is not None
            and input.duration_seconds > 0
            and input.elapsed_seconds < input.duration_seconds * _RUSHED_TIME_FRACTION
        )
        if any_unanswered or rushed:
            study_plan.append(
                "Attempt every question and pace yourself -- an unanswered question scores the "
                "same as a wrong one."
            )
        study_plan = study_plan[:6]

        summary = (
            f'You scored {input.score}% on "{input.test_title}" '
            f"({input.correct_count}/{input.total_questions} correct)."
        )

        return GeneratedFeedback(
            summary=summary,
            strengths=strengths,
            improvement_areas=improvement_areas,
            study_plan=study_plan,
            topic_breakdown=topic_breakdown,
        )
