from datetime import datetime

from pydantic import BaseModel


class Teacher(BaseModel):
    sub: str
    # Plain str, not EmailStr: this value comes from the identity provider,
    # which has already verified it. Strict validation belongs on student
    # emails, which a teacher types or uploads.
    email: str
    name: str
    created_at: datetime
