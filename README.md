# QuizDeck API

FastAPI backend for QuizDeck — teachers create MCQ tests (manually or with AI),
invite students by email, and track results. Frontend lives in
[`something-web`](https://github.com/Codeforme1234/something-web).

## Stack

FastAPI · Pydantic v2 · DynamoDB (single table, PK+SK only) · Cognito (Google
login) · SES · OpenAI · Poetry

## Local setup

Auth and the LLM still run against mocks. **Storage does not**: there is no
DynamoDB Local, no MinIO and no local-filesystem object store, so you need AWS
credentials for the dev table and bucket. See [docs/aws-setup.md](docs/aws-setup.md).

```bash
poetry install
cp .env.example .env.dev        # then fill in TABLE_NAME, S3_BUCKET, profiles
make table                      # idempotent; creates the table it names
make dev                        # uvicorn on :8000
```

`make test` needs none of that — the suite intercepts boto3 in-process with
moto, so it touches no AWS account and no network.

## Environments

Two env files, chosen by `QUIZDECK_ENV_FILE`, defaulting to `.env.dev`:

```bash
make dev     # .env.dev  -> quizdeck-dev  + quizdeck-media-dev
make prod    # .env.prod -> quizdeck-prod + quizdeck-media
```

Cognito and SES are **shared** — one user pool, one verified sending domain.
Only the table, the bucket and the public origins differ.

Two guards make the split hard to defeat: `APP_ENV=dev` is refused with a
`*-prod` resource name, and `APP_ENV=prod` is refused with any local credential
profile (a container has no `~/.aws`), so `.env.prod` will not boot locally.

## Modes

| Env var | Values | Notes |
| --- | --- | --- |
| `APP_ENV` | `dev` \| `prod` | `prod` refuses to boot with any mock below |
| `AUTH_MODE` | `fake` \| `cognito` | fake accepts a `dev-*` bearer token |
| `EMAIL_MODE` | `outbox` \| `ses` | outbox writes JSON to `.dev/outbox/` |
| `LLM_MODE` | `fake` \| `openai` | fake returns canned questions |

Storage is deliberately absent from that table: it has no mock. A local store
could not catch a bucket, credential or content-type mistake, and a container
filesystem is ephemeral, so every question image would vanish on the next deploy.

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
