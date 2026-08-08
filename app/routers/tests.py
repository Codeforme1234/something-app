from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, status

from app.auth.dependencies import get_current_teacher
from app.auth.protocol import TeacherClaims
from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.schemas.tests import (
    CreateTestRequest,
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    PutQuestionsRequest,
    QuestionImageUploadResponse,
    TestDetail,
    TestSummary,
    UpdateTestRequest,
)
from app.services import generation_job, test_service

router = APIRouter(prefix="/tests", tags=["tests"])


@router.post("", response_model=TestSummary, status_code=status.HTTP_201_CREATED)
def create_test(
    payload: CreateTestRequest, claims: TeacherClaims = Depends(get_current_teacher)
) -> TestSummary:
    return test_service.create_test(claims.sub, payload)


@router.post("/generate", response_model=TestSummary, status_code=status.HTTP_202_ACCEPTED)
def generate_test(
    payload: GenerateQuestionsRequest,
    background: BackgroundTasks,
    claims: TeacherClaims = Depends(get_current_teacher),
) -> TestSummary:
    """Start the "Generate with AI" workflow and return immediately.

    202, not 201, and a TestSummary rather than a TestDetail: the test exists and
    the credits are spent, but the questions do not exist yet. Extracting a real
    paper takes minutes, so waiting for them would time out the request and give
    the teacher a spinner with nothing behind it.

    The returned test is `generating`. The dashboard polls until it turns into a
    draft, or into generation_failed -- in which case the credits have been
    given back (app/services/generation_job.py).
    """
    summary = generation_job.start(claims.sub, payload)
    # Runs after the response is sent. `run` is sync, so Starlette hands it to
    # the threadpool rather than blocking the event loop for the whole
    # extraction. It never raises: every outcome is recorded on the test.
    background.add_task(generation_job.run, claims.sub, summary.test_id, payload)
    return summary


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


# The only `async def` in this file: UploadFile.read is a coroutine, so the
# handler has to be awaitable. Every other route here stays `def` because boto3
# is blocking and FastAPI runs sync handlers on the threadpool.
@router.post(
    "/{test_id}/question-images",
    response_model=QuestionImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_question_image(
    test_id: str,
    file: UploadFile = File(...),
    claims: TeacherClaims = Depends(get_current_teacher),
) -> QuestionImageUploadResponse:
    max_bytes = get_settings().max_image_bytes
    # Read one byte past the limit so an oversized upload is detectable. The
    # body-size middleware in app/main.py only inspects Content-Length, which a
    # client can understate, so this is the real ceiling.
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BadRequestError(f"image must be at most {max_bytes // 1_000_000} MB")
    return test_service.upload_question_image(claims.sub, test_id, file.content_type, data)
