# QuizDeck API — conventions

FastAPI + Pydantic backend for an MCQ test platform. Teachers author tests and
invite students by email; students take a timed test via a unique link.

## Non-negotiable rules

**1. DynamoDB: one table, PK + SK only.** No GSIs, no extra key attributes.
Every key string is built by a helper in `app/repositories/keys.py` — never
inline a `"TEST#..."` string anywhere else.

**2. Items are JSON blobs.** DynamoDB only ever sees
`{PK, SK, entityType, version, data}` where `data` is a Pydantic
`model_dump_json()`. Go through `app/repositories/store.py`; do not call
`table.put_item` directly from services. Because business fields are inside the
blob, DynamoDB conditions cannot reference them — use the `version` attribute
via `store.put_versioned` for optimistic locking.

**3. Ownership comes from the key, not from an if-statement.** Test meta lives
in the teacher's partition (`TEACHER#<sub>` / `TEST#<id>`), so reading a test
you don't own simply misses. Always look up a test with the *caller's* sub.
Never accept a teacher id from the request body.

**4. Students never receive answers.** `correct_index` must not appear on any
response model under `app/schemas/take.py`. Strip it by using a separate
response model, not by deleting keys at runtime.

**5. Server time only.** All timing decisions use `app.core.clock.now()`.
Never trust a timestamp sent by a client.

**6. Mode switches, never `if DEBUG`.** Auth, email, and LLM each have a
Protocol + a real and a fake implementation, chosen once at startup from
`Settings`. Fake modules are lazy-imported so production never loads them.
`Settings` refuses to boot with `APP_ENV=prod` plus any fake mode.

## Layout

```
app/core/         config (mode switches), clock, ids, exceptions
app/auth/         TokenVerifier protocol + cognito/fake, FastAPI dependency
app/models/       persisted domain models (what goes in the blob)
app/schemas/      request/response DTOs (what crosses the wire)
app/repositories/ keys.py + store.py + one repo module per entity
app/services/     business logic; email/ subpackage with ses + outbox senders
app/llm/          MCQGenerator protocol, OpenAI generator, prompts/ (text only)
app/routers/      thin HTTP layer; dev.py mounted only when APP_ENV=dev
```

Prompt text lives only in `app/llm/prompts/`. Call code must not contain prompt
strings.

## Errors

Raise the domain exceptions in `app/core/exceptions.py`
(`NotFoundError`, `ConflictError`, `GoneError`, ...). `main.py` maps them to
`{code, message}` JSON. Don't raise `HTTPException` from services.

## Naming

Python and JSON both use `snake_case`. The frontend mirrors these field names
exactly, so don't add camelCase aliases.

## Running

```bash
docker compose up -d          # DynamoDB Local on :8001 (colima is the docker runtime)
poetry install
poetry run python scripts/create_table.py
poetry run uvicorn app.main:app --reload --port 8000
poetry run pytest -q
```
