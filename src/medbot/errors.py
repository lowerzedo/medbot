from __future__ import annotations


class ApplicationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(ApplicationError):
    def __init__(self, resource: str) -> None:
        message = f"{resource.replace('_', ' ').title()} not found"
        super().__init__(f"{resource}_not_found", message, 404)


class ForbiddenError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("forbidden", "You do not have permission to perform this action", 403)


class ConflictError(ApplicationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, 409)


class UnauthenticatedError(ApplicationError):
    def __init__(self) -> None:
        super().__init__("unauthenticated", "A valid bearer token is required", 401)
