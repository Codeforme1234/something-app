"""Unit tests for CognitoJwtVerifier.

Both PyJWKClient and jwt.decode are mocked, so this suite never fetches a
real JWKS or talks to Cognito. Constructing a real PyJWKClient here is safe
either way -- its __init__ only validates the URI and sets up caches; the
network fetch happens lazily inside get_signing_key_from_jwt, which is the
one method mocked out below.
"""

from unittest.mock import MagicMock

import jwt
import pytest

from app.auth.cognito import CognitoJwtVerifier


def _verifier() -> CognitoJwtVerifier:
    return CognitoJwtVerifier(
        region="us-east-1", user_pool_id="us-east-1_Pool123", client_id="client-abc"
    )


def _stub_signing_key(monkeypatch, verifier: CognitoJwtVerifier) -> None:
    signing_key = MagicMock()
    signing_key.key = "fake-public-key"
    monkeypatch.setattr(
        verifier._jwks, "get_signing_key_from_jwt", MagicMock(return_value=signing_key)
    )


def test_valid_claims_map_to_teacher_claims(monkeypatch):
    verifier = _verifier()
    _stub_signing_key(monkeypatch, verifier)
    payload = {
        "sub": "abc-123",
        "email": "alice@example.com",
        "name": "Alice Teacher",
        "token_use": "id",
    }
    monkeypatch.setattr(jwt, "decode", MagicMock(return_value=payload))

    claims = verifier.verify("some.jwt.token")

    assert claims.sub == "abc-123"
    assert claims.email == "alice@example.com"
    assert claims.name == "Alice Teacher"


def test_missing_name_falls_back_to_email(monkeypatch):
    verifier = _verifier()
    _stub_signing_key(monkeypatch, verifier)
    payload = {"sub": "abc-123", "email": "alice@example.com", "token_use": "id"}
    monkeypatch.setattr(jwt, "decode", MagicMock(return_value=payload))

    claims = verifier.verify("some.jwt.token")

    assert claims.name == "alice@example.com"


def test_non_id_token_use_raises_value_error(monkeypatch):
    verifier = _verifier()
    _stub_signing_key(monkeypatch, verifier)
    payload = {"sub": "abc-123", "email": "alice@example.com", "token_use": "access"}
    monkeypatch.setattr(jwt, "decode", MagicMock(return_value=payload))

    with pytest.raises(ValueError, match="id token"):
        verifier.verify("some.jwt.token")


def test_pyjwt_error_is_wrapped_in_value_error(monkeypatch):
    verifier = _verifier()
    _stub_signing_key(monkeypatch, verifier)
    monkeypatch.setattr(
        jwt, "decode", MagicMock(side_effect=jwt.ExpiredSignatureError("token expired"))
    )

    with pytest.raises(ValueError, match="invalid token"):
        verifier.verify("some.jwt.token")


def test_signing_key_lookup_failure_is_wrapped_in_value_error(monkeypatch):
    """PyJWKClient raises jwt.PyJWKClientError (a PyJWTError subclass) for
    an unknown kid / unreachable JWKS -- that must be wrapped the same way
    as a jwt.decode failure, since verify() has one try/except around both
    calls."""
    verifier = _verifier()
    monkeypatch.setattr(
        verifier._jwks,
        "get_signing_key_from_jwt",
        MagicMock(side_effect=jwt.PyJWKClientError("unable to find a signing key")),
    )

    with pytest.raises(ValueError, match="invalid token"):
        verifier.verify("some.jwt.token")
