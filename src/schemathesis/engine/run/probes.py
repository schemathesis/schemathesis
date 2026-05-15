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

    from schemathesis.core.spec import SchemaMetadata
    from schemathesis.engine.context import EngineContext
    from schemathesis.engine.events import EventGenerator
    from schemathesis.engine.run import Phase
    from schemathesis.engine.run.cache import CacheReport


@dataclass(slots=True)
class ProbePayload:
    probes: list[ProbeRun]
    cache: CacheReport | None = None


def execute(ctx: EngineContext, phase: Phase) -> EventGenerator:
    """Discover capabilities of the tested app."""
    probes = run(ctx)
    status = Status.SUCCESS
    payload: Result[ProbePayload, Exception] | None = None
    for result in probes:
        if isinstance(result.probe, NullByteInHeader) and result.is_failure:
            ctx.schema.adapt_to_null_byte_in_header_failure()
        elif isinstance(result.probe, UnsafePathDecoder) and result.is_failure:
            ctx.schema.adapt_to_path_decoder_rejection()
        payload = Ok(ProbePayload(probes=probes))
    cache_report = ctx.cache.run()
    if cache_report is not None:
        payload = Ok(ProbePayload(probes=probes, cache=cache_report))
    yield events.PhaseFinished(phase=phase, status=status, payload=payload)


def run(ctx: EngineContext) -> list[ProbeRun]:
    """Run all probes against the given schema."""
    return [send(probe(), ctx) for probe in PROBES]


HEADER_NAME = "X-Schemathesis-Probe"


@dataclass(slots=True)
class Probe:
    """A request to determine the capabilities of the application under test."""

    name: str

    def prepare_request(
        self, session: requests.Session, request: requests.Request, schema: SchemaMetadata
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


@dataclass(slots=True)
class ProbeRun:
    probe: Probe
    outcome: ProbeOutcome
    request: requests.PreparedRequest | None
    response: requests.Response | None
    error: Exception | None

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
        self, session: requests.Session, request: requests.Request, schema: SchemaMetadata
    ) -> requests.PreparedRequest:
        request.method = "GET"
        request.url = schema.get_base_url()
        request.headers = {"X-Schemathesis-Probe-Null": "\x00"}
        return session.prepare_request(request)

    def analyze_response(self, response: requests.Response) -> ProbeOutcome:
        if response.status_code == 400:
            return ProbeOutcome.FAILURE
        return ProbeOutcome.SUCCESS


# Mix of chars that strict URL decoders (Tomcat, common WAFs) reject in the path component
# before routing: backslash, ESC, control chars from both ranges. If any of these come back
# as 400 with an empty body, the app never sees the request anyway.
_UNSAFE_PATH_PROBE_SUFFIX = "schemathesis-probe%5C%1B%01"


@dataclass
class UnsafePathDecoder(Probe):
    """Reject backslash and control characters in URL paths before routing."""

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name = "Accepts backslash and control characters in URL paths"

    def prepare_request(
        self, session: requests.Session, request: requests.Request, schema: SchemaMetadata
    ) -> requests.PreparedRequest:
        base_url = schema.get_base_url()
        request.method = "GET"
        separator = "" if base_url.endswith("/") else "/"
        request.url = f"{base_url}{separator}{_UNSAFE_PATH_PROBE_SUFFIX}"
        return session.prepare_request(request)

    def analyze_response(self, response: requests.Response) -> ProbeOutcome:
        if response.status_code == 400 and _is_path_decoder_rejection_body(response.content):
            return ProbeOutcome.FAILURE
        return ProbeOutcome.SUCCESS


# Tomcat ships a default HTML error page (rather than an empty body) when its URI parser
# rejects unsafe percent-encodings before routing — match its distinctive title.
_TOMCAT_400_TITLE = b"<title>HTTP Status 400 \xe2\x80\x93 Bad Request</title>"


def _is_path_decoder_rejection_body(content: bytes) -> bool:
    return not content or _TOMCAT_400_TITLE in content


PROBES = (NullByteInHeader, UnsafePathDecoder)


def send(probe: Probe, ctx: EngineContext) -> ProbeRun:
    """Send the probe to the application."""
    from requests import PreparedRequest, Request, RequestException
    from requests.exceptions import MissingSchema
    from urllib3.exceptions import InsecureRequestWarning

    from schemathesis.engine.context import make_session

    session = make_session(ctx.config)
    try:
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
    finally:
        session.close()
    result_type = probe.analyze_response(response)
    return ProbeRun(probe, result_type, request, response)
