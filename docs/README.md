# PDF Question Extractor & Generator

See `PLAN.md` for the full step-by-step pipeline design and cost breakdown.

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn app:app --reload
```

## Usage

```bash
curl -X POST http://127.0.0.1:8000/generate \
  -F "file=@jee_paper.pdf" \
  -F "prompt=Generate new questions of similar difficulty for practice"
```

Response shape:

```json
{
  "original_question_count": 30,
  "generated_question_count": 30,
  "extracted_questions": [ { "number": 1, "subject": "Physics", "question_type": "mcq", "question_text": "...", "options": ["..."], "difficulty": "medium" } ],
  "generated_questions": [ { "based_on_number": 1, "subject": "Physics", "question_type": "mcq", "question_text": "...", "options": ["..."], "difficulty": "medium" } ]
}
```

## How it decides text vs. vision per page

Each page's extractable text length is checked against `TEXT_DENSITY_THRESHOLD` (40 chars).
Below that, the page is assumed to be diagram/graph/equation-heavy (common on JEE physics/
chemistry pages) and is instead rendered to an image and read by `gpt-4o` vision, which
transcribes the question text and describes the diagram in enough detail to remain answerable.
This keeps cost/latency low on plain-text pages (SAT reading/writing sections, MCQ-only pages)
while still handling the harder pages correctly.

## Why questions are extracted before being generated (not generated in one shot)

Doing it in two structured steps (extract -> generate) rather than one big prompt:
- guarantees the output count matches the input count (1 generated question per extracted question)
- lets each generated question inherit its source's subject/topic/difficulty/type precisely
- keeps failures isolated — if generation fails on one batch, you still have the extraction intact

## Things to tune for your use case

- `QUESTIONS_PER_BATCH` (10): lower it if you see quality drop on very long papers (JEE Advanced
  can have 50+ questions); raise it to cut API calls if quality holds up.
- `MODEL_TEXT` / `MODEL_VISION`: swap for cheaper/faster or higher-accuracy models depending on
  your budget and how math/diagram-heavy the source papers are.
- `correct_answer` is currently optional and best-effort from the model — for anything
  answer-key-critical, you may want a separate verification pass or to source answer keys directly
  from the PDF's answer section if included.
- No de-duplication check is done across generated questions yet — for large batches you may want
  to add an embedding-similarity check to avoid two generated questions being near-duplicates of
  each other.

## Known limitations

- Very unusual PDF layouts (e.g. answer options split across columns oddly) may need the
  `TEXT_DENSITY_THRESHOLD` tuned down further, or forcing vision mode for the whole document.
- Scanned/image-only PDFs (no text layer at all) will automatically route every page through
  vision — works, but slower and costs more per page.
