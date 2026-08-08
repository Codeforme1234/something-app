from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.schemas.results import FeedbackView, StudentDetail, TestAnalytics
from app.schemas.students import AddStudentsRequest, AddStudentsResponse, PublishRequest, SessionRow
from app.schemas.tests import TestSummary
from app.services import feedback_job, feedback_service, results_service, student_service

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


@router.get("/{test_id}/students/{session_id}", response_model=StudentDetail)
def get_student_detail(
    test_id: str, session_id: str, claims: TeacherClaims = Depends(get_current_teacher)
) -> StudentDetail:
    return results_service.get_student_detail(claims.sub, test_id, session_id)


@router.get("/{test_id}/analytics", response_model=TestAnalytics)
def get_analytics(
    test_id: str, claims: TeacherClaims = Depends(get_current_teacher)
) -> TestAnalytics:
    return results_service.get_analytics(claims.sub, test_id)


@router.post("/{test_id}/students/{session_id}/feedback/email", response_model=FeedbackView)
def email_feedback(
    test_id: str, session_id: str, claims: TeacherClaims = Depends(get_current_teacher)
) -> FeedbackView:
    return FeedbackView.from_model(feedback_service.email_feedback(claims.sub, test_id, session_id))


@router.post(
    "/{test_id}/students/{session_id}/feedback/regenerate",
    response_model=FeedbackView,
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_feedback(
    test_id: str,
    session_id: str,
    background: BackgroundTasks,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> FeedbackView:
    view = FeedbackView.from_model(feedback_service.regenerate(claims.sub, test_id, session_id))
    # Runs after the response is sent, same pattern as generate_test /
    # take.py's submit route -- it never raises.
    background.add_task(feedback_job.run, claims.sub, test_id, session_id)
    return view
