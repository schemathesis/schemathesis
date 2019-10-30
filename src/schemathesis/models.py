# pylint: disable=too-many-instance-attributes
from collections import Counter
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.parse import urljoin

import attr
from hypothesis.searchstrategy import SearchStrategy

from .types import Body, Cookies, FormData, Headers, PathParameters, Query

if TYPE_CHECKING:
    import requests  # Typechecking-only import to speedup import of schemathesis


@attr.s(slots=True)  # pragma: no mutate
class Case:
    """A single test case parameters."""

    path: str = attr.ib()  # pragma: no mutate
    method: str = attr.ib()  # pragma: no mutate
    base_url: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    path_parameters: PathParameters = attr.ib(factory=dict)  # pragma: no mutate
    headers: Headers = attr.ib(factory=dict)  # pragma: no mutate
    cookies: Cookies = attr.ib(factory=dict)  # pragma: no mutate
    query: Query = attr.ib(factory=dict)  # pragma: no mutate
    body: Body = attr.ib(factory=dict)  # pragma: no mutate
    form_data: FormData = attr.ib(factory=dict)  # pragma: no mutate

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        return self.path.format(**self.path_parameters)

    def _get_base_url(self, base_url: Optional[str]) -> str:
        if base_url is None:
            if self.base_url is not None:
                base_url = self.base_url
            else:
                raise ValueError(
                    "Base URL is required as `base_url` argument in `call` or should be specified "
                    "in the schema constructor as a part of Schema URL."
                )
        return base_url

    def as_requests_kwargs(self, base_url: Optional[str] = None) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by requests."""
        base_url = self._get_base_url(base_url)
        if not base_url.endswith("/"):
            base_url += "/"
        url = urljoin(base_url, self.formatted_path.lstrip("/"))
        return {"method": self.method, "url": url, "headers": self.headers, "params": self.query, "json": self.body}

    def call(
        self, base_url: Optional[str] = None, session: Optional["requests.Session"] = None, **kwargs: Any
    ) -> "requests.Response":
        """Convert the case into a dictionary acceptable by requests."""
        # Local import to speedup import of schemathesis
        import requests  # pylint: disable=import-outside-toplevel

        if session is None:
            session = requests.Session()

        base_url = self._get_base_url(base_url)
        data = self.as_requests_kwargs(base_url)
        return session.request(**data, **kwargs)  # type: ignore


def empty_object() -> Dict[str, Any]:
    return {"properties": {}, "additionalProperties": False, "type": "object", "required": []}


@attr.s(slots=True)  # pragma: no mutate
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()  # pragma: no mutate
    method: str = attr.ib()  # pragma: no mutate
    base_url: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    path_parameters: PathParameters = attr.ib(factory=empty_object)  # pragma: no mutate
    headers: Headers = attr.ib(factory=empty_object)  # pragma: no mutate
    cookies: Cookies = attr.ib(factory=empty_object)  # pragma: no mutate
    query: Query = attr.ib(factory=empty_object)  # pragma: no mutate
    body: Body = attr.ib(factory=empty_object)  # pragma: no mutate
    form_data: FormData = attr.ib(factory=empty_object)  # pragma: no mutate

    def as_strategy(self) -> SearchStrategy:
        from ._hypothesis import get_case_strategy  # pylint: disable=import-outside-toplevel

        return get_case_strategy(self)


class Status(IntEnum):
    """Status of an action or multiple actions."""

    success = 1
    failure = 2
    error = 3


@attr.s(slots=True, repr=False)  # pragma: no mutate
class Check:
    """Single check run result."""

    name: str = attr.ib()  # pragma: no mutate
    value: Status = attr.ib()  # pragma: no mutate


@attr.s(slots=True, repr=False)  # pragma: no mutate
class TestResult:
    """Result of a single test."""

    path: str = attr.ib()  # pragma: no mutate
    method: str = attr.ib()  # pragma: no mutate
    checks: List[Check] = attr.ib(factory=list)  # pragma: no mutate

    def _add_check(self, name: str, status: Status) -> None:
        self.checks.append(Check(name, status))

    def add_success(self, name: str) -> None:
        self._add_check(name, Status.success)

    def add_failure(self, name: str) -> None:
        self._add_check(name, Status.failure)


@attr.s(slots=True, repr=False)
class TestResultSet:
    """Set of multiple test results."""

    results: List[TestResult] = attr.ib(factory=list)  # pragma: no mutate

    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0

    @property
    def has_errors(self) -> bool:
        checks_statuses = [check.value for result in self.results for check in result.checks]
        # First case: tests were collected but no checks were executed due to exception during the test
        # Second case: there are not successful checks in the results
        # pylint: disable=consider-using-ternary
        return (not checks_statuses and not self.is_empty) or any(
            status != Status.success for status in checks_statuses
        )

    @property
    def total(self) -> Dict[str, Counter]:
        output: Dict[str, Counter] = {}
        for item in self.results:
            for check in item.checks:
                output.setdefault(check.name, Counter())
                output[check.name][check.value] += 1
                output[check.name]["total"] += 1
        return output

    def append(self, item: TestResult) -> None:
        self.results.append(item)
