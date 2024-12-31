from __future__ import annotations

import base64
import datetime
from dataclasses import asdict, dataclass
from itertools import groupby
from typing import TYPE_CHECKING, Any, Generator, Iterator, cast

from schemathesis.core.failures import Failure
from schemathesis.core.transport import Response
from schemathesis.generation.meta import CaseMetadata
from schemathesis.runner import Status
from schemathesis.transport.prepare import normalize_base_url
from schemathesis.transport.requests import REQUESTS_TRANSPORT

if TYPE_CHECKING:
    import requests
    from requests.structures import CaseInsensitiveDict

    from schemathesis.generation.case import Case


@dataclass(repr=False)
class Check:
    """Single check run result."""

    name: str
    status: Status
    headers: CaseInsensitiveDict
    response: Response
    case: Case
    failure: Failure | None
    _code_sample_cache: str | None

    __slots__ = ("name", "status", "headers", "response", "case", "failure", "_code_sample_cache")

    def __init__(
        self,
        *,
        name: str,
        status: Status,
        headers: CaseInsensitiveDict,
        response: Response,
        case: Case,
        failure: Failure | None = None,
    ):
        self.name = name
        self.status = status
        self.headers = headers
        self.response = response
        self.case = case
        self.failure = failure
        self._code_sample_cache = None

    @property
    def code_sample(self) -> str:
        if self._code_sample_cache is None:
            self._code_sample_cache = self.case.as_curl_command(headers=self.headers, verify=self.response.verify)
        return self._code_sample_cache

    def asdict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "response": self.response.asdict(),
            "case": self.case.asdict(),
            "failure": asdict(self.failure) if self.failure is not None else None,  # type: ignore
        }


def group_failures_by_code_sample(checks: list[Check]) -> Generator[tuple[str, Iterator[Check]], None, None]:
    deduplicated = {check.failure: check for check in checks if check.failure is not None}
    failures = sorted(deduplicated.values(), key=_by_unique_key)
    for (sample, _, _), gen in groupby(failures, _by_unique_key):
        yield (sample, gen)


def _by_unique_key(check: Check) -> tuple[str, int, bytes]:
    return (
        check.code_sample,
        check.response.status_code,
        check.response.content or b"SCHEMATHESIS-INTERNAL-EMPTY-BODY",
    )


@dataclass(repr=False)
class TestResult:
    """Result of a single test."""

    __test__ = False

    label: str
    interactions: list[Interaction]

    __slots__ = ("label", "interactions")

    def __init__(self, *, label: str, interactions: list[Interaction] | None = None) -> None:
        self.label = label
        self.interactions = interactions or []

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def checks(self) -> list[Check]:
        return sum((interaction.checks for interaction in self.interactions), [])

    def record(
        self, case: Case, response: Response | None, status: Status, checks: list[Check], session: requests.Session
    ) -> None:
        self.interactions.append(Interaction.from_requests(case, response, status, checks, session))


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

    __slots__ = ("method", "uri", "body", "body_size", "headers", "_encoded_body_cache")

    def __init__(
        self,
        method: str,
        uri: str,
        body: bytes | None,
        body_size: int | None,
        headers: dict[str, list[str]],
    ):
        self.method = method
        self.uri = uri
        self.body = body
        self.body_size = body_size
        self.headers = headers
        self._encoded_body_cache: str | None = None

    @classmethod
    def from_case(cls, case: Case, session: requests.Session) -> Request:
        """Create a new `Request` instance from `Case`."""
        import requests

        base_url = normalize_base_url(case.operation.base_url)
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

    @property
    def encoded_body(self) -> str | None:
        if self.body is not None:
            if self._encoded_body_cache is None:
                self._encoded_body_cache = serialize_payload(self.body)
            return self._encoded_body_cache
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
    id: str
    meta: CaseMetadata | None
    recorded_at: str

    __slots__ = ("request", "response", "checks", "status", "id", "meta", "recorded_at")

    def __init__(
        self,
        request: Request,
        response: Response | None,
        checks: list[Check],
        status: Status,
        id: str,
        meta: CaseMetadata | None = None,
    ):
        self.request = request
        self.response = response
        self.checks = checks
        self.status = status
        self.id = id
        self.meta = meta
        self.recorded_at = datetime.datetime.now(TIMEZONE).isoformat()

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
            id=case.id,
            meta=case.meta,
        )

    def asdict(self) -> dict[str, Any]:
        return {
            "request": self.request.asdict(),
            "response": self.response.asdict() if self.response is not None else None,
            "checks": [check.asdict() for check in self.checks],
            "status": self.status.value,
            "meta": self.meta.asdict() if self.meta is not None else None,
            "recorded_at": self.recorded_at,
        }
