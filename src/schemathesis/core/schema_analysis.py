from __future__ import annotations

from typing import Protocol

from schemathesis.config import SchemathesisWarning


class SchemaWarning(Protocol):
    """Shared interface for static schema analysis warnings."""

    operation_label: str | None

    @property
    def kind(self) -> SchemathesisWarning: ...

    @property
    def message(self) -> str: ...
