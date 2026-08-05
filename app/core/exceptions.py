class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = ""):
        self.message = message or self.__class__.__name__
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class GoneError(AppError):
    status_code = 410
    code = "gone"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"


class InsufficientCreditsError(AppError):
    status_code = 402
    code = "insufficient_credits"
