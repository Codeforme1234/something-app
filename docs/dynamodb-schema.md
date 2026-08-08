# DynamoDB Schema Reference

*How everything is stored. Accurate as of 2026-08-07. The authoritative code
is `app/repositories/keys.py` (every key string) and
`app/repositories/store.py` (every read/write shape); this document explains
them.*

## 1. One table, one item shape

A single table per environment (`quizdeck-dev`, `quizdeck-prod` — `TABLE_NAME`
has no default, so an incomplete env file cannot inherit the other one's data),
**PK + SK only — no GSIs, no LSIs, ever** (CLAUDE.md rule 1). Every item,
regardless of entity, looks identical to DynamoDB:

```json
{
  "PK":         "TEST#01JQ...",
  "SK":         "Q#001",
  "entityType": "QUESTION",
  "version":    3,
  "data":       "{\"question_id\": \"01JQ...\", \"order\": 1, ...}"
}
```

- **`data`** is `model_dump_json()` of a Pydantic model from `app/models/`.
  The schema lives entirely in Python; DynamoDB never sees a business field.
- **`version`** is the only attribute conditions can reference (business
  fields are inside the blob), so it carries all optimistic locking.
- **`entityType`** is informational (debugging, the admin UI); no query
  branches on it.

Two consequences worth internalizing:

1. **Migrations are Pydantic defaults.** Adding a field with a default means
   every old blob still parses; that is the entire migration story
   (`Test.company_id`, `Question.image_key`, `Company.ai_credit_balance` were
   all added this way). A field without a default would 500 on the first old
   item read — never do that.
2. **You cannot filter or update by business field.** Every access pattern
   must be expressible as a key operation; if it can't be, the key design is
   wrong, not the query (rule 7: no `Scan`, enforced by a unit test that greps
   the source tree).

## 2. Key map

```
PK                     SK                      entityType     model (app/models/)
─────────────────────  ──────────────────────  ─────────────  ────────────────────
TEACHER#<sub>          PROFILE                 TEACHER        Teacher
COMPANY#<companyId>    PROFILE                 COMPANY        Company
TEACHER#<sub>          TEST#<testUlid>         TEST           Test
TEST#<testId>          Q#<order, 3 digits>     QUESTION       Question
TEST#<testId>          SESSION#<sessionUlid>   SESSION        StudentSession
TEST#<testId>          SUB#<sessionUlid>       SUBMISSION     Submission
TOKEN#<linkToken>      LOOKUP                  TOKEN_LOOKUP   TokenLookup
```

Every key string is built by a helper in `keys.py`; a literal `"TEST#..."`
anywhere else is a review-blocking offence.

### Why each key is where it is

- **Test meta lives in the *teacher's* partition**, not its own. This is the
  ownership mechanism: reading a test requires the caller's `sub` in the key,
  so someone else's test doesn't 403 — it *misses*. It also makes the
  dashboard list one Query (ULIDs are time-ordered, so `descending=True` gives
  newest-first for free).
- **Questions, sessions, and submissions live in the *test's* partition.**
  One Query per prefix fetches a whole test's worth. The question SK is the
  zero-padded `order` (`Q#001`), so questions come back sorted and a
  shorter replacement list can delete the leftover tail by computing keys.
- **`TOKEN#<token>/LOOKUP` is the no-GSI inverted index.** A student arrives
  with only a token; this item resolves it to `{test_id, session_id,
  teacher_sub}` in one GetItem. The token is *also* stored on the session
  (`link_token`) because publish-time invitation sending needs session→token,
  and without a GSI the lookup item can't be queried in reverse. That
  duplication is deliberate and write-once.

### Entity field summaries

| Entity | Fields (persisted, inside `data`) |
| --- | --- |
| `Teacher` | sub, email, name, company_id?, created_at |
| `Company` | company_id, name, credit_balance, ai_credit_balance?, created_at — both balances on one item so they debit in one write |
| `Test` | test_id, teacher_sub, company_id?, title, difficulty, duration_seconds, status (`draft\|published`), deadline?, question_count, student_count, created_at, published_at? |
| `Question` | question_id, order, stem (sanitized HTML), options[4], correct_index, image_key?, image_alt? |
| `StudentSession` | session_id, test_id, student_name, student_email, status (`invited\|started\|completed`), link_token, invited_at, started_at?, ends_at?, completed_at?, score?, correct_count?, total_questions? |
| `Submission` | session_id, test_id, submitted_at, answers{qid→idx}, per_question{qid→bool}, score, correct_count, total_questions |
| `TokenLookup` | test_id, session_id, teacher_sub |

Denormalizations, each with one writer: `question_count` / `student_count` on
Test (updated in the same request that changes the rows); `company_id` on Test
(reporting only, never access control); score fields copied onto the session at
completion so listing students never reads submissions.

## 3. Access patterns

Every read/write in the system, and the key operation it compiles to:

| Operation | DynamoDB call |
| --- | --- |
| Resolve teacher / provision company | GetItem `TEACHER#sub/PROFILE`; PutItem company; overwrite teacher |
| Dashboard test list | Query `TEACHER#sub` + `begins_with(SK, TEST#)`, descending |
| Open a test (owned) | GetItem `TEACHER#sub/TEST#id` |
| Load a test's questions | Query `TEST#id` + `begins_with(SK, Q#)` |
| Replace questions | BatchWrite: put `Q#001..Q#NNN`, delete `Q#N+1..Q#old` |
| Create test + spend credits | **TransactWriteItems**: put-new Test + versioned put Company |
| Roster students | BatchWrite: N sessions + N token lookups in one batch |
| List students / analytics inputs | Query `TEST#id` + `begins_with(SK, SESSION#)` (+ per-completed GetItem `SUB#`) |
| Student opens link | GetItem `TOKEN#token/LOOKUP` → GetItem test → GetItem session |
| Start attempt | Versioned put on the session (idempotent on lost race: re-read, continue) |
| Submit attempt | **TransactWriteItems**: put-new Submission + versioned put session→completed |
| Teacher review | GetItem session + GetItem submission + Query questions |
| Delete test | Query questions → BatchWrite delete (questions + meta) |

Nothing else exists. In particular there is no "all tests across teachers",
no "find session by email", no cross-tenant anything — those would need a
Scan or a GSI, and their absence is a design guarantee, not an oversight.

## 4. Write primitives (`store.py`)

| Helper | Semantics | On conflict |
| --- | --- | --- |
| `put_new` | Create; must not exist (`attribute_not_exists(PK)`) | 409 `ConflictError` |
| `put_overwrite` | Unconditional upsert (idempotent profile writes) | — |
| `put_versioned` | Replace iff stored `version` matches; returns new version | 409 `ConflictError` |
| `transact_put_new_and_update` | Atomically: one put-new **+** one versioned update, via the low-level client (hand-serialized) | 409 |
| `batch_write` | Bulk puts/deletes via `batch_writer` (auto-chunked, **not atomic**) | — |
| `get` / `query_prefix` | Single read / prefix query with `LastEvaluatedKey` pagination | — |
| `delete` | Unconditional single delete | — |

Reads return `Stored[T]` — the parsed model **plus the version it was read
at** — because any subsequent conditional write needs that version. Service
code holds a `Stored`, copies the model with `model_copy(update=...)`, and
writes back with the remembered version; a concurrent writer makes the loser
409 rather than silently clobbering.

The transaction helper takes exactly one new item and one versioned update.
That constraint is real: DynamoDB transactions cap at 100 items, so "create
test + write 100 questions" can never be one transaction — which is why
question writes are a separate batch and the Test meta item serves as the
commit point (nothing reads questions except through the meta).

## 5. Size discipline

DynamoDB items cap at 400 KB; the request body caps at 1 MB
(`app/main.py::MAX_BODY_BYTES`). The field caps in `app/schemas/` are the
security boundary that keeps every item comfortably under both:

- stem ≤ 6 000 raw chars (≤ 1 000 visible), options 4 × ≤ 300, ≤ 100
  questions per test → worst-case question item ~8 KB, worst-case
  full-test write far under the body cap;
- `answers` dict ≤ 100 entries with 0–3 values;
- knowledge-base text ≤ 20 000 chars (request field, not persisted).

Do not loosen a cap without re-checking both ceilings.

## 6. What is *not* in DynamoDB

Binary content lives in the object store (`app/services/storage/`), keyed by
strings that never appear in DynamoDB partition keys:

```
tests/<testId>/q/<imageUlid>.<png|jpg|webp>     question images
kb/<sha256(sub)[:32]>/<ulid>.<pdf|png|jpg|...>  knowledge-base source documents
```

DynamoDB stores only the **key** (`Question.image_key`), never a URL — the
public origin is runtime config, and since no access pattern can enumerate all
questions (no Scan), a stored URL could never be rewritten if the media host
changed. The key→URL translation happens at read time in the service layer.

## 7. Local development

There is no DynamoDB Local. Dev runs against the real `quizdeck-dev` table with
credentials from the `quizdeck` profile in `~/.aws`; `scripts/create_table.py`
reads `TABLE_NAME` from the active env file and is idempotent.

Tests reach no AWS account at all. `tests/conftest.py` points
`QUIZDECK_ENV_FILE` at a nonexistent path, blanks every credential profile, and
wraps the session in `moto`; `tests/integration/conftest.py` then creates the
table and bucket inside that mock and clears the `lru_cache`d
settings/clients. Network-freeness is therefore structural rather than a habit
— if a unit test starts needing DynamoDB, a module-level dependency leaked, and
moto will surface it as a missing table rather than a silent real call.
