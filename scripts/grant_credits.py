"""Set a teacher's company credit balances. Dev convenience only.

    .venv/bin/python scripts/grant_credits.py                 # dev-teacher, 1000/1000
    .venv/bin/python scripts/grant_credits.py dev-alice
    .venv/bin/python scripts/grant_credits.py dev-alice 500 250

`dev-teacher` is the default because that is the sub the web app's
FakeAuthProvider signs in as (quizdeck-web/src/lib/auth/fake.ts).

Both balances are SET, not incremented, so re-running is idempotent. Goes
through companies_repo/store like everything else -- no direct table writes
(CLAUDE.md rule 2), and no Scan (rule 7), which is why the teacher's sub has to
be named rather than discovered.
"""

import sys

sys.path.insert(0, ".")

from app.models.teacher import Teacher  # noqa: E402
from app.repositories import companies_repo, keys, store  # noqa: E402

DEFAULT_SUB = "dev-teacher"
DEFAULT_CREDITS = 1000


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        print(__doc__)
        return

    sub = args[0] if args else DEFAULT_SUB
    credits = int(args[1]) if len(args) > 1 else DEFAULT_CREDITS
    ai_credits = int(args[2]) if len(args) > 2 else credits

    teacher_stored = store.get(keys.teacher_pk(sub), keys.PROFILE_SK, Teacher)
    if teacher_stored is None:
        print(f"no teacher {sub!r} yet.")
        print(f"  sign in as that identity in the web app, or:")
        print(f"  curl -s localhost:8000/api/v1/me -H 'Authorization: Bearer {sub}'")
        raise SystemExit(1)

    company_id = teacher_stored.model.company_id
    if company_id is None:
        print(f"teacher {sub!r} has no company; call GET /api/v1/me to provision one")
        raise SystemExit(1)

    stored = companies_repo.get_company(company_id)
    if stored is None:
        print(f"company {company_id} referenced by {sub!r} is missing")
        raise SystemExit(1)

    updated = stored.model.model_copy(
        update={"credit_balance": credits, "ai_credit_balance": ai_credits}
    )
    companies_repo.update_company(updated, stored.version)
    print(f"{sub} -> {updated.name}: credits={credits} ai_credits={ai_credits}")


if __name__ == "__main__":
    main()
