.PHONY: table seed dev prod test

# Use the in-project venv directly: it avoids depending on which `poetry`
# (or which Python) happens to be first on PATH.
PY := .venv/bin/python

# There are no db-up/db-down targets any more. DynamoDB Local and MinIO are
# gone; every environment talks to real AWS with its own table and bucket, and
# the test suite intercepts boto3 with moto instead of running an emulator.

# Create the table named by the env file being used. Idempotent, and safe
# against an existing table -- see its ClientError handling.
table:
	$(PY) scripts/create_table.py

seed:
	$(PY) scripts/seed.py

# Dev is the default env file, so this needs no QUIZDECK_ENV_FILE.
dev:
	$(PY) -m uvicorn app.main:app --reload --port 8000

# Deliberately explicit, and deliberately not the default target. This reads
# .env.prod, which points at the production table and bucket -- and it will
# refuse to start on a laptop, because APP_ENV=prod rejects the credential
# profiles that local dev relies on.
prod:
	QUIZDECK_ENV_FILE=.env.prod $(PY) -m uvicorn app.main:app --port 8000

# Never touches AWS: tests/conftest.py sandboxes the whole session with moto.
test:
	$(PY) -m pytest -q
