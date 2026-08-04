import jwt
from jwt import PyJWKClient

from app.auth.protocol import TeacherClaims


class CognitoJwtVerifier:
    """Verifies Cognito ID tokens: signature (JWKS), issuer, audience, expiry."""

    def __init__(self, region: str, user_pool_id: str, client_id: str):
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.client_id = client_id
        self._jwks = PyJWKClient(f"{self.issuer}/.well-known/jwks.json")

    def verify(self, token: str) -> TeacherClaims:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise ValueError(f"invalid token: {e}") from e
        if payload.get("token_use") != "id":
            raise ValueError("expected an id token")
        return TeacherClaims(
            sub=payload["sub"],
            email=payload.get("email", ""),
            name=payload.get("name") or payload.get("email", "Teacher"),
        )
