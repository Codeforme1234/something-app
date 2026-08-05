import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.routers import me, students, tests

logging.basicConfig(level=logging.INFO)

MAX_BODY_BYTES = 1_000_000


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="QuizDeck API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413, content={"code": "payload_too_large", "message": "request body too large"}
            )
        return await call_next(request)

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok", "env": settings.app_env}

    app.include_router(me.router, prefix="/api/v1")
    app.include_router(tests.router, prefix="/api/v1")
    app.include_router(students.router, prefix="/api/v1")

    if settings.app_env == "dev":
        from app.routers import dev

        app.include_router(dev.router, prefix="/api/v1")

    return app


app = create_app()
