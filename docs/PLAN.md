# Implementation Plan — JEE/SAT Question Extractor & Generator

Updated based on real numbers from the JEE Main 2026 paper analysis. Target: ~$0.20 per
paper for extraction, good quality, `gpt-5.6-terra`.

---

## Step 1 — PDF ingestion & page classification

For each uploaded PDF:
1. Run `pdfplumber` text extraction on every page.
2. Classify each page as **text-page** or **vision-page**:
   - Text-page: extractable text length ≥ threshold (~40 chars) AND no unresolved figure markers.
   - Vision-page: sparse extractable text (scanned/complex layout), OR the page visually
     contains a figure — for these papers specifically, only ~9% of questions had real
     diagrams (Q32, 36, 37, 39, 41, 66, 68 in the sample paper), so most pages will be
     classified as text-pages.
3. **Watermark / duplicate-layer handling** (found in this specific PDF source — likely common
   across "answer key" PDFs from prep sites): the raw extracted text contains three overlapping
   layers — (a) a tiled watermark string repeated hundreds of times, (b) the visually-formatted
   question block, (c) a second plain-text/LaTeX-ish echo of the same equations at the bottom of
   the page. Before sending anything to the model:
   - Strip repeated single-word/short-string patterns that recur >10x on a page (watermark).
   - Deduplicate: if the same question number's content appears twice in different formatting
     within one page's extracted text, keep only one representation (prefer the LaTeX/plain-text
     echo — it's usually cleaner and cheaper in tokens than the formatted block).
   - This cleanup step is pure Python/regex, not a model call — it costs nothing and meaningfully
     shrinks the token count sent to the model.

## Step 2 — Vision pass (only for flagged pages)

For the small subset of vision-pages:
1. Render the page to an image (`PyMuPDF`).
2. Send to `gpt-5.6-terra` with a focused prompt: transcribe question text/options exactly,
   describe the figure/diagram/circuit/graph in enough detail to remain answerable without
   seeing it.
3. Insert the returned transcription back into the page's content in place of the image.

## Step 3 — Single-call structured extraction

1. Concatenate all page content (cleaned text + vision transcriptions) into one document.
2. Send the **whole document in a single call** to `gpt-5.6-terra` (not per-page, not batched —
   the 1M+ context window and 128K output ceiling comfortably fit a 75-question paper) with a
   **leaner schema**: only fields actually needed downstream (number, subject, type, question
   text, options, difficulty) — no verbose free-text diagram descriptions in the final schema,
   since those were only needed transiently for vision-page transcription.
3. Structured output (`response_format`) guarantees valid JSON shape; a post-call check confirms
   `len(extracted) == expected_count` (get expected count cheaply from question numbering in the
   raw text — regex for `Q\d+\.` patterns — before ever calling the model).
4. If count mismatches, re-run extraction only for the missing question-number range.

## Step 4 — Batched generation (text-only, no images)

1. Chunk extracted questions into batches (default 10).
2. For each batch, one call to `gpt-5.6-terra`: generate one new question per input question,
   matching subject/topic/difficulty/type, using the leaner schema.
3. No images in this step — it's the cheapest part of the pipeline per-token, but batching here
   (rather than one big call) keeps per-question writing quality consistent, which matters more
   for generation than extraction.

## Step 5 — Cost control levers (in order of impact)

1. **Hybrid extraction** (Step 1-2): biggest lever — only ~9% of pages pay vision-token prices.
2. **Leaner output schema**: cuts output tokens, which are priced 6x input on Terra.
3. **Single extraction call vs batched-with-images**: avoids re-sending page images per batch.
4. **Batch API** (optional, if async is acceptable): flat 50% off both input and output — turns
   a ~$0.20 sync extraction into ~$0.10.
5. **Prompt caching**: system instructions/schema reused across every paper — cache reads are
   ~90% cheaper than fresh input, meaningful at volume.

## Step 6 — Verification pass (optional, targeted)

Rather than running the whole paper through `gpt-5.6-pro` (expensive, slow), spot-check only:
- Questions extracted from vision-pages (higher error risk).
- Any question where extraction confidence is low (e.g. very short/garbled question_text).
This keeps the accuracy benefit of the Pro tier without paying its cost on the ~90% of
questions that don't need it.

## Step 7 — API endpoint & output

Single `/generate` endpoint:
- Input: PDF file + optional prompt (generation instructions).
- Output: JSON with `original_question_count`, `generated_question_count`,
  `extracted_questions`, `generated_questions`.
- Optionally add `/generate-async` using the Batch API for cost-sensitive, non-urgent use.

---

## Estimated cost per 75-question JEE paper (this pipeline)

| Step | Est. tokens (in/out) | Cost |
|---|---|---|
| Watermark/dedupe cleanup | — (regex, no model call) | $0 |
| Vision pass (~7 pages) | ~11K in / ~2K out | ~$0.05 |
| Extraction (1 call, cleaned text) | ~20K in / ~11K out | ~$0.17 |
| Generation (batched, text-only) | ~19K in / ~17.6K out | ~$0.25 |
| **Total (sync)** | | **~$0.47** |
| **Total (extraction only, sync)** | | **~$0.22** |
| **Total (extraction only, Batch API)** | | **~$0.11** |

Extraction alone lands at your ~$0.20 target with the hybrid+lean-schema approach. If
generation cost also needs to come down, the same batching/schema levers apply — happy to
tune that separately once extraction is validated against real output.

## Next build step

Update `app.py` to reflect this pipeline: hybrid page classification with watermark/dedupe
cleanup, single-call extraction with count verification, batched text-only generation. This is
implemented in the accompanying updated `app.py`.
