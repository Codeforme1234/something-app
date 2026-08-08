"""Integration tests for question-image upload and serving. Backed by moto
(see tests/conftest.py), so uploads go to a mocked S3 bucket rather than a
local directory; conftest.py creates the table and bucket.

The serve route is anonymous and reads bytes off a path built from user input,
so a good half of these tests are about what it must REFUSE.
"""

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# A real 1x1 PNG, not just the signature -- keeps these tests honest if the
# validation ever grows beyond a magic-byte check.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
# RIFF <4-byte little-endian size> WEBP -- the format tag sits at bytes 8:12.
WEBP_BYTES = b"RIFF" + (16).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 8
HTML_BYTES = b"<html><script>alert(document.cookie)</script></html>"


@pytest.fixture(autouse=True)
def _empty_bucket():
    """Each test starts with an empty bucket.

    This used to point OBJECT_DIR at a tmp_path, which no longer exists: there
    is no local object store, and the bucket is moto's, created once per
    session by conftest.py. Emptying it per test keeps the isolation that
    fixture provided -- notably so a leftover object cannot make a "the image
    was swept" assertion pass for the wrong reason.
    """
    yield
    import boto3

    from tests.conftest import TEST_BUCKET, TEST_REGION

    s3 = boto3.client("s3", region_name=TEST_REGION)
    listed = s3.list_objects_v2(Bucket=TEST_BUCKET)
    objects = [{"Key": item["Key"]} for item in listed.get("Contents", [])]
    if objects:
        s3.delete_objects(Bucket=TEST_BUCKET, Delete={"Objects": objects, "Quiet": True})


def _headers() -> dict:
    headers = {"Authorization": f"Bearer dev-{uuid.uuid4().hex[:12]}"}
    # Provisions this admin's company, exactly as the real app's AppShell does.
    client.get("/api/v1/me", headers=headers)
    return headers


def _create_test(headers: dict) -> str:
    resp = client.post("/api/v1/tests", json={"title": "Optics"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["test_id"]


def _upload(headers: dict, test_id: str, name: str, data: bytes, content_type: str):
    return client.post(
        f"/api/v1/tests/{test_id}/question-images",
        files={"file": (name, data, content_type)},
        headers=headers,
    )


def _path_of(image_url: str) -> str:
    """The serve path from an absolute image_url, so tests exercise the same URL
    the frontend would actually fetch."""
    return "/api/v1" + image_url.split("/api/v1", 1)[1]


# --- happy path ------------------------------------------------------------


def test_upload_png_returns_key_and_url():
    headers = _headers()
    test_id = _create_test(headers)

    resp = _upload(headers, test_id, "diagram.png", PNG_BYTES, "image/png")
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["image_key"].startswith(f"tests/{test_id}/q/")
    assert body["image_key"].endswith(".png")
    assert body["image_url"].endswith(body["image_key"])


@pytest.mark.parametrize(
    ("data", "declared", "extension"),
    [
        (PNG_BYTES, "image/png", ".png"),
        (JPEG_BYTES, "image/jpeg", ".jpg"),
        (WEBP_BYTES, "image/webp", ".webp"),
    ],
)
def test_each_supported_type_round_trips(data, declared, extension):
    headers = _headers()
    test_id = _create_test(headers)

    upload = _upload(headers, test_id, f"x{extension}", data, declared)
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["image_key"].endswith(extension)

    # The serve route is anonymous: no headers passed on purpose.
    served = client.get(_path_of(body["image_url"]))
    assert served.status_code == 200, served.text
    assert served.content == data
    assert served.headers["content-type"] == declared
    assert served.headers["x-content-type-options"] == "nosniff"


# --- content validation ----------------------------------------------------


def test_html_declared_as_png_is_rejected():
    """The core of why serving from our own origin is safe."""
    headers = _headers()
    test_id = _create_test(headers)

    resp = _upload(headers, test_id, "evil.png", HTML_BYTES, "image/png")
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "bad_request"


def test_png_declared_as_jpeg_is_rejected():
    headers = _headers()
    test_id = _create_test(headers)

    resp = _upload(headers, test_id, "x.jpg", PNG_BYTES, "image/jpeg")
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize("declared", ["text/plain", "image/svg+xml", "application/pdf"])
def test_unsupported_types_are_rejected(declared):
    """SVG in particular: it is script-capable, and these URLs are permanent."""
    headers = _headers()
    test_id = _create_test(headers)

    resp = _upload(headers, test_id, "x.bin", PNG_BYTES, declared)
    assert resp.status_code == 400, resp.text


def test_empty_file_is_rejected():
    headers = _headers()
    test_id = _create_test(headers)

    resp = _upload(headers, test_id, "empty.png", b"", "image/png")
    assert resp.status_code == 400, resp.text


def test_oversized_image_is_rejected(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("MAX_IMAGE_BYTES", "64")
    get_settings.cache_clear()
    try:
        headers = _headers()
        test_id = _create_test(headers)
        oversized = PNG_BYTES + b"\x00" * 200
        resp = _upload(headers, test_id, "big.png", oversized, "image/png")
        assert resp.status_code == 400, resp.text
    finally:
        get_settings.cache_clear()


# --- authorization ---------------------------------------------------------


def test_upload_requires_authentication():
    headers = _headers()
    test_id = _create_test(headers)

    resp = client.post(
        f"/api/v1/tests/{test_id}/question-images",
        files={"file": ("x.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 401


def test_upload_to_another_teachers_test_is_not_found():
    """Ownership by key (rule 3): someone else's test simply misses, so this is
    a 404 rather than a 403 -- the route never confirms the test exists."""
    owner = _headers()
    test_id = _create_test(owner)

    intruder = _headers()
    resp = _upload(intruder, test_id, "x.png", PNG_BYTES, "image/png")
    assert resp.status_code == 404, resp.text


def test_upload_to_unknown_test_is_not_found():
    headers = _headers()
    resp = _upload(headers, "01JQZZZZZZZZZZZZZZZZZZZZZZ", "x.png", PNG_BYTES, "image/png")
    assert resp.status_code == 404, resp.text


def test_upload_to_published_test_conflicts():
    headers = _headers()
    test_id = _create_test(headers)
    client.put(
        f"/api/v1/tests/{test_id}/questions",
        json={"questions": [{"stem": "2+2?", "options": ["3", "4", "5", "6"], "correct_index": 1}]},
        headers=headers,
    )
    deadline = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    published = client.post(
        f"/api/v1/tests/{test_id}/publish", json={"deadline": deadline}, headers=headers
    )
    assert published.status_code == 200, published.text

    resp = _upload(headers, test_id, "x.png", PNG_BYTES, "image/png")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict"


# --- serve route refusals --------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "tests/../../etc/passwd",
        "../../../etc/passwd",
        "tests/01JQZZZZZZZZZZZZZZZZZZZZZZ/q/../../../../etc/passwd",
        "etc/passwd",
        "tests/01JQZZZZZZZZZZZZZZZZZZZZZZ/q/01JQZZZZZZZZZZZZZZZZZZZZZZ.svg",
        "tests/01JQZZZZZZZZZZZZZZZZZZZZZZ/q/01JQZZZZZZZZZZZZZZZZZZZZZZ.png.html",
        "tests/lowercase/q/01JQZZZZZZZZZZZZZZZZZZZZZZ.png",
    ],
)
def test_serve_route_refuses_malformed_keys(key):
    resp = client.get(f"/api/v1/images/{key}")
    assert resp.status_code == 404, resp.text
    assert b"root:" not in resp.content


def test_serve_route_404s_a_wellformed_but_absent_key():
    resp = client.get(
        "/api/v1/images/tests/01JQZZZZZZZZZZZZZZZZZZZZZZ/q/01JQZZZZZZZZZZZZZZZZZZZZZY.png"
    )
    assert resp.status_code == 404, resp.text


# --- the key must survive a re-save -----------------------------------------


def test_image_key_survives_a_resave_that_remints_question_ids():
    """The regression that motivates keying images by test rather than question:
    replace_questions mints a fresh question_id on EVERY save, so an image keyed
    by question_id would be orphaned the first time a teacher hit Save."""
    headers = _headers()
    test_id = _create_test(headers)
    image_key = _upload(headers, test_id, "d.png", PNG_BYTES, "image/png").json()["image_key"]

    def put(alt: str):
        return client.put(
            f"/api/v1/tests/{test_id}/questions",
            json={
                "questions": [
                    {
                        "stem": "Which circuit is shown?",
                        "options": ["A", "B", "C", "D"],
                        "correct_index": 0,
                        "image_key": image_key,
                        "image_alt": alt,
                    }
                ]
            },
            headers=headers,
        )

    first = put("A series circuit")
    assert first.status_code == 200, first.text
    first_q = first.json()["questions"][0]
    assert first_q["image_key"] == image_key
    assert first_q["image_url"].endswith(image_key)
    assert first_q["image_alt"] == "A series circuit"

    second = put("A parallel circuit")
    assert second.status_code == 200, second.text
    second_q = second.json()["questions"][0]

    # The identity changed; the image did not.
    assert second_q["question_id"] != first_q["question_id"]
    assert second_q["image_key"] == image_key
    assert second_q["image_alt"] == "A parallel circuit"

    # And it is still actually fetchable after both saves.
    assert client.get(_path_of(second_q["image_url"])).status_code == 200


def test_put_questions_rejects_an_image_key_from_another_test():
    headers = _headers()
    mine = _create_test(headers)
    theirs = _create_test(headers)
    foreign_key = _upload(headers, theirs, "d.png", PNG_BYTES, "image/png").json()["image_key"]

    resp = client.put(
        f"/api/v1/tests/{mine}/questions",
        json={
            "questions": [
                {
                    "stem": "Borrowed image",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 0,
                    "image_key": foreign_key,
                }
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["code"] == "bad_request"


def test_put_questions_rejects_a_traversal_image_key():
    headers = _headers()
    test_id = _create_test(headers)

    resp = client.put(
        f"/api/v1/tests/{test_id}/questions",
        json={
            "questions": [
                {
                    "stem": "Nasty key",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 0,
                    "image_key": f"tests/{test_id}/q/../../../../etc/passwd",
                }
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


def test_questions_without_an_image_report_null_image_fields():
    headers = _headers()
    test_id = _create_test(headers)

    resp = client.put(
        f"/api/v1/tests/{test_id}/questions",
        json={"questions": [{"stem": "2+2?", "options": ["3", "4", "5", "6"], "correct_index": 1}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    q = resp.json()["questions"][0]
    assert q["image_key"] is None
    assert q["image_url"] is None
    assert q["image_alt"] is None


def test_blank_image_alt_is_stored_as_null():
    headers = _headers()
    test_id = _create_test(headers)
    image_key = _upload(headers, test_id, "d.png", PNG_BYTES, "image/png").json()["image_key"]

    resp = client.put(
        f"/api/v1/tests/{test_id}/questions",
        json={
            "questions": [
                {
                    "stem": "No alt given",
                    "options": ["A", "B", "C", "D"],
                    "correct_index": 0,
                    "image_key": image_key,
                    "image_alt": "   ",
                }
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["questions"][0]["image_alt"] is None


# --- the whole point: an image reaching an anonymous student -----------------


def test_image_reaches_the_student_and_the_teachers_review(monkeypatch, tmp_path):
    """End to end: a teacher attaches a diagram, a student on a token link sees
    it, and the teacher's review of that attempt shows it too. The student is
    fully anonymous here -- no bearer token on either the attempt calls or the
    image fetch."""
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    from app.core.config import get_settings

    get_settings.cache_clear()

    headers = _headers()
    test_id = _create_test(headers)
    image_key = _upload(headers, test_id, "circuit.png", PNG_BYTES, "image/png").json()["image_key"]

    put = client.put(
        f"/api/v1/tests/{test_id}/questions",
        json={
            "questions": [
                {
                    "stem": "Which circuit is shown below?",
                    "options": ["Series", "Parallel", "Neither", "Both"],
                    "correct_index": 0,
                    "image_key": image_key,
                    "image_alt": "A two-resistor circuit",
                }
            ]
        },
        headers=headers,
    )
    assert put.status_code == 200, put.text

    added = client.post(
        f"/api/v1/tests/{test_id}/students",
        json={"students": [{"name": "Ada Lovelace", "email": "ada@example.com"}]},
        headers=headers,
    )
    assert added.status_code == 201, added.text
    session_id = added.json()["added"][0]["session_id"]

    deadline = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    assert (
        client.post(f"/api/v1/tests/{test_id}/publish", json={"deadline": deadline}, headers=headers)
    ).status_code == 200

    outbox = client.get("/api/v1/dev/outbox").json()
    token = outbox[0]["student_link"].rsplit("/t/", 1)[-1]

    start = client.post(f"/api/v1/take/{token}/start")
    assert start.status_code == 200, start.text
    student_q = start.json()["questions"][0]

    assert student_q["image_url"].endswith(image_key)
    assert student_q["image_alt"] == "A two-resistor circuit"
    # The student gets the URL but never the storage key, and never the answer.
    assert "image_key" not in student_q
    assert "correct_index" not in student_q

    # Anonymous fetch of the image itself -- what the <img> tag will do.
    served = client.get(_path_of(student_q["image_url"]))
    assert served.status_code == 200
    assert served.content == PNG_BYTES

    submitted = client.post(
        f"/api/v1/take/{token}/submit", json={"answers": {student_q["question_id"]: 0}}
    )
    assert submitted.status_code == 200, submitted.text

    detail = client.get(f"/api/v1/tests/{test_id}/students/{session_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    reviewed = detail.json()["review"][0]
    assert reviewed["image_url"].endswith(image_key)
    assert reviewed["image_alt"] == "A two-resistor circuit"
    assert "image_key" not in reviewed
    assert reviewed["correct_index"] == 0

    get_settings.cache_clear()
