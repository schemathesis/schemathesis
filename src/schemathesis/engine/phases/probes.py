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

from schemathesis.core.result import Err, Ok, Result
from schemathesis.core.transport import USER_AGENT
from schemathesis.engine import Status, events

if TYPE_CHECKING:
    import requests

    from schemathesis.engine.config import NetworkConfig
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
    probes = run(ctx.schema, ctx.session, ctx.config.network)
    status = Status.SUCCESS
    payload: Result[ProbePayload, Exception] | None = None
    for result in probes:
        if isinstance(result.probe, NullByteInHeader) and result.is_failure:
            from ...specs.openapi import formats
            from ...specs.openapi.formats import HEADER_FORMAT, header_values

            formats.register(HEADER_FORMAT, header_values(blacklist_characters="\n\r\x00"))
        if result.error is not None:
            status = Status.ERROR
            payload = Err(result.error)
        else:
            status = Status.SUCCESS
            payload = Ok(ProbePayload(probes=probes))
    yield events.PhaseFinished(phase=phase, status=status, payload=payload)


def run(schema: BaseSchema, session: requests.Session, config: NetworkConfig) -> list[ProbeRun]:
    """Run all probes against the given schema."""
    return [send(probe(), session, schema, config) for probe in PROBES]


HEADER_NAME = "X-Schemathesis-Probe"


@dataclass
class Probe:
    """A request to determine the capabilities of the application under test."""

    name: str

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
    # Error occurred during the probe
    ERROR = "error"


@dataclass
class ProbeRun:
    probe: Probe
    outcome: ProbeOutcome
    request: requests.PreparedRequest | None = None
    response: requests.Response | None = None
    error: Exception | None = None

    @property
    def is_failure(self) -> bool:
        return self.outcome == ProbeOutcome.FAILURE


@dataclass
class NullByteInHeader(Probe):
    """Support NULL bytes in headers."""

    name: str = "Supports NULL byte in headers"

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


def send(probe: Probe, session: requests.Session, schema: BaseSchema, config: NetworkConfig) -> ProbeRun:
    """Send the probe to the application."""
    from requests import PreparedRequest, Request, RequestException
    from requests.exceptions import MissingSchema
    from urllib3.exceptions import InsecureRequestWarning

    try:
        request = probe.prepare_request(session, Request(), schema)
        request.headers[HEADER_NAME] = probe.name
        request.headers["User-Agent"] = USER_AGENT
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureRequestWarning)
            response = session.send(request, timeout=config.timeout or 2)
    except MissingSchema:
        # In-process ASGI/WSGI testing will have local URLs and requires extra handling
        # which is not currently implemented
        return ProbeRun(probe, ProbeOutcome.SKIP, None, None, None)
    except RequestException as exc:
        req = exc.request if isinstance(exc.request, PreparedRequest) else None
        return ProbeRun(probe, ProbeOutcome.ERROR, req, None, exc)
    result_type = probe.analyze_response(response)
    return ProbeRun(probe, result_type, request, response)
