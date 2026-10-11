"""Typed errors. Every tool turns these into a structured error payload, never into a guess."""

from __future__ import annotations

from typing import Any


class FoncierError(Exception):
    code = "internal_error"
    retryable = False

    def __init__(self, message: str, *, source: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.source is not None:
            payload["source"] = self.source
        if self.details is not None:
            payload["details"] = self.details
        return payload


class InvalidInput(FoncierError):
    """The caller sent something the tool cannot interpret. Nothing was fetched."""

    code = "invalid_input"


class SourceUnavailable(FoncierError):
    """The upstream public API did not answer, or answered with a server error."""

    code = "source_unavailable"
    retryable = True


class NotFound(FoncierError):
    """The source answered, and has no record for this input."""

    code = "not_found"


class NotCovered(FoncierError):
    """The source does not cover this territory (for instance DVF in Alsace-Moselle)."""

    code = "not_covered"


class OfflineCacheMiss(FoncierError):
    """Offline mode is on and the response is not in the cache."""

    code = "offline_cache_miss"


class Unauthorized(FoncierError):
    code = "unauthorized"


class QuotaExceeded(FoncierError):
    code = "quota_exceeded"
    retryable = True


class FeatureDisabled(FoncierError):
    code = "feature_disabled"
