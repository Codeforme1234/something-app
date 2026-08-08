# QuizDeck — Low-Level Design

*Per-feature module design. Accurate as of 2026-08-07 (464 backend tests).
Read [HLD.md](HLD.md) first for the system view;
[dynamodb-schema.md](dynamodb-schema.md) for storage;
[llm-pipeline.md](llm-pipeline.md) for AI internals.*

## 0. Layering and layout

```
app/core/          config (mode switches), clock, ids, exceptions, rich_text
app/auth/          TokenVerifier protocol + cognito/fake + FastAPI dependency
app/models/        persisted domain models (what goes inside the data blob)
app/schemas/       request/response DTOs (what crosses the wire)
app/repositories/  keys.py + store.py + one repo module per entity
app/services/      business logic; email/, storage/ subpackages; ai_credits,
                   knowledge_base, generation_pipeline, grading, results
app/llm/           MCQGenerator + QuestionExtractor, prompts/, schemas
app/pdf/           pure PDF layer: document, cleanup, classify, numbering, answers
app/routers/       thin HTTP layer; dev.py mounted only when APP_ENV=dev
```

Dependency direction is strictly downward: routers → services → repositories →
store. Routers never touch a repo; services never build a key string or call
`table.put_item`; repositories never contain business rules. `models` vs
`schemas` is the persisted/wire split — the same entity usually has one model
and several DTOs (e.g. `Question` → `QuestionOut` / `TakeQuestion` /
`QuestionReview`), and constructing a DTO field-by-field (never `**splat` on
student-facing ones) is what keeps hidden fields hidden.

### Cross-cutting mechanics

- **Errors.** Services raise domain exceptions from `app/core/exceptions.py`
  (`NotFoundError` 404, `ConflictError` 409, `GoneError` 410,
  `BadRequestError` 400, `UpstreamError` 502, `InsufficientCreditsError` /
  `InsufficientAiCreditsError` 402). One handler in `main.py` maps them to
  `{code, message}`. Routers raise nothing but 401 (auth dependency).
- **Time.** Every timing decision calls `app.core.clock.now()`; client
  timestamps are never trusted. Responses that drive countdowns include
  `server_now` so the client computes an offset instead of using its clock.
- **IDs.** `new_ulid()` for entities (time-sortable, so ULID SKs give
  chronological ordering free); `new_link_token()` =
  `secrets.token_urlsafe(24)` for student links (~144-bit bearer capability).
- **Body size.** Middleware rejects >1 MB by Content-Length on every route
  except the upload paths (`/question-images`, `/tests/generate`,
  `/knowledge-base`), which get `MAX_UPLOAD_BYTES` — and each upload handler
  re-enforces its own cap **while reading**, because Content-Length can lie.
- **Concurrency.** The `Stored[T]` read-copy-conditional-write pattern
  everywhere (see schema doc §4). Losing a race is a 409, surfaced to the UI
  as a toast; nothing retries silently except the two documented idempotent
  paths (attempt start, AI-credit backfill).
- **Auth dependency.** `get_current_teacher` → `get_verifier().verify(bearer)`
  → `TeacherClaims{sub, email, name}`. 401 on missing/invalid. Student routes
  take no dependency at all — the token in the path is the credential.

---

## 1. Identity, tenancy, credits

**`GET /me`** → `teachers_repo.upsert_teacher` → `MeResponse`.

Upsert semantics: profile fields (email, name) refresh from claims on every
login; `created_at` is preserved; `company_id` is sticky. Two backfills happen
lazily here so no migration scripts exist:

- teacher without a company (pre-multi-tenancy record) → provision one with
  `starting_credits` and `starting_ai_credits`;
- company with `ai_credit_balance is None` (pre-AI-credits record) → one-time
  grant. The `None` sentinel is what makes this idempotent: `None` means
  "never granted", `0` means "granted and spent", so a drained balance is
  never refilled. The versioned write swallows `ConflictError` — a concurrent
  login's duplicate grant loses the race harmlessly.

`MeResponse` carries both balances plus `ai_credit_cost: {prompt: 1, pdf: 2}`
so pricing lives server-side only.

**Credit spending** (`app/services/ai_credits.py`): `mode_for(payload)` derives
`prompt|document` from whether a knowledge base is attached (never
client-declared); `cost(mode)` reads settings; `debited(company, ...)` returns
one copy with **both** pools reduced. Guard `_require_credits` checks test
credits first, then AI credits against the *mode's* price, raising the
pool-specific 402.

Tests: `test_teacher_provisioning.py` (backfill matrix),
`test_test_service.py` (debit amounts, guard order, drained-during-generation
recheck), `test_credits_api.py` (integration, both pools, distinct codes).

---

## 2. Test CRUD

| Endpoint | Service | Guards |
| --- | --- | --- |
| `POST /tests` | `create_test` | ≥1 test credit; transact create+debit |
| `GET /tests` | `list_tests` | ownership by partition |
| `GET /tests/{id}` | `get_test_detail` | `_get_owned_test` (miss = 404) |
| `PATCH /tests/{id}` | `update_test` | `_require_draft` |
| `DELETE /tests/{id}` | `delete_test` | `_require_draft`; sweeps images after row delete |
| `PUT /tests/{id}/questions` | `replace_questions` | `_require_draft`; validates image keys |

Invariants:

- `_get_owned_test(sub, id)` is the only test lookup — it queries with the
  *caller's* partition, so cross-tenant access is structurally impossible.
- `_require_draft`: published tests are frozen. Publishing is one-way.
- `CreateTestRequest` fields all default (`{}` works) — the dashboard's "New
  test" button creates instantly and the teacher renames in the editor via
  PATCH-on-blur (`InlineTestSettings` on the web side).
- Delete order: read question rows (collect `image_key`s) → delete DynamoDB
  rows → best-effort `delete_many` on storage, wrapped so a storage failure
  can never fail the user-visible delete (orphaned blob ≪ broken UI).

## 3. Question authoring

**Write path** (`replace_questions`): whole-list `PUT`, max 100. Each
`QuestionInput.stem` passes `sanitize_rich_text(stem, max_visible_chars=1000)`
— bleach with allow-list `p strong em u ul ol li br`, `attributes={}`, length
checked against *visible* text. Options: 4, each 1–300 stripped, all distinct.
Every question gets a **fresh `question_id`** (`new_ulid`) on every save;
`order` is array position, 1-based.

That re-minting is the single most important fact for anything referencing
questions:

- **Images survive it** because the S3 key embeds `test_id` + an image-ULID,
  never `question_id`, and the key round-trips through the client:
  `QuestionOut.image_key` → editor state → `QuestionInput.image_key` → new
  `Question` row. `QuestionOut` is the only DTO carrying the raw key; take
  and review DTOs carry only the resolved `image_url` (+ `image_alt`).
- Each supplied `image_key` is validated with
  `storage_keys.belongs_to_test(key, test_id)` — an allow-list regex
  (`tests/<ULID>/q/<ULID>.<png|jpg|webp>`) that makes traversal, foreign
  test IDs, and double extensions unrepresentable.

**Image upload** (`POST /tests/{id}/question-images`, the file's one
`async def` because `UploadFile.read` is a coroutine): ownership → draft check
→ content-type allow-list (SVG excluded: script-capable, and these URLs are
permanent) → read `max_image_bytes + 1` and reject oversize → **magic-byte
check** (`signatures.matches_declared_type`) → store under a freshly minted
key → return `{image_key, image_url}`.

**Serve route** (`GET /images/{key:path}`, anonymous — students have no
bearer): allow-list regex re-validation *before* the key touches storage
(404, not 400, so malformed and missing are indistinguishable) →
`media_type` derived from the key **we** minted, never from the request →
`X-Content-Type-Options: nosniff` → `Cache-Control: immutable` (keys are
write-once). Traversal is ruled out earlier and by construction: every key is
matched against the allow-list regexes in `storage/keys.py` before it reaches
the store at all.

Editor UX contract (web): upload state lives *outside* the questions array so
it can never serialize into a save payload; Save is disabled while any upload
is in flight (a mid-upload save would persist a key whose object doesn't
exist yet, or silently drop it); removing an image detaches the key but never
deletes from storage (a stale tab's PUT is not a reliable statement of
intent, and there is no undo).

## 4. AI generation

`POST /tests/generate` → `test_service.generate_test`. Sequence, with the
reasoning encoded in the order:

```
derive mode + price ──▶ validate kb key ownership (if any)
   check BOTH pools ──▶ LLM call (see llm-pipeline.md §3)
 re-check BOTH pools ──▶ build Question rows (fresh ULIDs, order 1..N)
                         build Test (title = topic[:200], 900s duration,
                                     question_count preset)
                         TRANSACT: put Test + debit company (both pools, one copy)
                         batch-write questions
                         → TestDetail (client lands in the editor)
```

- The double credit check brackets the slow call: the first stops a doomed
  request from ever paying for a model call; the second catches a balance
  drained *during* generation. A generation failure (502) occurs before
  anything is created or spent.
- The transact + batch pair is deliberately not one transaction (101-item
  cap); the Test meta is the commit point — see schema doc §4.
- `GenerateQuestionsRequest`: `topic` 1–300 (doubles as title); `count`
  defaults 10 / `difficulty` defaults medium (the page no longer asks;
  scripts may still set them); `guidelines` — optional Tiptap fragment,
  sanitized at 4 000 visible chars, `"<p></p>"` normalized to `None`, and
  flattened to structured plain text (`rich_text_to_plain`) before the
  prompt; `knowledge_base` — ≤20 000 chars of extracted text;
  `knowledge_base_key` — pinned to the caller's namespace
  (`kb_belongs_to_teacher`, 400 on foreign) though not yet persisted.

`POST /tests/{id}/generate-questions` shares the LLM call but persists
nothing — draft-only, returns `GeneratedQuestion[]` for a future in-editor
"generate more" flow. No UI caller today.

## 5. Knowledge base

`app/services/knowledge_base.py` + `app/routers/knowledge_base.py`. Both
routes teacher-authenticated (unlike question images — a source document has
no reason to leave its owner's account).

**`ingest(sub, name, content_type, bytes)`** pipeline: type allow-list
(pdf/png/jpg/webp/txt/md) → content verification (magic bytes; UTF-8
decodability for text — no signature exists, but a binary renamed `.txt`
would reach the model as mojibake) → **read text** (strategy table in
llm-pipeline.md §4) → reject if empty → truncate to 20 000 → **store only
after the read succeeds** (failed uploads leave nothing) → return
`KnowledgeBaseUpload{file_key, file_url, text, char_count, truncated,
used_vision}`.

Key shape `kb/<sha256(sub)[:32]>/<ulid>.<ext>`: the owner segment is a *hash*
of the sub — path-safe whatever Cognito puts in a sub, absent from storage
access logs, and ownership-checkable by pure string comparison with zero
reads. `read_stored` 404s (not 403s) a foreign key: someone else's key must
be indistinguishable from one that never existed.

Web contract: the picked `File` stays in the browser; upload happens inside
the Generate mutation (phase `"reading"` → `"generating"` spinner copy), so
no vision cost is incurred without a generate. Opening the stored file uses
`apiFetchBlob` + a `blob:` URL, because a plain `<a href>` cannot carry the
bearer header (it would 401).

## 6. Rostering, publishing, invitations

`app/services/student_service.py`.

**`add_students`**: dedupe case-insensitively against existing sessions *and*
within the batch (dupes reported in `skipped_emails`, not errored) → build
`StudentSession` rows (`status=invited`, fresh ULID + link token each) →
`sessions_repo.create_sessions` writes each session **and its
`TOKEN#/LOOKUP` item in one batch** so the pair can't diverge → bump
denormalized `student_count` (versioned) → if the test is already published,
send invitations immediately.

**`publish_test`**: draft + ≥1 question + future deadline → versioned flip to
`published` with `published_at`/`deadline` → send invitations to every
`invited` session.

**Invitation sending is fire-and-log** (`_send_invitations`): mail transport
failures are logged, never raised — the durable session row *is* the
invitation; email is best-effort delivery of it. Links are
`{frontend_origin}/t/{link_token}`. The outbox fake writes one JSON file per
message and extracts the student link by regex so dev/integration flows can
fish the token out of `GET /dev/outbox`.

The contrast with **support** (`POST /support`) is instructive: a support
message has no persistence of its own — the email *is* the record — so there a
send failure propagates as a 502 instead of being swallowed. Whether mail
failure is fatal depends on whether anything else durably records the intent.

## 7. Take flow (anonymous)

`app/services/attempt_service.py`. `_resolve(token)`:
`TOKEN#` lookup → owned-test read (using `teacher_sub` *from the lookup*) →
session read. **Every failure raises the same 404 with the same message** —
unknown token, draft test, and unpublished test are deliberately
indistinguishable.

- `GET /take/{token}` → `TakeInfo` (title, duration, count, status,
  `server_now`; deadline-passed → 410).
- `POST /take/{token}/start`: completed → 410; invited → versioned flip to
  `started` with `ends_at = now + duration_seconds`; **a lost version race is
  absorbed** (re-read, fall through) so double-clicks and two tabs are
  idempotent; past `ends_at + grace` → 410. Returns `TakeQuestion[]` — no
  `correct_index`, no `image_key`, enforced by a recursive model-walk test —
  plus `ends_at` and `server_now`.
- `POST /take/{token}/submit`: `SubmitRequest` is `extra="forbid"`, ≤100
  answers, values 0–3. Late (past grace) → 410, never stored. Grading is the
  pure `grading.grade()` — unanswered counts wrong, bogus question IDs
  ignored, score = round(100·correct/total). Submission + session→completed
  land in **one transaction**; a concurrent submit's `ConflictError`
  surfaces as "already submitted".

`SUBMIT_GRACE_SECONDS` (30) absorbs network latency at the deadline edge on
both start-resume and submit.

## 8. Results

`app/services/results_service.py`, teacher-only, computed on read (no cron,
no stored aggregates — a deliberate architectural rule).

- **`effective_status(session, test, now)`** — the *presentation* status:
  `started` past `ends_at`+grace → `expired`; `invited` past the deadline →
  `link_expired`; otherwise the stored value. Never written back. The
  frontend mirrors this as `SessionStatus` vs `EffectiveStatus`.
- **Student detail**: session row + (if completed) `QuestionReview[]` — the
  one student-adjacent DTO that *does* carry `correct_index`, plus
  `chosen_index`/`is_correct` and the resolved image URL.
- **Analytics**: one session query + one submission read per completed
  session + one question query → `compute_analytics` (pure): completion
  rate, avg/max/min score, per-question correct-rate sorted hardest-first
  with order as the deterministic tiebreak.

## 9. Object storage layer

`app/services/storage/` — the Protocol (`put_bytes/get_bytes/public_url/
delete_many`), `S3ObjectStore` (the only implementation; one bucket per
environment, moto-tested), `keys.py` (both key families + allow-list
validators), `signatures.py` (magic bytes), `urls.py` (proxied vs CDN URL
shapes, with `kb/` keys never eligible for the CDN).

The one design rule that ripples everywhere: **DynamoDB stores keys, never
URLs.** `public_url()` translates at read time — proxied through
`GET /images/{key}` by default (bucket fully private), or direct from
`IMAGE_PUBLIC_BASE_URL` (CDN) when set — so changing the media origin is a
config change with zero data migration. S3 specifics: `ContentType` pinned
from *our* key's extension at put time, immutable cache headers, 404-vs-error
mapping (`NoSuchKey` → `NotFoundError`, `AccessDenied` → `UpstreamError` —
a bucket-policy mistake must not masquerade as a missing image),
`delete_many` batched ≤1 000 and never raising.

## 10. Frontend structure (summary)

`src/lib/api/client.ts` is the single transport: base URL, bearer attachment,
`ApiError` normalization, JSON by default, `FormData` passthrough (no manual
Content-Type — the boundary), `AbortSignal`, plus `apiFetchBlob` for
authenticated binary. Rules that keep it coherent: all backend calls go
through typed functions in `src/lib/api/<feature>.ts`; DTO field names stay
`snake_case` mirroring the backend exactly; `/t/[token]` lives outside the
authenticated shell and calls with `anonymous: true`; shared visual atoms
(`StatusBadge`, `CreditBadge`, `QuestionImage`, …) live in
`components/shared/` and are extended, not duplicated.

State is TanStack Query + local `useState`; no form library. Validation is
hand-rolled mirrors of the Pydantic caps so users fail fast client-side while
the server stays authoritative.

## 11. Test strategy

- **Unit (`tests/unit/`, no network by design)**: pure functions directly;
  services with `monkeypatch.setattr` on module-level names (the codebase
  imports collaborators by name specifically to keep this idiom working —
  there are no `dependency_overrides`); boto3 clients as `MagicMock`s.
  Guard-rail tests worth knowing: `test_no_scan.py` (greps the tree for
  `.scan(`), `test_take_schemas.py` (recursive DTO walk banning
  `correct_index`/`image_key`), `test_config.py` (the refusal matrix: fakes and
  credential profiles in prod, `*-prod` resource names in dev, missing resource
  names anywhere). Network-freeness is now structural rather than something you
  demonstrate by hand — see `tests/conftest.py`.
- **Integration (`tests/integration/`, moto)**: real HTTP through
  `TestClient` against a moto-backed table and bucket created per session
  (`tests/conftest.py` sandboxes the whole run, so no test can reach AWS);
  fake auth/LLM/email. Fresh random `dev-<hex>` identity per test for isolation;
  outbox/object dirs pointed at `tmp_path`.
