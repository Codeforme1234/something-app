"""Run the PDF extraction pipeline against a file and print what comes out.

    .venv/bin/python scripts/extract_pdf.py path/to/paper.pdf
    .venv/bin/python scripts/extract_pdf.py paper.pdf --instruction "Only physics"
    .venv/bin/python scripts/extract_pdf.py paper.pdf --dry-run   # no model calls

Dev tool for eyeballing extraction quality and cost before wiring the async job.
Honours LLM_MODE: `fake` costs nothing, `openai` makes real calls and spends real
money. Writes figures next to the report so the crops can be checked.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

from app.core.config import get_settings  # noqa: E402
from app.llm import get_question_extractor  # noqa: E402
from app.pdf import answers, document, numbering  # noqa: E402
from app.services import generation_pipeline  # noqa: E402


def _dry_run(pdf_bytes: bytes, max_pages: int) -> None:
    """Everything except the model calls: parsing, cleaning, answer keys, figures."""
    doc = document.open_document(pdf_bytes, max_pages=max_pages)
    blocks = answers.split_question_blocks(doc.text())
    option_answers = answers.answer_index_map(blocks)
    numeric_answers = answers.numeric_answer_map(blocks)
    numbers = numbering.find_question_numbers(doc.text())

    print(f"pages              : {doc.page_count}")
    print(f"vision pages       : {doc.vision_pages()}")
    print(f"document chars     : {len(doc.text())}")
    print(f"questions detected : {numbering.expected_count(numbers)}")
    print(f"  four-option      : {sum(b.has_options for b in blocks)}")
    print(f"  numerical-value  : {sum(not b.has_options for b in blocks)}")
    print(f"answer keys read   : {len(option_answers)} option + {len(numeric_answers)} numeric")
    figures = sum(len(document.extract_figures(pdf_bytes, p)) for p in doc.vision_pages())
    print(f"figures croppable  : {figures}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--instruction", default=None, help="teacher's transform request")
    parser.add_argument("--dry-run", action="store_true", help="skip all model calls")
    parser.add_argument("--out", type=Path, default=None, help="directory for the report + figures")
    args = parser.parse_args()

    pdf_bytes = args.pdf.read_bytes()
    settings = get_settings()
    print(f"file      : {args.pdf}  ({len(pdf_bytes):,} bytes)")
    print(f"llm_mode  : {settings.llm_mode}")
    if not args.dry_run:
        print(f"model     : {settings.openai_extraction_model}")
    print()

    if args.dry_run:
        _dry_run(pdf_bytes, settings.max_pdf_pages)
        return

    started = time.monotonic()
    outcome = generation_pipeline.run_extraction(
        pdf_bytes=pdf_bytes,
        extractor=get_question_extractor(),
        instruction=args.instruction,
        max_pages=settings.max_pdf_pages,
        progress=lambda message: print(f"  .. {message}", flush=True),
    )
    elapsed = time.monotonic() - started

    got, expected = len(outcome.questions), outcome.expected_count
    print()
    print(f"extracted : {got} of {expected} detected  ({elapsed:.1f}s)")
    print(f"figures   : {len(outcome.figures)} attached")
    missing = sorted(set(range(1, expected + 1)) - {q.number for q in outcome.questions})
    if missing:
        print(f"MISSING   : {missing}")

    for question in outcome.questions[:5]:
        print(f"\n--- Q{question.number} (page {question.source_page}, figure={question.has_figure})")
        print(f"    {question.stem[:220]}")
        for index, option in enumerate(question.options):
            mark = "*" if index == question.correct_index else " "
            print(f"  {mark} ({index + 1}) {option[:90]}")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "questions.json").write_text(
            json.dumps([q.model_dump() for q in outcome.questions], indent=2, ensure_ascii=False)
        )
        for figure in outcome.figures:
            (args.out / f"q{figure.question_number:03d}.png").write_bytes(figure.png)
        print(f"\nwrote {args.out}/questions.json and {len(outcome.figures)} figure(s)")


if __name__ == "__main__":
    main()
