from app.auth.protocol import TeacherClaims


class FakeVerifier:
    """Dev-only verifier. Accepts any token starting with "dev-".

    The suffix becomes the teacher identity, so "dev-alice" and "dev-bob"
    act as two different teachers when testing ownership boundaries.
    """

    def verify(self, token: str) -> TeacherClaims:
        if not token.startswith("dev-"):
            raise ValueError("fake auth expects a 'dev-*' token")
        suffix = token[4:] or "teacher"
        return TeacherClaims(
            sub=f"dev-{suffix}",
            email=f"{suffix}@local.test",
            name=suffix.replace("-", " ").title(),
        )
