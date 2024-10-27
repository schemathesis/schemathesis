from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, cast

from ...generation import DataGenerationMethod
from ...transports import RequestsTransport, serialize_payload
from ...types import Headers
from .status import Status

if TYPE_CHECKING:
    import requests

    from ...models import Case, TestPhase
    from ...transports.responses import GenericResponse, WSGIResponse
    from .check import Check


@dataclass(repr=False)
class Request:
    """Request data extracted from `Case`."""

    method: str
    uri: str
    body: bytes | None
    body_size: int | None
    headers: Headers

    @classmethod
    def from_case(cls, case: Case, session: requests.Session) -> Request:
        """Create a new `Request` instance from `Case`."""
        import requests

        base_url = case.get_full_base_url()
        kwargs = RequestsTransport().serialize_case(case, base_url=base_url)
        request = requests.Request(**kwargs)
        prepared = session.prepare_request(request)  # type: ignore
        return cls.from_prepared_request(prepared)

    @classmethod
    def from_prepared_request(cls, prepared: requests.PreparedRequest) -> Request:
        """A prepared request version is already stored in `requests.Response`."""
        # TODO: Support httpx.Request
        body = prepared.body

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
            body=body,
            body_size=len(body) if body is not None else None,
        )

    @cached_property
    def encoded_body(self) -> str | None:
        if self.body is not None:
            return serialize_payload(self.body)
        return None

    def asdict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "uri": self.uri,
            "body": self.encoded_body,
            "body_size": self.body_size,
            "headers": self.headers,
        }


@dataclass(repr=False)
class Response:
    """Unified response data."""

    status_code: int
    message: str
    headers: dict[str, list[str]]
    body: bytes | None
    body_size: int | None
    encoding: str | None
    http_version: str
    elapsed: float
    verify: bool

    @classmethod
    def from_generic(cls, *, response: GenericResponse) -> Response:
        import requests

        if isinstance(response, requests.Response):
            return cls.from_requests(response)
        return cls.from_wsgi(response, response.elapsed.total_seconds())

    @classmethod
    def from_requests(cls, response: requests.Response) -> Response:
        """Create a response from requests.Response."""
        raw = response.raw
        raw_headers = raw.headers if raw is not None else {}
        headers = {name: response.raw.headers.getlist(name) for name in raw_headers.keys()}
        # Similar to http.client:319 (HTTP version detection in stdlib's `http` package)
        version = raw.version if raw is not None else 10
        http_version = "1.0" if version == 10 else "1.1"

        def is_empty(_response: requests.Response) -> bool:
            # Assume the response is empty if:
            #   - no `Content-Length` header
            #   - no chunks when iterating over its content
            return "Content-Length" not in headers and list(_response.iter_content()) == []

        body = None if is_empty(response) else response.content
        return cls(
            status_code=response.status_code,
            message=response.reason,
            body=body,
            body_size=len(response.content) if body is not None else None,
            encoding=response.encoding,
            headers=headers,
            http_version=http_version,
            elapsed=response.elapsed.total_seconds(),
            verify=getattr(response, "verify", True),
        )

    @classmethod
    def from_wsgi(cls, response: WSGIResponse, elapsed: float) -> Response:
        """Create a response from WSGI response."""
        from ...transports.responses import get_reason

        message = get_reason(response.status_code)
        headers = {name: response.headers.getlist(name) for name in response.headers.keys()}
        # Note, this call ensures that `response.response` is a sequence, which is needed for comparison
        data = response.get_data()
        body = None if response.response == [] else data
        encoding: str | None
        if body is not None:
            # Werkzeug <3.0 had `charset` attr, newer versions always have UTF-8
            encoding = response.mimetype_params.get("charset", getattr(response, "charset", "utf-8"))
        else:
            encoding = None
        return cls(
            status_code=response.status_code,
            message=message,
            body=body,
            body_size=len(data) if body is not None else None,
            encoding=encoding,
            headers=headers,
            http_version="1.1",
            elapsed=elapsed,
            verify=True,
        )

    @cached_property
    def encoded_body(self) -> str | None:
        if self.body is not None:
            return serialize_payload(self.body)
        return None

    def asdict(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "message": self.message,
            "headers": self.headers,
            "body": self.encoded_body,
            "body_size": self.body_size,
            "encoding": self.encoding,
            "http_version": self.http_version,
            "elapsed": self.elapsed,
            "verify": self.verify,
        }


TIMEZONE = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo


@dataclass
class Interaction:
    """A single interaction with the target app."""

    request: Request
    response: Response | None
    checks: list[Check]
    status: Status
    data_generation_method: DataGenerationMethod
    phase: TestPhase | None
    # `description` & `location` are related to metadata about this interaction
    # NOTE: It will be better to keep it in a separate attribute
    description: str | None
    location: str | None
    parameter: str | None
    parameter_location: str | None
    recorded_at: str = field(default_factory=lambda: datetime.datetime.now(TIMEZONE).isoformat())

    @classmethod
    def from_requests(
        cls,
        case: Case,
        response: requests.Response | None,
        status: Status,
        checks: list[Check],
        headers: dict[str, Any] | None,
        session: requests.Session | None,
    ) -> Interaction:
        if response is not None:
            prepared = response.request
            request = Request.from_prepared_request(prepared)
        else:
            import requests

            if session is None:
                session = requests.Session()
                session.headers.update(headers or {})
            request = Request.from_case(case, session)
        return cls(
            request=request,
            response=Response.from_requests(response) if response is not None else None,
            status=status,
            checks=checks,
            data_generation_method=cast(DataGenerationMethod, case.data_generation_method),
            phase=case.meta.phase if case.meta is not None else None,
            description=case.meta.description if case.meta is not None else None,
            location=case.meta.location if case.meta is not None else None,
            parameter=case.meta.parameter if case.meta is not None else None,
            parameter_location=case.meta.parameter_location if case.meta is not None else None,
        )

    @classmethod
    def from_wsgi(
        cls,
        case: Case,
        response: WSGIResponse | None,
        headers: dict[str, Any],
        elapsed: float | None,
        status: Status,
        checks: list[Check],
    ) -> Interaction:
        import requests

        session = requests.Session()
        session.headers.update(headers)
        return cls(
            request=Request.from_case(case, session),
            response=Response.from_wsgi(response, elapsed) if response is not None and elapsed is not None else None,
            status=status,
            checks=checks,
            data_generation_method=cast(DataGenerationMethod, case.data_generation_method),
            phase=case.meta.phase if case.meta is not None else None,
            description=case.meta.description if case.meta is not None else None,
            location=case.meta.location if case.meta is not None else None,
            parameter=case.meta.parameter if case.meta is not None else None,
            parameter_location=case.meta.parameter_location if case.meta is not None else None,
        )

    def asdict(self) -> dict[str, Any]:
        return {
            "request": self.request.asdict(),
            "response": self.response.asdict() if self.response is not None else None,
            "checks": [check.asdict() for check in self.checks],
            "status": self.status.value,
            "data_generation_method": self.data_generation_method.as_short_name(),
            "phase": self.phase.value if self.phase is not None else None,
            "description": self.description,
            "location": self.location,
            "parameter": self.parameter,
            "parameter_location": self.parameter_location,
            "recorded_at": self.recorded_at,
        }
