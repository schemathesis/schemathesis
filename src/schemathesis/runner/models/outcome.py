from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from schemathesis.core.transport import Response
from schemathesis.runner import Status

from .check import Check
from .transport import Interaction

if TYPE_CHECKING:
    import requests

    from schemathesis.generation.case import Case


@dataclass(repr=False)
class TestResult:
    """Result of a single test."""

    __test__ = False

    label: str
    interactions: list[Interaction] = field(default_factory=list)

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def checks(self) -> list[Check]:
        return sum((interaction.checks for interaction in self.interactions), [])

    def record(
        self, case: Case, response: Response | None, status: Status, checks: list[Check], session: requests.Session
    ) -> None:
        self.interactions.append(Interaction.from_requests(case, response, status, checks, session))
