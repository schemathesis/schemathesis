import base64
import datetime
import http
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from enum import IntEnum
from logging import LogRecord
from typing import TYPE_CHECKING, Any, Callable, Dict, Generator, Iterator, List, Optional, Sequence, Tuple, Union, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import attr
import curlify
import requests
import werkzeug
from hypothesis.strategies import SearchStrategy
from starlette.testclient import TestClient as ASGIClient

from .constants import USER_AGENT, DataGenerationMethod
from .exceptions import CheckFailed, InvalidSchema, get_grouped_exception
from .types import Body, Cookies, FormData, Headers, PathParameters, Query
from .utils import GenericResponse, WSGIResponse, get_response_payload

if TYPE_CHECKING:
    from .hooks import HookDispatcher
    from .schemas import BaseSchema
    from .stateful import Feedback, Stateful, StatefulTest


MISSING_STATEFUL_ARGUMENT_MESSAGE = (
    "To use `store_response` you need to enable stateful testing by adding "
    "`stateful=Stateful.links` to your `parametrize` call."
)


@attr.s(slots=True)  # pragma: no mutate
class CaseSource:
    """Data sources, used to generate a test case."""

    case: "Case" = attr.ib()  # pragma: no mutate
    response: GenericResponse = attr.ib()  # pragma: no mutate


@attr.s(slots=True, repr=False)  # pragma: no mutate
class Case:  # pylint: disable=too-many-public-methods
    """A single test case parameters."""

    endpoint: "Endpoint" = attr.ib()  # pragma: no mutate
    path_parameters: Optional[PathParameters] = attr.ib(default=None)  # pragma: no mutate
    headers: Optional[Headers] = attr.ib(default=None)  # pragma: no mutate
    cookies: Optional[Cookies] = attr.ib(default=None)  # pragma: no mutate
    query: Optional[Query] = attr.ib(default=None)  # pragma: no mutate
    body: Optional[Body] = attr.ib(default=None)  # pragma: no mutate
    form_data: Optional[FormData] = attr.ib(default=None)  # pragma: no mutate

    feedback: "Feedback" = attr.ib(default=None)  # pragma: no mutate
    source: Optional[CaseSource] = attr.ib(default=None)  # pragma: no mutate

    def __repr__(self) -> str:
        parts = [f"{self.__class__.__name__}("]
        first = True
        for name in ("path_parameters", "headers", "cookies", "query", "body", "form_data"):
            value = getattr(self, name)
            if value is not None:
                if first:
                    first = False
                else:
                    parts.append(", ")
                parts.extend((name, "=", repr(value)))
        return "".join(parts) + ")"

    @property
    def path(self) -> str:
        return self.endpoint.path

    @property
    def full_path(self) -> str:
        return self.endpoint.full_path

    @property
    def method(self) -> str:
        return self.endpoint.method.upper()

    @property
    def base_url(self) -> Optional[str]:
        return self.endpoint.base_url

    @property
    def app(self) -> Any:
        return self.endpoint.app

    def set_source(self, response: GenericResponse, case: "Case") -> None:
        self.source = CaseSource(case=case, response=response)

    @property
    def formatted_path(self) -> str:
        # pylint: disable=not-a-mapping
        try:
            return self.path.format(**self.path_parameters or {})
        except KeyError as variable:
            raise InvalidSchema(
                f"Missing path parameter {variable}. It either not defined or has no `required: true` in its definition"
            ) from variable

    def get_full_base_url(self) -> Optional[str]:
        """Create a full base url, adding "localhost" for WSGI apps."""
        parts = urlsplit(self.base_url)
        if not parts.hostname:
            path = cast(str, parts.path or "")
            return urlunsplit(("http", "localhost", path or "", "", ""))
        return self.base_url

    def as_text_lines(self) -> List[str]:
        """Textual representation.

        Each component is a separate line.
        """
        output = {
            "Path parameters": self.path_parameters,
            "Headers": self.headers,
            "Cookies": self.cookies,
            "Query": self.query,
            "Body": self.body,
            "Form data": self.form_data,
        }
        max_length = max(map(len, output))
        template = f"{{:<{max_length}}} : {{}}"
        return [
            template.format(key, value)
            for key, value in output.items()
            if (key == "Body" and value is not None) or value not in (None, {})
        ]

    def get_code_to_reproduce(
        self, headers: Optional[Dict[str, Any]] = None, request: Optional[requests.PreparedRequest] = None
    ) -> str:
        """Construct a Python code to reproduce this case with `requests`."""
        if request is not None:
            kwargs: Dict[str, Any] = {
                "method": request.method,
                "url": request.url,
                "headers": request.headers,
                "data": request.body,
            }
        else:
            base_url = self.get_full_base_url()
            kwargs = self.as_requests_kwargs(base_url)
        if headers:
            final_headers = kwargs["headers"] or {}
            final_headers.update(headers)
            kwargs["headers"] = final_headers
        method = kwargs["method"].lower()

        printed_kwargs = ", ".join(
            f"{key}={repr(value)}"
            for key, value in kwargs.items()
            if key not in ("method", "url") and value is not None
        )
        args_repr = f"'{kwargs['url']}'"
        if printed_kwargs:
            args_repr += f", {printed_kwargs}"
        return f"requests.{method}({args_repr})"

    def as_curl_command(self, headers: Optional[Dict[str, Any]] = None) -> str:
        """Construct a curl command for a given case."""
        base_url = self.get_full_base_url()
        kwargs = self.as_requests_kwargs(base_url)
        if headers:
            final_headers = kwargs["headers"] or {}
            final_headers.update(headers)
            kwargs["headers"] = final_headers
        request = requests.Request(**kwargs)
        return curlify.to_curl(request.prepare())

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

    def _get_headers(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        final_headers = self.headers.copy() if self.headers is not None else {}
        if headers:
            final_headers.update(headers)
        if "user-agent" not in {header.lower() for header in final_headers}:
            final_headers["User-Agent"] = USER_AGENT
        return final_headers

    def as_requests_kwargs(
        self, base_url: Optional[str] = None, headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by requests."""
        final_headers = self._get_headers(headers)
        base_url = self._get_base_url(base_url)
        formatted_path = self.formatted_path.lstrip("/")  # pragma: no mutate
        url = urljoin(base_url + "/", formatted_path)
        # Form data and body are mutually exclusive
        extra: Dict[str, Optional[Body]]
        if self.form_data:
            files, data = self.endpoint.prepare_multipart(self.form_data)
            extra = {"files": files, "data": data}
        elif is_multipart(self.body):
            extra = {"data": self.body}
        else:
            extra = {"json": self.body}
        return {
            "method": self.method,
            "url": url,
            "cookies": self.cookies,
            "headers": final_headers,
            "params": self.query,
            **extra,
        }

    def call(
        self,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
        headers: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        """Make a network call with `requests`."""
        if session is None:
            session = requests.Session()
            close_session = True
        else:
            close_session = False
        data = self.as_requests_kwargs(base_url, headers)
        data.update(kwargs)
        response = session.request(**data)  # type: ignore
        if close_session:
            session.close()
        if self.feedback:
            self.store_response(response)
        return response

    def store_response(self, response: GenericResponse) -> None:
        if self.feedback is None:
            raise RuntimeError(MISSING_STATEFUL_ARGUMENT_MESSAGE)
        self.feedback.add_test_case(self, response)

    def as_werkzeug_kwargs(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Convert the case into a dictionary acceptable by werkzeug.Client."""
        final_headers = self._get_headers(headers)
        extra: Dict[str, Optional[Body]]
        if self.form_data:
            extra = {"data": self.form_data}
            final_headers = final_headers or {}
            if "multipart/form-data" in self.endpoint.get_request_payload_content_types():
                final_headers.setdefault("Content-Type", "multipart/form-data")
        elif is_multipart(self.body):
            extra = {"data": self.body}
        else:
            extra = {"json": self.body}
        return {
            "method": self.method,
            "path": self.endpoint.schema.get_full_path(self.formatted_path),
            "headers": final_headers,
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
        data = self.as_werkzeug_kwargs(headers)
        client = werkzeug.Client(application, WSGIResponse)
        with cookie_handler(client, self.cookies):
            response = client.open(**data, **kwargs)
        requests_kwargs = self.as_requests_kwargs(base_url=self.get_full_base_url(), headers=headers)
        response.request = requests.Request(**requests_kwargs).prepare()
        if self.feedback:
            self.store_response(response)
        return response

    def call_asgi(
        self,
        app: Any = None,
        base_url: Optional[str] = "http://testserver",
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> requests.Response:
        application = app or self.app
        if application is None:
            raise RuntimeError(
                "ASGI application instance is required. "
                "Please, set `app` argument in the schema constructor or pass it to `call_asgi`"
            )
        client = ASGIClient(application)

        return self.call(base_url=base_url, session=client, headers=headers, **kwargs)

    def validate_response(
        self,
        response: GenericResponse,
        checks: Tuple["CheckFunction", ...] = (),
        additional_checks: Tuple["CheckFunction", ...] = (),
    ) -> None:
        from .checks import ALL_CHECKS  # pylint: disable=import-outside-toplevel

        checks = checks or ALL_CHECKS
        checks += additional_checks
        errors = []
        for check in checks:
            try:
                check(response, self)
            except CheckFailed as exc:
                errors.append(exc)
        if errors:
            exception_cls = get_grouped_exception(self.endpoint.verbose_name, *errors)
            formatted_errors = "\n\n".join(f"{idx}. {error.args[0]}" for idx, error in enumerate(errors, 1))
            code = self.get_code_to_reproduce(request=response.request)
            payload = get_response_payload(response)
            raise exception_cls(
                f"\n\n{formatted_errors}\n\n----------\n\nResponse payload: `{payload}`\n\n"
                f"Run this Python code to reproduce this response: \n\n    {code}\n"
            )

    def call_and_validate(
        self,
        base_url: Optional[str] = None,
        session: Optional[requests.Session] = None,
        headers: Optional[Dict[str, Any]] = None,
        checks: Tuple["CheckFunction", ...] = (),
        **kwargs: Any,
    ) -> None:
        response = self.call(base_url, session, headers, **kwargs)
        self.validate_response(response, checks)

    def get_full_url(self) -> str:
        """Make a full URL to the current endpoint, including query parameters."""
        base_url = self.base_url or "http://localhost"
        kwargs = self.as_requests_kwargs(base_url)
        request = requests.Request(**kwargs)
        prepared = requests.Session().prepare_request(request)  # type: ignore
        return prepared.url

    def partial_deepcopy(self) -> "Case":
        return self.__class__(
            endpoint=self.endpoint.partial_deepcopy(),
            path_parameters=deepcopy(self.path_parameters),
            headers=deepcopy(self.headers),
            cookies=deepcopy(self.cookies),
            query=deepcopy(self.query),
            body=deepcopy(self.body),
            form_data=deepcopy(self.form_data),
        )


def is_multipart(item: Optional[Body]) -> bool:
    """A poor detection if the body should be a multipart request.

    It traverses the structure and if it contains bytes in any value, then it is a multipart request, because
    it may happen only if there was `format: binary`, which usually is in multipart payloads.
    Probably a better way would be checking actual content types defined in `requestBody` and drive behavior based on
    that fact.
    """
    if isinstance(item, bytes):
        return True
    if isinstance(item, dict):
        for value in item.values():
            if is_multipart(value):
                return True
    if isinstance(item, list):
        for value in item:
            if is_multipart(value):
                return True
    return False


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
class EndpointDefinition:
    """A wrapper to store not resolved endpoint definitions.

    To prevent recursion errors we need to store definitions without resolving references. But endpoint definitions
    itself can be behind a reference (when there is a ``$ref`` in ``paths`` values), therefore we need to store this
    scope change to have a proper reference resolving later.
    """

    raw: Dict[str, Any] = attr.ib()  # pragma: no mutate
    resolved: Dict[str, Any] = attr.ib()  # pragma: no mutate
    scope: str = attr.ib()  # pragma: no mutate
    parameters: List[Dict[str, Any]] = attr.ib()  # pragma: no mutate


@attr.s(slots=True)  # pragma: no mutate
class Endpoint:
    """A container that could be used for test cases generation."""

    # `path` does not contain `basePath`
    # Example <scheme>://<host>/<basePath>/users - "/users" is path
    # https://swagger.io/docs/specification/2-0/api-host-and-base-path/
    path: str = attr.ib()  # pragma: no mutate
    method: str = attr.ib()  # pragma: no mutate
    definition: EndpointDefinition = attr.ib(repr=False)  # pragma: no mutate
    schema: "BaseSchema" = attr.ib()  # pragma: no mutate
    app: Any = attr.ib(default=None)  # pragma: no mutate
    base_url: Optional[str] = attr.ib(default=None)  # pragma: no mutate
    path_parameters: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    headers: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    cookies: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    query: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    body: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate
    form_data: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate

    @property
    def verbose_name(self) -> str:
        return f"{self.method.upper()} {self.path}"

    @property
    def full_path(self) -> str:
        return self.schema.get_full_path(self.path)

    @property
    def links(self) -> Dict[str, Dict[str, Any]]:
        return self.schema.get_links(self)

    def as_strategy(
        self,
        hooks: Optional["HookDispatcher"] = None,
        feedback: Optional["Feedback"] = None,
        data_generation_method: DataGenerationMethod = DataGenerationMethod.default(),
    ) -> SearchStrategy:
        return self.schema.get_case_strategy(self, hooks, feedback, data_generation_method)

    def get_strategies_from_examples(self) -> List[SearchStrategy[Case]]:
        """Get examples from the endpoint."""
        return self.schema.get_strategies_from_examples(self)

    def get_stateful_tests(self, response: GenericResponse, stateful: Optional["Stateful"]) -> Sequence["StatefulTest"]:
        return self.schema.get_stateful_tests(response, self, stateful)

    def get_hypothesis_conversions(self, location: str) -> Optional[Callable]:
        return self.schema.get_hypothesis_conversion(self, location)

    def prepare_multipart(self, form_data: Optional[FormData]) -> Tuple[Optional[List], Optional[Dict[str, Any]]]:
        if not form_data:
            return None, None
        return self.schema.prepare_multipart(form_data, self)

    def get_request_payload_content_types(self) -> List[str]:
        return self.schema.get_request_payload_content_types(self)

    def partial_deepcopy(self) -> "Endpoint":
        return self.__class__(
            path=self.path,  # string, immutable
            method=self.method,  # string, immutable
            definition=deepcopy(self.definition),
            schema=self.schema.clone(),  # shallow copy
            app=self.app,  # not deepcopyable
            base_url=self.base_url,  # string, immutable
            path_parameters=deepcopy(self.path_parameters),
            headers=deepcopy(self.headers),
            cookies=deepcopy(self.cookies),
            query=deepcopy(self.query),
            body=deepcopy(self.body),
            form_data=deepcopy(self.form_data),
        )

    def clone(self, **components: Any) -> "Endpoint":
        """Create a new instance of this endpoint with updated components."""
        return self.__class__(
            path=self.path,
            method=self.method,
            definition=self.definition,
            schema=self.schema,
            app=self.app,
            base_url=self.base_url,
            path_parameters=components["path_parameters"],
            query=components["query"],
            headers=components["headers"],
            cookies=components["cookies"],
            body=components["body"],
            form_data=self.form_data,
        )

    def make_case(
        self,
        *,
        path_parameters: Optional[PathParameters] = None,
        headers: Optional[Headers] = None,
        cookies: Optional[Cookies] = None,
        query: Optional[Query] = None,
        body: Optional[Body] = None,
        form_data: Optional[FormData] = None,
    ) -> Case:
        return Case(
            endpoint=self,
            path_parameters=path_parameters,
            headers=headers,
            cookies=cookies,
            query=query,
            body=body,
            form_data=form_data,
        )

    @property
    def operation_reference(self) -> str:
        path = self.path.replace("~", "~0").replace("/", "~1")
        return f"#/paths/{path}/{self.method}"

    def validate_response(self, response: GenericResponse) -> None:
        return self.schema.validate_response(self, response)

    def is_response_valid(self, response: GenericResponse) -> bool:
        try:
            self.validate_response(response)
            return True
        except CheckFailed:
            return False


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
class Request:
    """Request data extracted from `Case`."""

    method: str = attr.ib()  # pragma: no mutate
    uri: str = attr.ib()  # pragma: no mutate
    body: str = attr.ib()  # pragma: no mutate
    headers: Headers = attr.ib()  # pragma: no mutate

    @classmethod
    def from_case(cls, case: Case, session: requests.Session) -> "Request":
        """Create a new `Request` instance from `Case`."""
        base_url = case.get_full_base_url()
        kwargs = case.as_requests_kwargs(base_url)
        request = requests.Request(**kwargs)
        prepared = session.prepare_request(request)  # type: ignore
        return cls.from_prepared_request(prepared)

    @classmethod
    def from_prepared_request(cls, prepared: requests.PreparedRequest) -> "Request":
        """A prepared request version is already stored in `requests.Response`."""
        body = prepared.body or b""

        if isinstance(body, str):
            # can be a string for `application/x-www-form-urlencoded`
            body = body.encode("utf-8")

        # these values have `str` type at this point
        uri = cast(str, prepared.url)
        method = cast(str, prepared.method)
        return cls(
            uri=uri,
            method=method,
            headers={key: [value] for (key, value) in prepared.headers.items()},
            body=base64.b64encode(body).decode(),
        )


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


@attr.s(slots=True, repr=False)  # pragma: no mutate
class Response:
    """Unified response data."""

    status_code: int = attr.ib()  # pragma: no mutate
    message: str = attr.ib()  # pragma: no mutate
    headers: Dict[str, List[str]] = attr.ib()  # pragma: no mutate
    body: str = attr.ib()  # pragma: no mutate
    encoding: str = attr.ib()  # pragma: no mutate
    http_version: str = attr.ib()  # pragma: no mutate
    elapsed: float = attr.ib()  # pragma: no mutate

    @classmethod
    def from_requests(cls, response: requests.Response) -> "Response":
        """Create a response from requests.Response."""
        headers = {name: response.raw.headers.getlist(name) for name in response.raw.headers.keys()}
        # Similar to http.client:319 (HTTP version detection in stdlib's `http` package)
        http_version = "1.0" if response.raw.version == 10 else "1.1"
        return cls(
            status_code=response.status_code,
            message=response.reason,
            body=serialize_payload(response.content),
            encoding=response.encoding or "utf8",
            headers=headers,
            http_version=http_version,
            elapsed=response.elapsed.total_seconds(),
        )

    @classmethod
    def from_wsgi(cls, response: WSGIResponse, elapsed: float) -> "Response":
        """Create a response from WSGI response."""
        message = http.client.responses.get(response.status_code, "UNKNOWN")
        headers = {name: response.headers.getlist(name) for name in response.headers.keys()}
        return cls(
            status_code=response.status_code,
            message=message,
            body=serialize_payload(response.data),
            encoding=response.content_encoding or "utf-8",
            headers=headers,
            http_version="1.1",
            elapsed=elapsed,
        )


@attr.s(slots=True)  # pragma: no mutate
class Interaction:
    """A single interaction with the target app."""

    request: Request = attr.ib()  # pragma: no mutate
    response: Response = attr.ib()  # pragma: no mutate
    checks: List[Check] = attr.ib()  # pragma: no mutate
    status: Status = attr.ib()  # pragma: no mutate
    recorded_at: str = attr.ib(factory=lambda: datetime.datetime.now().isoformat())  # pragma: no mutate

    @classmethod
    def from_requests(cls, response: requests.Response, status: Status, checks: List[Check]) -> "Interaction":
        return cls(
            request=Request.from_prepared_request(response.request),
            response=Response.from_requests(response),
            status=status,
            checks=checks,
        )

    @classmethod
    def from_wsgi(
        cls,
        case: Case,
        response: WSGIResponse,
        headers: Dict[str, Any],
        elapsed: float,
        status: Status,
        checks: List[Check],
    ) -> "Interaction":
        session = requests.Session()
        session.headers.update(headers)
        return cls(
            request=Request.from_case(case, session),
            response=Response.from_wsgi(response, elapsed),
            status=status,
            checks=checks,
        )


@attr.s(slots=True, repr=False)  # pragma: no mutate
class TestResult:
    """Result of a single test."""

    endpoint: Endpoint = attr.ib()  # pragma: no mutate
    data_generation_method: DataGenerationMethod = attr.ib()  # pragma: no mutate
    checks: List[Check] = attr.ib(factory=list)  # pragma: no mutate
    errors: List[Tuple[Exception, Optional[Case]]] = attr.ib(factory=list)  # pragma: no mutate
    interactions: List[Interaction] = attr.ib(factory=list)  # pragma: no mutate
    logs: List[LogRecord] = attr.ib(factory=list)  # pragma: no mutate
    is_errored: bool = attr.ib(default=False)  # pragma: no mutate
    seed: Optional[int] = attr.ib(default=None)  # pragma: no mutate
    # To show a proper reproduction code if a failure happens
    overridden_headers: Optional[Dict[str, Any]] = attr.ib(default=None)  # pragma: no mutate

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

    def store_requests_response(self, response: requests.Response, status: Status, checks: List[Check]) -> None:
        self.interactions.append(Interaction.from_requests(response, status, checks))

    def store_wsgi_response(
        self,
        case: Case,
        response: WSGIResponse,
        headers: Dict[str, Any],
        elapsed: float,
        status: Status,
        checks: List[Check],
    ) -> None:
        self.interactions.append(Interaction.from_wsgi(case, response, headers, elapsed, status, checks))


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
        """An aggregated statistic about test results."""
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


CheckFunction = Callable[[GenericResponse, Case], Optional[bool]]  # pragma: no mutate
