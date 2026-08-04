from functools import lru_cache

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.protocol import TeacherClaims, TokenVerifier
from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def get_verifier() -> TokenVerifier:
    settings = get_settings()
    if settings.auth_mode == "cognito":
        from app.auth.cognito import CognitoJwtVerifier

        return CognitoJwtVerifier(
            region=settings.cognito_region,
            user_pool_id=settings.cognito_user_pool_id,
            client_id=settings.cognito_client_id,
        )
    # fake mode is only reachable in dev: Settings refuses fake modes in prod
    from app.auth.fake import FakeVerifier

    return FakeVerifier()


def get_current_teacher(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TeacherClaims:
    if credentials is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return get_verifier().verify(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid token")
