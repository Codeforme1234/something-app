# QuizDeck API

FastAPI backend for QuizDeck — teachers create MCQ tests (manually or with AI),
invite students by email, and track results. Frontend lives in
[`something-web`](https://github.com/Codeforme1234/something-web).

## Stack

FastAPI · Pydantic v2 · DynamoDB (single table, PK+SK only) · Cognito (Google
login) · SES · OpenAI · Poetry

## Local setup

Everything runs against mocks — no AWS account or OpenAI key needed.

```bash
docker compose up -d                          # DynamoDB Local on :8001
poetry install
cp .env.example .env
poetry run python scripts/create_table.py
poetry run uvicorn app.main:app --reload --port 8000
```

Docker runs through [colima](https://github.com/abiosoft/colima) on this
machine (`docker context` shows `colima` as active). Start it with
`colima start` if the daemon isn't up.

A DynamoDB browser is available at http://localhost:8002.

## Modes

| Env var | Values | Notes |
| --- | --- | --- |
| `APP_ENV` | `dev` \| `prod` | `prod` refuses to boot with any mock below |
| `AUTH_MODE` | `fake` \| `cognito` | fake accepts a `dev-*` bearer token |
| `EMAIL_MODE` | `outbox` \| `ses` | outbox writes JSON to `.dev/outbox/` |
| `LLM_MODE` | `fake` \| `openai` | fake returns canned questions |
| `DYNAMO_ENDPOINT_URL` | url \| unset | set for DynamoDB Local, unset for AWS |

In dev, `GET /api/v1/dev/outbox` lists the mock emails.

## Data model

One table, `PK` + `SK` only — no GSIs. Each item is
`{PK, SK, entityType, version, data}` where `data` is a JSON-serialized
Pydantic model, so the schema lives in Python rather than in DynamoDB.

| Entity | PK | SK |
| --- | --- | --- |
| Teacher | `TEACHER#<sub>` | `PROFILE` |
| Test | `TEACHER#<sub>` | `TEST#<ulid>` |
| Question | `TEST#<id>` | `Q#<order>` |
| Session | `TEST#<id>` | `SESSION#<id>` |
| Submission | `TEST#<id>` | `SUB#<sessionId>` |
| Link token | `TOKEN#<token>` | `LOOKUP` |

Tests live in the teacher's partition, so listing a teacher's tests is one
query and cross-teacher access misses on the key rather than relying on an
ownership check.

See [CLAUDE.md](CLAUDE.md) for the conventions this codebase follows.

## Tests

```bash
poetry run pytest -q
```

## Seeding dev data

```bash
poetry run python scripts/seed.py
```

Idempotent — creates (or reuses) a demo teacher, one published 3-question
test, two invited students, and one completed attempt, then prints the
dashboard URL, the outbox directory, and each student's `/t/<token>` link.
Refuses to run unless `APP_ENV=dev`.

## Going to real AWS

Everything above runs on mocks. See [docs/aws-setup.md](docs/aws-setup.md)
for standing up DynamoDB, Cognito (Google sign-in), and SES, and for the
exact env vars that flip each mode switch to real.
