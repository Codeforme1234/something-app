from fastapi import APIRouter, Depends, status

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.schemas.students import AddStudentsRequest, AddStudentsResponse, PublishRequest, SessionRow
from app.schemas.tests import TestSummary
from app.services import student_service

router = APIRouter(prefix="/tests", tags=["students"])


@router.post(
    "/{test_id}/students", response_model=AddStudentsResponse, status_code=status.HTTP_201_CREATED
)
def add_students(
    test_id: str,
    payload: AddStudentsRequest,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> AddStudentsResponse:
    return student_service.add_students(claims.sub, test_id, payload)


@router.get("/{test_id}/students", response_model=list[SessionRow])
def list_students(
    test_id: str, claims: TeacherClaims = Depends(get_current_teacher)
) -> list[SessionRow]:
    return student_service.list_students(claims.sub, test_id)


@router.post("/{test_id}/publish", response_model=TestSummary)
def publish_test(
    test_id: str,
    payload: PublishRequest,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> TestSummary:
    return student_service.publish_test(claims.sub, test_id, payload)
