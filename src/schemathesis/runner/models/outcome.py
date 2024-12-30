from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from schemathesis.core.failures import Failure
from schemathesis.core.transport import Response
from schemathesis.runner import Status

from .check import Check
from .transport import Interaction, Request

if TYPE_CHECKING:
    import unittest

    import requests

    from schemathesis.core.control import SkipTest
    from schemathesis.generation.case import Case


@dataclass(repr=False)
class TestResult:
    """Result of a single test."""

    __test__ = False

    label: str
    checks: list[Check] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    skip_reason: str | None = None

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    def asdict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "checks": [check.asdict() for check in self.checks],
            "interactions": [interaction.asdict() for interaction in self.interactions],
            "skip_reason": self.skip_reason,
        }

    def mark_skipped(self, exc: SkipTest | unittest.case.SkipTest) -> None:
        self.skip_reason = str(exc)

    def add_success(self, *, name: str, case: Case, request: Request, response: Response) -> Check:
        check = Check(name=name, status=Status.SUCCESS, request=request, response=response, case=case)
        self.checks.append(check)
        return check

    def add_failure(self, *, name: str, case: Case, request: Request, response: Response, failure: Failure) -> Check:
        check = Check(
            name=name,
            status=Status.FAILURE,
            case=case,
            request=request,
            response=response,
            failure=failure,
        )
        self.checks.append(check)
        return check

    def store_requests_response(
        self,
        case: Case,
        response: Response | None,
        status: Status,
        checks: list[Check],
        session: requests.Session,
    ) -> None:
        self.interactions.append(Interaction.from_requests(case, response, status, checks, session))
