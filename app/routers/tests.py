from fastapi import APIRouter, Depends, status

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.schemas.tests import (
    CreateTestRequest,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    PutQuestionsRequest,
    TestDetail,
    TestSummary,
    UpdateTestRequest,
)
from app.services import test_service

router = APIRouter(prefix="/tests", tags=["tests"])


@router.post("", response_model=TestSummary, status_code=status.HTTP_201_CREATED)
def create_test(
    payload: CreateTestRequest, claims: TeacherClaims = Depends(get_current_teacher)
) -> TestSummary:
    return test_service.create_test(claims.sub, payload)


@router.get("", response_model=list[TestSummary])
def list_tests(claims: TeacherClaims = Depends(get_current_teacher)) -> list[TestSummary]:
    return test_service.list_tests(claims.sub)


@router.get("/{test_id}", response_model=TestDetail)
def get_test(test_id: str, claims: TeacherClaims = Depends(get_current_teacher)) -> TestDetail:
    return test_service.get_test_detail(claims.sub, test_id)


@router.patch("/{test_id}", response_model=TestSummary)
def update_test(
    test_id: str,
    payload: UpdateTestRequest,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> TestSummary:
    return test_service.update_test(claims.sub, test_id, payload)


@router.delete("/{test_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test(test_id: str, claims: TeacherClaims = Depends(get_current_teacher)) -> None:
    test_service.delete_test(claims.sub, test_id)


@router.put("/{test_id}/questions", response_model=TestDetail)
def put_questions(
    test_id: str,
    payload: PutQuestionsRequest,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> TestDetail:
    return test_service.replace_questions(claims.sub, test_id, payload)


@router.post("/{test_id}/generate-questions", response_model=GenerateQuestionsResponse)
def generate_questions(
    test_id: str,
    payload: GenerateQuestionsRequest,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> GenerateQuestionsResponse:
    return test_service.generate_questions(claims.sub, test_id, payload)
