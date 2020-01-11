# pylint: disable=too-many-instance-attributes
from collections import Counter
from contextlib import contextmanager
from enum import IntEnum
from logging import LogRecord
from typing import TYPE_CHECKING, Any, Callable, Dict, Generator, Iterator, List, Optional, Tuple, Union
from urllib.parse import urljoin

import attr
import requests
import werkzeug
from hypothesis.strategies import SearchStrategy

from .checks import ALL_CHECKS
from .exceptions import InvalidSchema
from .types import Body, Cookies, FormData, Headers, PathParameters, Query
from .utils import WSGIResponse

if TYPE_CHECKING:
    from .schemas import BaseSchema


@attr.s(slots=True)  # pragma: no mutate
class Case:
    """A single test case parameters."""

    endpoint: "Endpoint" = attr.ib()  # pragma: no mutate
    path_parameters: Optional[PathParameters] = attr.ib(default=None)  # pragma: no mutate
    headers: Optional[Headers] = attr.ib(default=None)  # pragma: no mutate
    cookies: Optional[Cookies] = attr.ib(default=None)  # pragma: no mutate
    query: Optional[Query] = attr.ib(default=None)  # pragma: no mutate
    body: Optional[Body] = attr.ib(default=None)  # pragma: no mutate
    form_data: Optional[FormData] = attr.ib(default=None)  # pragma: no mutate

    @property
    def path(self) -> str:
        return self.endpoint.path

    @property
    def method(self) -> str:
        return self.endpoint.method

    @property
    def base_url(self) -> Optional[str]:
        return self.endpoint.base_url

    @property
    def app(self) -> Any:
        return self.endpoint.app

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        try:
            return self.path.format(**self.path_parameters or {})
        except KeyError:
            raise InvalidSchema("Missing required property `required: true`")

    def get_code_to_reproduce(self) -> str:
        """Construct a Python code to reproduce this case with `requests`."""
        base_url = self.base_url or "http://localhost"
        kwargs = self.as_requests_kwargs(base_url)
        method = kwargs["method"].lower()

        def are_defaults(key: str, value: Optional[Dict]) -> bool:
            default_value: Optional[Dict] = {"json": None}.get(key, None)
            return value == default_value

        printed_kwargs = ", ".join(
            f"{key}={value}"
            for key, value in kwargs.items()
            if key not in ("method", "url") and not are_defaults(key, value)
        )
        args_repr = f"'{kwargs['url']}'"
        if printed_kwargs:
            args_repr += f", {printed_kwargs}"
        return f"requests.{method}({args_repr})"

    def _get_base_url(self, base_url: Optional[str] = None) -> str:
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
        formatted_path = self.formatted_path.lstrip("/")  # pragma: no mutate
        url = urljoin(base_url + "/", formatted_path)
        # Form data and body are mutually exclusive
        extra: Dict[str, Optional[Union[Dict, bytes]]]
        if self.form_data:
            extra = {"files": self.form_data}
        elif isinstance(self.body, bytes):
            extra = {"data": self.body}
        else:
            extra = {"json": self.body}
        return {
            "method": self.method,
            "url": url,
            "cookies": self.cookies,
            "headers": self.headers,
            "params": self.query,
            **extra,
        }

    def call(
        self, base_url: Optional[str] = None, session: Optional[requests.Session] = None, **kwargs: Any
    ) -> requests.Response:
        """Make a network call with `requests`."""
        if session is None:
            session = requests.Session()
            close_session = True
        else:
            close_session = False

        base_url = self._get_base_url(base_url)
        data = self.as_requests_kwargs(base_url)
        response = session.request(**data, **kwargs)  # type: ignore
        if close_session:
            session.close()
        return response

    def as_werkzeug_kwargs(self) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by werkzeug.Client."""
        headers = self.headers
        extra: Dict[str, Optional[Union[Dict, bytes]]]
        if self.form_data:
            extra = {"data": self.form_data}
            headers = headers or {}
            headers.setdefault("Content-Type", "multipart/form-data")
        elif isinstance(self.body, bytes):
            extra = {"data": self.body}
        else:
            extra = {"json": self.body}
        return {
            "method": self.method,
            "path": self.formatted_path,
            "headers": headers,
            "query_string": self.query,
            **extra,
        }

    def call_wsgi(self, app: Any = None, headers: Optional[Dict[str, str]] = None, **kwargs: Any) -> WSGIResponse:
        application = app or self.app
        if application is None:
            raise RuntimeError(
                "WSGI application instance is required. "
                "Please, set `app` argument in the schema constructor or pass it to `call_wsgi`"
            )
        data = self.as_werkzeug_kwargs()
        if headers:
            data["headers"] = data["headers"] or {}
            data["headers"].update(headers)
        client = werkzeug.Client(application, WSGIResponse)
        with cookie_handler(client, self.cookies):
            return client.open(**data, **kwargs)

    def validate_response(
        self,
        response: Union[requests.Response, WSGIResponse],
        checks: Tuple[Callable[[Union[requests.Response, WSGIResponse], "Case"], None], ...] = ALL_CHECKS,
    ) -> None:
        errors = []
        for check in checks:
            try:
                check(response, self)
            except AssertionError as exc:
                errors.append(exc.args[0])
        if errors:
            raise AssertionError(*errors)


@contextmanager
def cookie_handler(client: werkzeug.Client, cookies: Optional[Cookies]) -> Generator[None, None, None]:
    """Set cookies required for a call."""
    if not cookies:
        yield
    else:
        for key, value in cookies.items():
            client.set_cookie("localhost", key, value)
        yield
        for key in cookies:
            client.delete_cookie("localhost", key)


def empty_object() -> Dict[str, Any]:
    return {"properties": {}, "additionalProperties": False, "type": "object", "required": []}


@attr.s(slots=True)  # pragma: no mutate
class Endpoint:
    """A container that could be used for test cases generation."""

    path: str = attr.ib()  # pragma: no mutate
    method: str = attr.ib()  # pragma: no mutate
    definition: Dict[str, Any] = attr.ib()  # pragma: no mutate
    schema: "BaseSchema" = attr.ib()  # pragma: no mutate
    app: Any = attr.ib(default=None)  # pragma: no mutate
    base_url: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    path_parameters: Optional[PathParameters] = attr.ib(default=None)  # pragma: no mutate
    headers: Optional[Headers] = attr.ib(default=None)  # pragma: no mutate
    cookies: Optional[Cookies] = attr.ib(default=None)  # pragma: no mutate
    query: Optional[Query] = attr.ib(default=None)  # pragma: no mutate
    body: Optional[Body] = attr.ib(default=None)  # pragma: no mutate
    form_data: Optional[FormData] = attr.ib(default=None)  # pragma: no mutate

    def as_strategy(self) -> SearchStrategy:
        from ._hypothesis import get_case_strategy  # pylint: disable=import-outside-toplevel

        return get_case_strategy(self)


class Status(IntEnum):
    """Status of an action or multiple actions."""

    success = 1  # pragma: no mutate
    failure = 2  # pragma: no mutate
    error = 3  # pragma: no mutate


@attr.s(slots=True, repr=False)  # pragma: no mutate
class Check:
    """Single check run result."""

    name: str = attr.ib()  # pragma: no mutate
    value: Status = attr.ib()  # pragma: no mutate
    example: Optional[Case] = attr.ib(default=None)  # pragma: no mutate
    message: Optional[str] = attr.ib(default=None)  # pragma: no mutate


@attr.s(slots=True, repr=False)  # pragma: no mutate
class TestResult:
    """Result of a single test."""

    endpoint: Endpoint = attr.ib()  # pragma: no mutate
    checks: List[Check] = attr.ib(factory=list)  # pragma: no mutate
    errors: List[Tuple[Exception, Optional[Case]]] = attr.ib(factory=list)  # pragma: no mutate
    logs: List[LogRecord] = attr.ib(factory=list)  # pragma: no mutate
    is_errored: bool = attr.ib(default=False)  # pragma: no mutate
    seed: Optional[int] = attr.ib(default=None)  # pragma: no mutate

    def mark_errored(self) -> None:
        self.is_errored = True

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def has_failures(self) -> bool:
        return any(check.value == Status.failure for check in self.checks)

    @property
    def has_logs(self) -> bool:
        return bool(self.logs)

    def add_success(self, name: str, example: Case) -> None:
        self.checks.append(Check(name, Status.success, example))

    def add_failure(self, name: str, example: Case, message: str) -> None:
        self.checks.append(Check(name, Status.failure, example, message))

    def add_error(self, exception: Exception, example: Optional[Case] = None) -> None:
        self.errors.append((exception, example))


@attr.s(slots=True, repr=False)  # pragma: no mutate
class TestResultSet:
    """Set of multiple test results."""

    results: List[TestResult] = attr.ib(factory=list)  # pragma: no mutate

    def __iter__(self) -> Iterator[TestResult]:
        return iter(self.results)

    @property
    def is_empty(self) -> bool:
        """If the result set contains no results."""
        return len(self.results) == 0

    @property
    def has_failures(self) -> bool:
        """If any result has any failures."""
        return any(result.has_failures for result in self)

    @property
    def has_errors(self) -> bool:
        """If any result has any errors."""
        return any(result.has_errors for result in self)

    @property
    def has_logs(self) -> bool:
        """If any result has any captured logs."""
        return any(result.has_logs for result in self)

    def _count(self, predicate: Callable) -> int:
        return sum(1 for result in self if predicate(result))

    @property
    def passed_count(self) -> int:
        return self._count(lambda result: not result.has_errors and not result.has_failures)

    @property
    def failed_count(self) -> int:
        return self._count(lambda result: result.has_failures and not result.is_errored)

    @property
    def errored_count(self) -> int:
        return self._count(lambda result: result.has_errors or result.is_errored)

    @property
    def total(self) -> Dict[str, Dict[Union[str, Status], int]]:
        """Aggregated statistic about test results."""
        output: Dict[str, Dict[Union[str, Status], int]] = {}
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
