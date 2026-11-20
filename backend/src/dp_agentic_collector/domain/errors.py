from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code = "domain_error"
    status = 400

    def __init__(self, message: str = "", **details: Any) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details


class NotFound(DomainError):
    code = "not_found"
    status = 404


class PermissionDenied(DomainError):
    code = "permission_denied"
    status = 403


class Unauthenticated(DomainError):
    code = "unauthenticated"
    status = 401


class InvalidTransition(DomainError):
    code = "invalid_transition"
    status = 409


class VersionConflict(DomainError):
    """Raised when `based_on` is not the latest version. Never overwrite silently."""

    code = "version_conflict"
    status = 409


class TenantViolation(DomainError):
    code = "tenant_violation"
    status = 403


class ValidationFailed(DomainError):
    code = "validation_failed"
    status = 422


class ConfirmationRequired(DomainError):
    code = "confirmation_required"
    status = 409


class LimitExceeded(DomainError):
    code = "limit_exceeded"
    status = 413


class ConnectionUnavailable(DomainError):
    code = "connection_unavailable"
    status = 424


class RateLimited(DomainError):
    code = "rate_limited"
    status = 429
