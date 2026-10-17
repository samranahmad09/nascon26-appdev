import psycopg
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", details: dict | None = None, headers: dict | None = None):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code
        self.details = details or {}
        self.headers = headers


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def error_response(status: int, code: str, message: str, details: dict | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message, details), headers=headers)


def validation_error(fields: list[str], message: str = "Request failed validation.") -> ApiError:
    return ApiError(400, "VALIDATION_ERROR", message, {"fields": list(fields)})


def not_found(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(404, code, message, details)


def conflict(code: str, message: str, details: dict | None = None) -> ApiError:
    return ApiError(409, code, message, details)


def install(app) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = []
        for err in exc.errors():
            loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
            name = loc[0] if loc else "body"
            if name not in fields:
                fields.append(name)
        return error_response(400, "VALIDATION_ERROR", "Request failed validation.", {"fields": fields})

    @app.exception_handler(psycopg.DataError)
    async def _bad_data(_: Request, exc: psycopg.DataError):
        # e.g. a NUL character in a path id, or a number outside the column range: bad input, not a crash.
        return error_response(400, "VALIDATION_ERROR", "A value in the request cannot be stored or looked up.",
                              {"fields": ["path"]})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE"}
        return error_response(exc.status_code, codes.get(exc.status_code, f"HTTP_{exc.status_code}"), str(exc.detail))
