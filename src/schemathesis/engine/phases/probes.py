"""Detecting capabilities of the application under test.

Schemathesis sends specially crafted requests to the application before running tests in order to detect whether
the application supports certain inputs. This is done to avoid false positives in the tests.
For example, certail web servers do not support NULL bytes in headers, in such cases, the generated test case
will not reach the tested application at all.
"""

from __future__ import annotations

import enum
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.result import Ok, Result
from schemathesis.core.transport import USER_AGENT
from schemathesis.engine import Status, events
from schemathesis.transport.prepare import get_default_headers

if TYPE_CHECKING:
    import requests

    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.phases import Phase
    from schemathesis.schemas import BaseSchema


@dataclass
class ProbePayload:
    probes: list[ProbeRun]

    __slots__ = ("probes",)


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    """Discover capabilities of the tested app."""
    probes = run(ctx)
    status = Status.SUCCESS
    payload: Result[ProbePayload, Exception] | None = None
    for result in probes:
        if isinstance(result.probe, NullByteInHeader) and result.is_failure:
            from schemathesis.specs.openapi import formats
            from schemathesis.specs.openapi.formats import (
                DEFAULT_HEADER_EXCLUDE_CHARACTERS,
                HEADER_FORMAT,
                header_values,
            )

            formats.register(
                HEADER_FORMAT, header_values(exclude_characters=DEFAULT_HEADER_EXCLUDE_CHARACTERS + "\x00")
            )
        payload = Ok(ProbePayload(probes=probes))
    yield events.PhaseFinished(phase=phase, status=status, payload=payload)


def run(ctx: EngineContext) -> list[ProbeRun]:
    """Run all probes against the given schema."""
    return [send(probe(), ctx) for probe in PROBES]


HEADER_NAME = "X-Schemathesis-Probe"


@dataclass
class Probe:
    """A request to determine the capabilities of the application under test."""

    name: str

    __slots__ = ("name",)

    def prepare_request(
        self, session: requests.Session, request: requests.Request, schema: BaseSchema
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


@dataclass
class ProbeRun:
    probe: Probe
    outcome: ProbeOutcome
    request: requests.PreparedRequest | None
    response: requests.Response | None
    error: Exception | None

    __slots__ = ("probe", "outcome", "request", "response", "error")

    def __init__(
        self,
        probe: Probe,
        outcome: ProbeOutcome,
        request: requests.PreparedRequest | None = None,
        response: requests.Response | None = None,
        error: Exception | None = None,
    ) -> None:
        self.probe = probe
        self.outcome = outcome
        self.request = request
        self.response = response
        self.error = error

    @property
    def is_failure(self) -> bool:
        return self.outcome == ProbeOutcome.FAILURE


@dataclass
class NullByteInHeader(Probe):
    """Support NULL bytes in headers."""

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "Supports NULL byte in headers"

    def prepare_request(
        self, session: requests.Session, request: requests.Request, schema: BaseSchema
    ) -> requests.PreparedRequest:
        request.method = "GET"
        request.url = schema.get_base_url()
        request.headers = {"X-Schemathesis-Probe-Null": "\x00"}
        return session.prepare_request(request)

    def analyze_response(self, response: requests.Response) -> ProbeOutcome:
        if response.status_code == 400:
            return ProbeOutcome.FAILURE
        return ProbeOutcome.SUCCESS


PROBES = (NullByteInHeader,)


def send(probe: Probe, ctx: EngineContext) -> ProbeRun:
    """Send the probe to the application."""
    from requests import PreparedRequest, Request, RequestException
    from requests.exceptions import MissingSchema
    from urllib3.exceptions import InsecureRequestWarning

    try:
        session = ctx.get_session()
        request = probe.prepare_request(session, Request(), ctx.schema)
        request.headers[HEADER_NAME] = probe.name
        request.headers["User-Agent"] = USER_AGENT
        for header, value in get_default_headers().items():
            request.headers.setdefault(header, value)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = session.send(request, timeout=ctx.config.request_timeout or 2)
    except MissingSchema:
        # In-process ASGI/WSGI testing will have local URLs and requires extra handling
        # which is not currently implemented
        return ProbeRun(probe, ProbeOutcome.SKIP, None, None, None)
    except RequestException as exc:
        # Consider any network errors as a failed probe
        req = exc.request if isinstance(exc.request, PreparedRequest) else None
        return ProbeRun(probe, ProbeOutcome.FAILURE, req, None, exc)
    result_type = probe.analyze_response(response)
    return ProbeRun(probe, result_type, request, response)
