from datetime import datetime

from pydantic import BaseModel


class Company(BaseModel):
    """The paying customer. Each admin belongs to exactly one company, and
    every test-creation credit is spent from the company's balance, not the
    admin's -- multiple admins under one company would share the same pool
    if this app ever supports that; today one admin still means one company."""

    company_id: str
    name: str
    credit_balance: int
    created_at: datetime
    # Separate pool for AI generation, on top of the test-creation credit above:
    # an AI run costs real money, so it is metered independently.
    #
    # `None` means "never granted" and is distinct from 0, which means "granted
    # and spent". teachers_repo backfills a None on the next /me call; without
    # the sentinel that backfill would keep refilling a drained balance.
    # Optional at all because companies provisioned before AI runs existed must
    # still deserialize -- same reason as Teacher.company_id.
    ai_credit_balance: int | None = None
