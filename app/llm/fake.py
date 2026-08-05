"""Dev-only MCQ generator: deterministic, no network, no API key. Incorporates
the topic string into every stem/option so the UI demo reads sensibly, and
always returns output that passes app.llm.schemas validation (unique options
per question, no duplicate stems across the set, correct_index varying).
"""

from app.llm.schemas import GeneratedMCQ
from app.models.test import Difficulty

_OPTION_COUNT = 4


class FakeMCQGenerator:
    def generate(
        self,
        topic: str,
        count: int,
        difficulty: Difficulty,
        knowledge_base: str | None = None,
    ) -> list[GeneratedMCQ]:
        clean_topic = topic.strip() or "the topic"
        # Visibly reflects the flag in dev so a tester can confirm an upload
        # actually reached the generator, without needing a real LLM call.
        source_note = " (from your uploaded material)" if knowledge_base else ""
        questions: list[GeneratedMCQ] = []
        for i in range(count):
            n = i + 1
            correct = i % _OPTION_COUNT
            options = [f"{clean_topic}: distractor {n}.{j}" for j in range(_OPTION_COUNT)]
            options[correct] = f"{clean_topic}: the correct fact #{n}"
            questions.append(
                GeneratedMCQ(
                    stem=(
                        f"[{difficulty.value}] Regarding {clean_topic}{source_note}, "
                        f"which statement #{n} is correct?"
                    ),
                    options=options,
                    correct_index=correct,
                )
            )
        return questions
