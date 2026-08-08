import pytest

from app.services.storage.signatures import matches_declared_type

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_WEBP_BYTES = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 16
_HTML_BYTES = b"<!DOCTYPE html><html><body>not an image</body></html>"

_REAL_BYTES_BY_TYPE = {
    "image/png": _PNG_BYTES,
    "image/jpeg": _JPEG_BYTES,
    "image/webp": _WEBP_BYTES,
}


@pytest.mark.parametrize(("content_type", "data"), _REAL_BYTES_BY_TYPE.items())
def test_matches_declared_type_accepts_real_magic_bytes(content_type, data):
    assert matches_declared_type(data, content_type)


def test_png_bytes_declared_as_jpeg_are_rejected():
    assert not matches_declared_type(_PNG_BYTES, "image/jpeg")


def test_html_document_declared_as_png_is_rejected():
    assert not matches_declared_type(_HTML_BYTES, "image/png")


@pytest.mark.parametrize("content_type", _REAL_BYTES_BY_TYPE.keys())
def test_empty_input_is_rejected(content_type):
    assert not matches_declared_type(b"", content_type)


@pytest.mark.parametrize("content_type", _REAL_BYTES_BY_TYPE.keys())
def test_two_byte_input_is_rejected(content_type):
    assert not matches_declared_type(b"\x89P", content_type)


def test_unknown_content_type_is_rejected():
    assert not matches_declared_type(_PNG_BYTES, "image/gif")
