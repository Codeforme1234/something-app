"""Unit tests for app/services/storage/s3.py.

boto3 is monkeypatched at the module's own `boto3` name, the same way
tests/unit/test_ses_sender.py does it, so nothing here touches AWS.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.exceptions import NotFoundError, UpstreamError
from app.services.storage import s3 as s3_module
from app.services.storage import urls

PNG = b"\x89PNG\r\n\x1a\n" + b"body"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "GetObject")


def _patch_session(monkeypatch) -> tuple[MagicMock, MagicMock]:
    """Stub boto3.Session at the module's own `boto3` name. Returns the Session
    factory (to assert how it was built) and the client it hands back."""
    client = MagicMock()
    session = MagicMock(**{"client.return_value": client})
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(s3_module.boto3, "Session", factory)
    return factory, client


def _settings(**overrides) -> Settings:
    # s3_profile is pinned rather than left to default because tests/conftest.py
    # blanks it in os.environ for the whole session, and pydantic-settings reads
    # os.environ even with _env_file=None.
    fields = {
        "table_name": "quizdeck-test",
        "s3_bucket": "quizdeck-media-test",
        "api_public_origin": "https://api.example.com",
        "s3_profile": None,
    } | overrides
    return Settings(_env_file=None, **fields)


@pytest.fixture
def store(monkeypatch):
    """An S3ObjectStore whose boto3 client is a MagicMock."""
    _, client = _patch_session(monkeypatch)
    monkeypatch.setattr(s3_module, "get_settings", _settings)
    built = s3_module.S3ObjectStore()
    return built, client


def test_a_missing_bucket_is_refused_at_settings_construction(monkeypatch):
    """S3_BUCKET has no default: there is no local mode to fall back to, so an
    env file that forgets it must fail at startup rather than at first upload."""
    monkeypatch.delenv("S3_BUCKET", raising=False)

    with pytest.raises(ValidationError, match="s3_bucket"):
        Settings(_env_file=None, table_name="quizdeck-test")


# --- credentials --------------------------------------------------------------


def test_s3_profile_names_the_session_profile(monkeypatch):
    """Without this the client falls back to the default chain, which on a dev
    machine is usually a different AWS account."""
    factory, _ = _patch_session(monkeypatch)
    monkeypatch.setattr(s3_module, "get_settings", lambda: _settings(s3_profile="quizdeck"))

    s3_module.S3ObjectStore()

    assert factory.call_args.kwargs["profile_name"] == "quizdeck"


def test_no_s3_profile_uses_the_normal_credential_chain(monkeypatch):
    """What a deployment does: credentials come from the task or instance role."""
    factory, _ = _patch_session(monkeypatch)
    monkeypatch.setattr(s3_module, "get_settings", _settings)

    s3_module.S3ObjectStore()

    assert factory.call_args.kwargs["profile_name"] is None


def test_the_client_is_built_for_the_configured_region(monkeypatch):
    factory, _ = _patch_session(monkeypatch)
    monkeypatch.setattr(s3_module, "get_settings", lambda: _settings(aws_region="eu-west-1"))

    s3_module.S3ObjectStore()

    assert factory.return_value.client.call_args.kwargs["region_name"] == "eu-west-1"


# --- put ---------------------------------------------------------------------


def test_put_pins_the_content_type_and_a_long_cache(store):
    built, client = store

    built.put_bytes("tests/T1/q/IMG.png", PNG, "image/png")

    kwargs = client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "quizdeck-media-test"
    assert kwargs["Key"] == "tests/T1/q/IMG.png"
    assert kwargs["Body"] == PNG
    # Pinned from the key's extension by the caller, never from the upload's own
    # headers -- so S3 can never be talked into serving these as active content.
    assert kwargs["ContentType"] == "image/png"
    assert "immutable" in kwargs["CacheControl"]


def test_a_put_failure_surfaces_as_upstream_error(store):
    built, client = store
    client.put_object.side_effect = _client_error("AccessDenied")

    with pytest.raises(UpstreamError):
        built.put_bytes("tests/T1/q/IMG.png", PNG, "image/png")


# --- get ---------------------------------------------------------------------


def test_get_returns_the_body(store):
    built, client = store
    client.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=PNG))}

    assert built.get_bytes("tests/T1/q/IMG.png") == PNG


@pytest.mark.parametrize("code", ["NoSuchKey", "404", "NotFound"])
def test_a_missing_key_raises_not_found(store, code):
    """Callers (app/routers/images.py) map NotFoundError to a 404; anything else
    would leak an S3 error shape to a student."""
    built, client = store
    client.get_object.side_effect = _client_error(code)

    with pytest.raises(NotFoundError):
        built.get_bytes("tests/T1/q/IMG.png")


def test_a_permission_failure_is_not_reported_as_missing(store):
    """AccessDenied means the bucket policy is wrong, which must not masquerade
    as "no such image" -- that would send someone debugging the wrong thing."""
    built, client = store
    client.get_object.side_effect = _client_error("AccessDenied")

    with pytest.raises(UpstreamError):
        built.get_bytes("tests/T1/q/IMG.png")


# --- public_url --------------------------------------------------------------


def test_public_url_proxies_through_the_api_when_no_base_url_is_set(store):
    built, _client = store

    assert built.public_url("tests/T1/q/IMG.png") == (
        "https://api.example.com/api/v1/images/tests/T1/q/IMG.png"
    )


def test_public_url_uses_the_cdn_when_a_base_url_is_set(monkeypatch):
    _patch_session(monkeypatch)
    monkeypatch.setattr(
        s3_module,
        "get_settings",
        lambda: _settings(image_public_base_url="https://media.example.com/"),
    )

    built = s3_module.S3ObjectStore()

    # Exactly one slash, even though the configured base URL has a trailing one.
    assert built.public_url("tests/T1/q/IMG.png") == (
        "https://media.example.com/tests/T1/q/IMG.png"
    )


# --- delete ------------------------------------------------------------------


def test_delete_many_batches_and_uses_quiet_mode(store):
    built, client = store

    built.delete_many(["a.png", "b.png"])

    kwargs = client.delete_objects.call_args.kwargs
    assert kwargs["Bucket"] == "quizdeck-media-test"
    assert kwargs["Delete"]["Objects"] == [{"Key": "a.png"}, {"Key": "b.png"}]
    assert kwargs["Delete"]["Quiet"] is True


def test_delete_many_splits_batches_over_the_s3_limit(store):
    built, client = store

    built.delete_many([f"k{i}.png" for i in range(s3_module._DELETE_BATCH + 5)])

    assert client.delete_objects.call_count == 2


def test_delete_many_never_raises(store):
    """An orphaned object is a storage-cost problem; raising here would fail a
    teacher's delete after their rows are already gone."""
    built, client = store
    client.delete_objects.side_effect = _client_error("AccessDenied")

    built.delete_many(["a.png"])  # must not raise


def test_delete_many_with_no_keys_makes_no_call(store):
    built, client = store

    built.delete_many([])

    client.delete_objects.assert_not_called()


# --- client construction -----------------------------------------------------


def test_the_client_never_takes_an_endpoint_override(monkeypatch):
    """There is no S3-compatible-endpoint setting any more. Real AWS is the only
    target, and tests intercept boto3 with moto instead of redirecting it."""
    factory, _ = _patch_session(monkeypatch)
    monkeypatch.setattr(s3_module, "get_settings", _settings)

    s3_module.S3ObjectStore()

    assert "endpoint_url" not in factory.return_value.client.call_args.kwargs


# --- the shared URL helper ---------------------------------------------------


def test_both_url_shapes_agree_on_the_proxy_path():
    """app/routers/images.py is mounted at this prefix; if the helper and the
    router disagree, every proxied image 404s."""
    assert urls.PROXY_PATH == "/api/v1/images"
    assert urls.proxied_url("https://api.example.com/", "k.png") == (
        "https://api.example.com/api/v1/images/k.png"
    )


def test_a_kb_key_is_routed_to_the_authenticated_knowledge_base_path():
    """The images route is anonymous and only accepts tests/<ULID>/q/<ULID>.<ext>,
    so a kb/ key sent that way 404s. Knowledge-base documents belong to the
    token-checked route instead."""
    assert urls.KB_PROXY_PATH == "/api/v1/knowledge-base"
    assert urls.proxied_url("https://api.example.com", "kb/abc123/DOC.pdf") == (
        "https://api.example.com/api/v1/knowledge-base/kb/abc123/DOC.pdf"
    )


def test_a_kb_key_is_never_served_from_the_cdn():
    """A CDN origin has no bearer token and no ownership check, so routing a
    private teacher document there would bypass kb_belongs_to_teacher."""
    built = urls.public_url_for(
        "kb/abc123/DOC.pdf",
        api_public_origin="https://api.example.com",
        public_base_url="https://media.example.com",
    )

    assert built == "https://api.example.com/api/v1/knowledge-base/kb/abc123/DOC.pdf"
    assert "media.example.com" not in built


def test_an_image_key_still_goes_to_the_cdn_when_one_is_configured():
    """The kb/ carve-out must not disable direct serving for question images."""
    assert urls.public_url_for(
        "tests/T1/q/IMG.png",
        api_public_origin="https://api.example.com",
        public_base_url="https://media.example.com",
    ) == "https://media.example.com/tests/T1/q/IMG.png"


def test_get_settings_cache_is_untouched_by_these_tests():
    """Guard against a monkeypatched get_settings leaking into other modules,
    and against the sandbox in tests/conftest.py coming undone -- these names
    are the throwaway ones, never the dev or prod resources."""
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.table_name == "quizdeck-test"
    assert settings.s3_bucket == "quizdeck-media-test"
