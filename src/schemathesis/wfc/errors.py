"""WFC-specific errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.errors import SchemathesisError

if TYPE_CHECKING:
    from jsonschema import ValidationError


class WFCError(SchemathesisError):
    """Base exception for WFC-related errors."""


class WFCLoadError(WFCError):
    """Failed to load or parse WFC authentication file."""


class WFCValidationError(WFCError):
    """WFC document validation failed."""

    @classmethod
    def from_validation_error(cls, error: ValidationError) -> WFCValidationError:
        """Convert JSON Schema validation error to WFC validation error.

        Args:
            error: JSON Schema validation error

        Returns:
            WFC validation error with formatted message

        """
        # For now, use the default error message
        # Can be extended later with custom formatting like ConfigError
        return cls(error.message)


class WFCLoginError(WFCError):
    """Login endpoint call failed."""


class WFCTokenExtractionError(WFCError):
    """Failed to extract token from login response."""
