"""How a stored key becomes a URL a browser can fetch.

Shared by both ObjectStore implementations so they can never disagree about the
proxied path -- app/routers/images.py has to be able to serve whatever they
produce.

Two shapes, chosen by whether `image_public_base_url` is set:

  proxied  ->  {api_public_origin}/api/v1/images/{key}
               The object store stays fully private. Every image byte crosses
               the API, which costs bandwidth but needs no bucket policy, no
               CDN, and no public access of any kind. This is the default.

  direct   ->  {image_public_base_url}/{key}
               Served straight from a CloudFront distribution or the bucket's
               own public URL. Keeps student traffic off the API entirely, at
               the cost of the tests/ prefix being publicly readable by anyone
               holding the (unguessable ULID) key.

Only the KEY is ever stored, so moving between the two is a config change with
no data migration -- which is the whole reason this indirection exists.

Both of those describe question images. Knowledge-base documents are a
different case and get neither shape: they are served by
app/routers/knowledge_base.py, which requires a bearer token and checks the key
against the caller (storage.keys.kb_belongs_to_teacher), whereas
app/routers/images.py is deliberately anonymous. So a kb/ key routed the image
way is wrong twice over -- it 404s today because the images route rejects any
key that is not tests/<ULID>/q/<ULID>.<ext>, and the moment
IMAGE_PUBLIC_BASE_URL is set it would instead hand out a *public CDN URL for a
private teacher document*, silently bypassing the ownership check. kb/ keys are
therefore always proxied, and never sent to the CDN.
"""

from app.services.storage.keys import KB_PREFIX

#: Must match the prefix app/routers/images.py is mounted at.
PROXY_PATH = "/api/v1/images"
#: Must match the prefix app/routers/knowledge_base.py is mounted at.
KB_PROXY_PATH = "/api/v1/knowledge-base"


def _is_kb_key(key: str) -> bool:
    return key.startswith(f"{KB_PREFIX}/")


def proxied_url(api_public_origin: str, key: str) -> str:
    path = KB_PROXY_PATH if _is_kb_key(key) else PROXY_PATH
    return f"{api_public_origin.rstrip('/')}{path}/{key}"


def direct_url(public_base_url: str, key: str) -> str:
    return f"{public_base_url.rstrip('/')}/{key}"


def public_url_for(key: str, *, api_public_origin: str, public_base_url: str | None) -> str:
    # A CDN origin serves whatever it is asked for, with no bearer token and no
    # ownership check, so a private document must never be addressed that way.
    if public_base_url and not _is_kb_key(key):
        return direct_url(public_base_url, key)
    return proxied_url(api_public_origin, key)
