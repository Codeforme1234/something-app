"""Unit tests for support_service. The repository and email layers are
monkeypatched so these run without DynamoDB or a real send -- the outbox
round trip is covered by tests/integration/test_support_api.py instead."""

import pytest

from app.core.clock import now
from app.core.exceptions import ConflictError, NotFoundError, UpstreamError
from app.models.company import Company
from app.models.teacher import Teacher
from app.repositories import companies_repo, store, teachers_repo
from app.schemas.support import SupportCategory, SupportRequest
from app.services import support_service
from app.services.email import support as support_email


def _teacher(company_id: str | None = "COMP1") -> Teacher:
    return Teacher(sub="dev-alice", email="a@x.com", name="Alice", company_id=company_id, created_at=now())


def _company() -> Company:
    return Company(company_id="COMP1", name="Alice's company", credit_balance=10, created_at=now())


def _payload(**overrides) -> SupportRequest:
    defaults = {"category": SupportCategory.bug, "subject": "Broken button", "message": "It doesn't work"}
    defaults.update(overrides)
    return SupportRequest(**defaults)


def test_sends_with_the_callers_own_identity(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher())
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(_company(), 1))
    calls = []
    monkeypatch.setattr(
        support_email, "send_support_request", lambda **kwargs: calls.append(kwargs)
    )

    result = support_service.submit_support_request("dev-alice", _payload())

    assert result.status == "sent"
    assert calls == [
        {
            "category": SupportCategory.bug,
            "subject": "Broken button",
            "message": "It doesn't work",
            "admin_name": "Alice",
            "admin_email": "a@x.com",
            "company_name": "Alice's company",
        }
    ]


def test_missing_teacher_raises_conflict(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: None)

    with pytest.raises(ConflictError):
        support_service.submit_support_request("dev-nobody", _payload())


def test_teacher_without_company_raises_conflict(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher(company_id=None))

    with pytest.raises(ConflictError):
        support_service.submit_support_request("dev-alice", _payload())


def test_missing_company_record_raises_not_found(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher())
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: None)

    with pytest.raises(NotFoundError):
        support_service.submit_support_request("dev-alice", _payload())


def test_send_failure_raises_upstream_error(monkeypatch):
    monkeypatch.setattr(teachers_repo, "get_teacher", lambda sub: _teacher())
    monkeypatch.setattr(companies_repo, "get_company", lambda cid: store.Stored(_company(), 1))

    def _boom(**kwargs):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(support_email, "send_support_request", _boom)

    with pytest.raises(UpstreamError):
        support_service.submit_support_request("dev-alice", _payload())
