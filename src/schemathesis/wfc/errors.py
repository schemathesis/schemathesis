from __future__ import annotations

from schemathesis.core.errors import SchemathesisError


class WFCError(SchemathesisError):
    """Base exception for WFC-related errors."""


class WFCLoadError(WFCError):
    """Failed to load or parse a WFC authentication file."""


class WFCValidationError(WFCError):
    """WFC document validation failed."""


class WFCLoginError(WFCError):
    """Login endpoint call failed."""


class WFCLoginUnavailable(WFCLoginError):
    """Login endpoint could not be reached or answered that it cannot serve yet."""


class WFCTokenExtractionError(WFCError):
    """Failed to extract a token from the login response."""
