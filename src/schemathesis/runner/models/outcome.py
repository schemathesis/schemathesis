from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator

from ..errors import EngineErrorInfo
from .check import Check
from .status import Status
from .transport import Interaction, Request, Response

if TYPE_CHECKING:
    import unittest

    import requests
    from hypothesis.vendor.pretty import RepresentationPrinter

    from ...exceptions import FailureContext, OperationSchemaError, SkipTest
    from ...generation import DataGenerationMethod
    from ...models import Case
    from ...transports.responses import WSGIResponse


@dataclass(repr=False)
class TestResultSet:
    __test__ = False

    seed: int | None
    results: list[TestResult] = field(default_factory=list)
    generic_errors: list[OperationSchemaError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def _repr_pretty_(self, printer: RepresentationPrinter, cycle: bool) -> None:
        return None

    def __iter__(self) -> Iterator[TestResult]:
        return iter(self.results)

    @property
    def is_empty(self) -> bool:
        """If the result set contains no results."""
        return len(self.results) == 0 and len(self.generic_errors) == 0

    @property
    def has_failures(self) -> bool:
        """If any result has any failures."""
        return any(result.has_failures for result in self)

    @property
    def has_errors(self) -> bool:
        """If any result has any errors."""
        return self.errored_count > 0

    @property
    def has_logs(self) -> bool:
        """If any result has any captured logs."""
        return any(result.has_logs for result in self)

    def _count(self, predicate: Callable) -> int:
        return sum(1 for result in self if predicate(result))

    @property
    def passed_count(self) -> int:
        return self._count(lambda result: not result.has_errors and not result.is_skipped and not result.has_failures)

    @property
    def skipped_count(self) -> int:
        return self._count(lambda result: result.is_skipped)

    @property
    def failed_count(self) -> int:
        return self._count(lambda result: result.has_failures and not result.is_errored)

    @property
    def errored_count(self) -> int:
        return self._count(lambda result: result.has_errors or result.is_errored) + len(self.generic_errors)

    @property
    def total(self) -> dict[str, dict[str | Status, int]]:
        """An aggregated statistic about test results."""
        output: dict[str, dict[str | Status, int]] = {}
        for item in self.results:
            for check in item.checks:
                output.setdefault(check.name, Counter())
                output[check.name][check.value] += 1
                output[check.name]["total"] += 1
        # Avoid using Counter, since its behavior could harm in other places:
        # `if not total["unknown"]:` - this will lead to the branch execution
        # It is better to let it fail if there is a wrong key
        return {key: dict(value) for key, value in output.items()}

    def append(self, item: TestResult) -> None:
        """Add a new item to the results list."""
        self.results.append(item)

    def add_warning(self, warning: str) -> None:
        """Add a new warning to the warnings list."""
        self.warnings.append(warning)


@dataclass(repr=False)
class TestResult:
    """Result of a single test."""

    __test__ = False

    method: str
    path: str
    verbose_name: str
    data_generation_method: list[DataGenerationMethod]
    checks: list[Check] = field(default_factory=list)
    errors: list[EngineErrorInfo] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    is_errored: bool = False
    is_flaky: bool = False
    is_skipped: bool = False
    skip_reason: str | None = None
    is_executed: bool = False

    def _repr_pretty_(self, printer: RepresentationPrinter, cycle: bool) -> None:
        return None

    def asdict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "verbose_name": self.verbose_name,
            "data_generation_method": [method.as_short_name() for method in self.data_generation_method],
            "checks": [check.asdict() for check in self.checks],
            "errors": [error.asdict() for error in self.errors],
            "interactions": [interaction.asdict() for interaction in self.interactions],
            "logs": self.logs,
            "is_errored": self.is_errored,
            "is_flaky": self.is_flaky,
            "is_skipped": self.is_skipped,
            "skip_reason": self.skip_reason,
            "is_executed": self.is_executed,
        }

    def mark_errored(self) -> None:
        self.is_errored = True

    def mark_flaky(self) -> None:
        self.is_flaky = True

    def mark_skipped(self, exc: SkipTest | unittest.case.SkipTest | None) -> None:
        self.is_skipped = True
        if exc is not None:
            self.skip_reason = str(exc)

    def mark_executed(self) -> None:
        self.is_executed = True

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_failures(self) -> bool:
        return any(check.value == Status.failure for check in self.checks)

    @property
    def has_logs(self) -> bool:
        return bool(self.logs)

    def add_success(self, *, name: str, case: Case, request: Request, response: Response) -> Check:
        check = Check(name=name, value=Status.success, request=request, response=response, case=case)
        self.checks.append(check)
        return check

    def add_failure(
        self,
        *,
        name: str,
        case: Case,
        request: Request,
        response: Response | None,
        message: str,
        context: FailureContext | None,
    ) -> Check:
        check = Check(
            name=name,
            value=Status.failure,
            response=response,
            case=case,
            message=message,
            context=context,
            request=request,
        )
        self.checks.append(check)
        return check

    def add_error(self, exception: Exception) -> None:
        self.errors.append(EngineErrorInfo(exception))

    def store_requests_response(
        self,
        case: Case,
        response: requests.Response | None,
        status: Status,
        checks: list[Check],
        headers: dict[str, Any] | None,
        session: requests.Session | None,
    ) -> None:
        self.interactions.append(Interaction.from_requests(case, response, status, checks, headers, session))

    def store_wsgi_response(
        self,
        case: Case,
        response: WSGIResponse | None,
        headers: dict[str, Any],
        elapsed: float | None,
        status: Status,
        checks: list[Check],
    ) -> None:
        self.interactions.append(Interaction.from_wsgi(case, response, headers, elapsed, status, checks))