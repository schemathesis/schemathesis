from __future__ import annotations

from typing import Protocol

from schemathesis.core.warnings import SchemathesisWarning


class SchemaWarning(Protocol):
    """Shared interface for static schema analysis warnings."""

    operation_label: str | None

    @property
    def kind(self) -> SchemathesisWarning: ...  # pragma: no cover

    @property
    def message(self) -> str: ...  # pragma: no cover

    @property
    def group(self) -> str | None:
        """Key that collapses warnings sharing the same cause into one display block."""
        ...  # pragma: no cover
