.PHONY: db-up db-down table seed dev test

# Use the in-project venv directly: it avoids depending on which `poetry`
# (or which Python) happens to be first on PATH.
PY := .venv/bin/python

db-up:
	docker compose up -d

db-down:
	docker compose down

table:
	$(PY) scripts/create_table.py

seed:
	$(PY) scripts/seed.py

dev:
	$(PY) -m uvicorn app.main:app --reload --port 8000

test:
	$(PY) -m pytest -q
