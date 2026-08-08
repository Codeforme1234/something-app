"""Extract a PDF and persist it as a real Test, so it can be opened in the UI.

    # reuse a previous run's questions.json (no model calls, no spend)
    .venv/bin/python scripts/seed_from_pdf.py paper.pdf --reuse out/questions.json

    # full run against the real model
    LLM_MODE=openai .venv/bin/python scripts/seed_from_pdf.py paper.pdf

Goes through test_service exactly as the HTTP API would -- create the test, upload
each figure, then PUT the questions -- so what lands in DynamoDB is whatever the
real endpoints would have written. This is the synchronous stand-in for the async
job; it exists so the extraction output can be reviewed in the editor before the
job wiring is finished.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from app.core.config import get_settings  # noqa: E402
from app.llm import get_question_extractor  # noqa: E402
from app.llm.extraction_schemas import ExtractedQuestion  # noqa: E402
from app.schemas.tests import CreateTestRequest, PutQuestionsRequest, QuestionInput  # noqa: E402
from app.services import generation_pipeline, test_service  # noqa: E402

DEFAULT_SUB = "dev-teacher"


def _as_rich_text(plain: str) -> str:
    """Wrap an extracted stem as the HTML fragment the editor and take page expect.

    No escaping here: QuestionInput runs sanitize_rich_text, whose bleach pass
    already escapes a stray `<` in extracted maths. Escaping first would
    double-escape anything the model emitted as an entity.
    """
    return f"<p>{plain}</p>"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--sub", default=DEFAULT_SUB, help="teacher to own the test")
    parser.add_argument("--title", default=None)
    parser.add_argument("--instruction", default=None)
    parser.add_argument(
        "--reuse", type=Path, default=None, help="questions.json from a previous run"
    )
    args = parser.parse_args()

    pdf_bytes = args.pdf.read_bytes()
    settings = get_settings()

    if args.reuse:
        questions = [ExtractedQuestion(**q) for q in json.loads(args.reuse.read_text())]
        print(f"reusing {len(questions)} questions from {args.reuse}")
        # Figures still have to be cropped from the PDF, but that is free.
        from app.pdf import document

        doc = document.open_document(pdf_bytes, max_pages=settings.max_pdf_pages)
        figures = generation_pipeline._attach_figures(doc, pdf_bytes, questions, lambda m: None)
        expected = len(questions)
    else:
        print(f"extracting with llm_mode={settings.llm_mode} "
              f"model={settings.openai_extraction_model}")
        started = time.monotonic()
        outcome = generation_pipeline.run_extraction(
            pdf_bytes=pdf_bytes,
            extractor=get_question_extractor(),
            instruction=args.instruction,
            max_pages=settings.max_pdf_pages,
            progress=lambda m: print(f"  .. {m}", flush=True),
        )
        print(f"  extracted {len(outcome.questions)}/{outcome.expected_count} "
              f"in {time.monotonic() - started:.1f}s")
        questions, figures = outcome.questions, outcome.figures
        expected = outcome.expected_count

    title = args.title or args.pdf.stem.replace("_", " ").title()

    summary = test_service.create_test(
        args.sub, CreateTestRequest(title=title[:200], duration_seconds=len(questions) * 60)
    )
    test_id = summary.test_id
    print(f"created test {test_id} ({title})")

    # Upload each figure through the same service the HTTP route calls, so the
    # magic-byte check and key minting are exercised for real.
    keys_by_question: dict[int, str] = {}
    for figure in figures:
        uploaded = test_service.upload_question_image(
            args.sub, test_id, "image/png", figure.png
        )
        keys_by_question[figure.question_number] = uploaded.image_key
    print(f"uploaded {len(keys_by_question)} figure(s)")

    payload = PutQuestionsRequest(
        questions=[
            QuestionInput(
                stem=_as_rich_text(q.stem),
                options=q.options,
                correct_index=q.correct_index,
                image_key=keys_by_question.get(q.number),
                image_alt=f"Figure for question {q.number}" if q.number in keys_by_question else None,
            )
            for q in questions
        ]
    )
    detail = test_service.replace_questions(args.sub, test_id, payload)

    with_images = sum(1 for q in detail.questions if q.image_url)
    print(f"saved {len(detail.questions)} questions ({with_images} with an image)")
    print(f"detected {expected}, persisted {len(detail.questions)}")
    print(f"\nopen:  http://localhost:3000/tests/{test_id}/edit")


if __name__ == "__main__":
    main()
