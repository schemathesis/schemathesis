from __future__ import annotations

import base64
import datetime
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, cast

from schemathesis.core.transport import Response
from schemathesis.transport.requests import REQUESTS_TRANSPORT

from ...generation import DataGenerationMethod
from .status import Status

if TYPE_CHECKING:
    import requests

    from schemathesis.generation.meta import TestPhase

    from ...models import Case
    from .check import Check


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()


@dataclass(repr=False)
class Request:
    """Request data extracted from `Case`."""

    method: str
    uri: str
    body: bytes | None
    body_size: int | None
    headers: dict[str, list[str]]

    @classmethod
    def from_case(cls, case: Case, session: requests.Session) -> Request:
        """Create a new `Request` instance from `Case`."""
        import requests

        base_url = case.get_full_base_url()
        kwargs = REQUESTS_TRANSPORT.serialize_case(case, base_url=base_url)
        request = requests.Request(**kwargs)
        prepared = session.prepare_request(request)  # type: ignore
        return cls.from_prepared_request(prepared)

    @classmethod
    def from_prepared_request(cls, prepared: requests.PreparedRequest) -> Request:
        """A prepared request version is already stored in `requests.Response`."""
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
        response: Response | None,
        status: Status,
        checks: list[Check],
        session: requests.Session,
    ) -> Interaction:
        if response is not None:
            request = Request.from_prepared_request(response.request)
        else:
            request = Request.from_case(case, session)
        return cls(
            request=request,
            response=response,
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
