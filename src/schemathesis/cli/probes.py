"""Detecting capabilities of the application under test.

Schemathesis sends specially crafted requests to the application before running tests in order to detect whether
the application supports certain inputs. This is done to avoid false positives in the tests.
For example, certail web servers do not support NULL bytes in headers, in such cases, the generated test case
will not reach the tested application at all.
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from ..constants import USER_AGENT
from ..exceptions import format_exception
from ..models import Request, Response
from ..sanitization import sanitize_request, sanitize_response
from ..transports.auth import get_requests_auth

if TYPE_CHECKING:
    import requests

    from ..schemas import BaseSchema
    from . import LoaderConfig


HEADER_NAME = "X-Schemathesis-Probe"


@dataclass
class Probe:
    """A request to determine the capabilities of the application under test."""

    name: str

    def prepare_request(
        self, session: requests.Session, request: requests.Request, schema: BaseSchema, config: LoaderConfig
    ) -> requests.PreparedRequest:
        raise NotImplementedError

    def analyze_response(self, response: requests.Response) -> ProbeOutcome:
        raise NotImplementedError


class ProbeOutcome(str, enum.Enum):
    # Capability is supported
    SUCCESS = "success"
    # Capability is not supported
    FAILURE = "failure"
    # Probe is not applicable
    SKIP = "skip"
    # Error occurred during the probe
    ERROR = "error"


@dataclass
class ProbeRun:
    probe: Probe
    outcome: ProbeOutcome
    request: requests.PreparedRequest | None = None
    response: requests.Response | None = None
    error: requests.RequestException | None = None

    @property
    def is_failure(self) -> bool:
        return self.outcome == ProbeOutcome.FAILURE

    def serialize(self) -> dict[str, Any]:
        """Serialize probe results so it can be sent over the network."""
        if self.request:
            _request = Request.from_prepared_request(self.request)
            sanitize_request(_request)
            request = asdict(_request)
        else:
            request = None
        if self.response:
            sanitize_response(self.response)
            response = asdict(Response.from_requests(self.response))
        else:
            response = None
        if self.error:
            error = format_exception(self.error)
        else:
            error = None
        return {
            "name": self.probe.name,
            "outcome": self.outcome.value,
            "request": request,
            "response": response,
            "error": error,
        }


@dataclass
class NullByteInHeader(Probe):
    """Support NULL bytes in headers."""

    name: str = "NULL_BYTE_IN_HEADER"

    def prepare_request(
        self, session: requests.Session, request: requests.Request, schema: BaseSchema, config: LoaderConfig
    ) -> requests.PreparedRequest:
        request.method = "GET"
        request.url = config.base_url or schema.get_base_url()
        request.headers = {"X-Schemathesis-Probe-Null": "\x00"}
        return session.prepare_request(request)

    def analyze_response(self, response: requests.Response) -> ProbeOutcome:
        if response.status_code == 400:
            return ProbeOutcome.FAILURE
        return ProbeOutcome.SUCCESS


PROBES = (NullByteInHeader,)


def send(probe: Probe, session: requests.Session, schema: BaseSchema, config: LoaderConfig) -> ProbeRun:
    """Send the probe to the application."""
    from requests import PreparedRequest, Request, RequestException
    from requests.exceptions import MissingSchema

    try:
        request = probe.prepare_request(session, Request(), schema, config)
        request.headers[HEADER_NAME] = probe.name
        request.headers["User-Agent"] = USER_AGENT
        response = session.send(request)
    except MissingSchema:
        # In-process ASGI/WSGI testing will have local URLs and requires extra handling
        # which is not currently implemented
        return ProbeRun(probe, ProbeOutcome.SKIP, None, None, None)
    except RequestException as exc:
        req = exc.request if isinstance(exc.request, PreparedRequest) else None
        return ProbeRun(probe, ProbeOutcome.ERROR, req, None, exc)
    result_type = probe.analyze_response(response)
    return ProbeRun(probe, result_type, request, response)


def run(schema: BaseSchema, config: LoaderConfig) -> list[ProbeRun]:
    """Run all probes against the given schema."""
    from requests import Session

    session = Session()
    session.verify = config.request_tls_verify
    if config.request_cert is not None:
        session.cert = config.request_cert
    if config.auth is not None:
        session.auth = get_requests_auth(config.auth, config.auth_type)

    return [send(probe(), session, schema, config) for probe in PROBES]
