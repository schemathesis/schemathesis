"""WFC-specific errors."""

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


class WFCTokenExtractionError(WFCError):
    """Failed to extract a token from the login response."""
