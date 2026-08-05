from app.models.company import Company
from app.repositories import keys, store

COMPANY_ENTITY = "COMPANY"


def create_company(company: Company) -> None:
    store.put_new(keys.company_pk(company.company_id), keys.PROFILE_SK, COMPANY_ENTITY, company)


def get_company(company_id: str) -> store.Stored[Company] | None:
    return store.get(keys.company_pk(company_id), keys.PROFILE_SK, Company)
