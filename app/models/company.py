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
