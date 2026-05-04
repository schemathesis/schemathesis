from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from schemathesis.core.error_feedback.store import Observation
from schemathesis.core.registries import Registry

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation


class ResponseParser(Protocol):
    """Framework-specific extractor of structured signal from a server's 4xx body."""

    priority: int

    def can_parse(self, *, body: object) -> bool:
        """Cheap shape pre-check; the pipeline uses this to skip non-matching parsers."""
        ...  # pragma: no cover

    def parse(
        self,
        *,
        operation: APIOperation,
        body: object,
        case: Case,
    ) -> tuple[Observation, ...]:
        """Extract observations from the body. Empty tuple means no signal."""
        ...  # pragma: no cover


PARSERS: Registry[type[ResponseParser]] = Registry()


# Bundled parsers self-register on import.
from schemathesis.core.error_feedback.parsers import (  # noqa: F401, E402
    ajv,
    aspnet,
    drf,
    go_validator,
    jackson,
    laravel,
    pydantic,
    rails,
    spring,
    symfony,
    zod,
)
