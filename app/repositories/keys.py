"""Every PK/SK pattern in the single table. No key strings anywhere else.

Layout (PK + SK only, no GSIs):
  TEACHER#<sub>    / PROFILE               admin profile (company_id links to its company)
  COMPANY#<compId> / PROFILE               company profile (credit_balance lives here)
  TEACHER#<sub>    / TEST#<testUlid>       test meta (teacher partition -> listing + ownership)
  TEST#<testId>    / Q#<001>               question (zero-padded order)
  TEST#<testId>    / SESSION#<sessionId>   student session/invitation
  TEST#<testId>    / SUB#<sessionId>       submission
  TOKEN#<token>    / LOOKUP                student-link token -> {testId, sessionId, teacherSub}
"""

PROFILE_SK = "PROFILE"
LOOKUP_SK = "LOOKUP"

TEST_SK_PREFIX = "TEST#"
QUESTION_SK_PREFIX = "Q#"
SESSION_SK_PREFIX = "SESSION#"
SUBMISSION_SK_PREFIX = "SUB#"


def teacher_pk(sub: str) -> str:
    return f"TEACHER#{sub}"


def company_pk(company_id: str) -> str:
    return f"COMPANY#{company_id}"


def test_sk(test_id: str) -> str:
    return f"{TEST_SK_PREFIX}{test_id}"


def test_pk(test_id: str) -> str:
    return f"TEST#{test_id}"


def question_sk(order: int) -> str:
    return f"{QUESTION_SK_PREFIX}{order:03d}"


def session_sk(session_id: str) -> str:
    return f"{SESSION_SK_PREFIX}{session_id}"


def submission_sk(session_id: str) -> str:
    return f"{SUBMISSION_SK_PREFIX}{session_id}"


def token_pk(token: str) -> str:
    return f"TOKEN#{token}"
