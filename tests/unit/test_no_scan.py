"""Regression guard for CLAUDE.md rule 7: no DynamoDB Scan, anywhere.

Every access pattern must be a GetItem, a Query with a key condition, or a
TransactWriteItems -- see app/repositories/store.py. This test walks the
actual source tree so a `.scan(` call added later (by a human or an agent)
fails CI instead of silently reintroducing a full-table scan.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = [REPO_ROOT / "app", REPO_ROOT / "scripts"]


def test_no_dynamodb_scan_calls_in_source():
    offenders = []
    for source_dir in SOURCE_DIRS:
        for path in source_dir.rglob("*.py"):
            text = path.read_text()
            if ".scan(" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, (
        "found .scan( in: "
        + ", ".join(offenders)
        + " -- use a Query with a key condition instead (see store.py)"
    )
