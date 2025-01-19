from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, cast

from schemathesis.core.failures import Failure
from schemathesis.core.transport import Response
from schemathesis.engine import Status
from schemathesis.generation.case import Case

if TYPE_CHECKING:
    import requests

    from schemathesis.generation.stateful.state_machine import Transition


@dataclass
class ScenarioRecorder:
    """Tracks and organizes all data related to a logical block of testing.

    Records test cases, their hierarchy, API interactions, and results of checks performed during execution.
    """

    id: uuid.UUID
    # Human-readable label
    label: str

    # Recorded test cases
    cases: dict[str, CaseNode]
    # Results of checks categorized by test case ID
    checks: dict[str, list[CheckNode]]
    # Network interactions by test case ID
    interactions: dict[str, Interaction]

    __slots__ = ("id", "label", "status", "roots", "cases", "checks", "interactions")

    def __init__(self, *, label: str) -> None:
        self.id = uuid.uuid4()
        self.label = label
        self.cases = {}
        self.checks = {}
        self.interactions = {}

    def record_case(self, *, parent_id: str | None, transition: Transition | None, case: Case) -> None:
        """Record a test case and its relationship to a parent, if applicable."""
        self.cases[case.id] = CaseNode(value=case, parent_id=parent_id, transition=transition)

    def record_response(self, *, case_id: str, response: Response) -> None:
        """Record the API response for a given test case."""
        request = Request.from_prepared_request(response.request)
        self.interactions[case_id] = Interaction(request=request, response=response)

    def record_request(self, *, case_id: str, request: requests.PreparedRequest) -> None:
        """Record a network-level error for a given test case."""
        self.interactions[case_id] = Interaction(request=Request.from_prepared_request(request), response=None)

    def record_check_failure(self, *, name: str, case_id: str, code_sample: str, failure: Failure) -> None:
        """Record a failure of a check for a given test case."""
        self.checks.setdefault(case_id, []).append(
            CheckNode(
                name=name,
                status=Status.FAILURE,
                failure_info=CheckFailureInfo(code_sample=code_sample, failure=failure),
            )
        )

    def record_check_success(self, *, name: str, case_id: str) -> None:
        """Record a successful pass of a check for a given test case."""
        self.checks.setdefault(case_id, []).append(CheckNode(name=name, status=Status.SUCCESS, failure_info=None))

    def find_failure_data(self, *, parent_id: str, failure: Failure) -> FailureData:
        """Retrieve the relevant test case & interaction data for a failure.

        It may happen that a failure comes from a different test case if a check generated some additional
        test cases & interactions.
        """
        case_id = failure.case_id or parent_id
        case = self.cases[case_id].value
        request = self.interactions[case_id].request
        response = self.interactions[case_id].response
        assert isinstance(response, Response)
        headers = {key: value[0] for key, value in request.headers.items()}
        return FailureData(case=case, headers=headers, verify=response.verify)

    def find_parent(self, *, case_id: str) -> Case | None:
        """Find the parent case of a given test case, if it exists."""
        case = self.cases.get(case_id)
        if case is not None and case.parent_id is not None:
            parent = self.cases.get(case.parent_id)
            # The recorder state should always be consistent
            assert parent is not None, "Parent does not exist"
            return parent.value
        return None

    def find_related(self, *, case_id: str) -> Iterator[Case]:
        """Iterate over all ancestors and their children for a given case."""
        current_id = case_id
        seen = {current_id}

        while True:
            current_node = self.cases.get(current_id)
            if current_node is None or current_node.parent_id is None:
                break

            # Get all children of the parent (siblings of the current case)
            parent_id = current_node.parent_id
            for case_id, maybe_child in self.cases.items():
                # If this case has the same parent and we haven't seen it yet
                if parent_id == maybe_child.parent_id and case_id not in seen:
                    seen.add(case_id)
                    yield maybe_child.value

            # Move up to the parent
            current_id = parent_id
            if current_id not in seen:
                seen.add(current_id)
                parent_node = self.cases.get(current_id)
                if parent_node:
                    yield parent_node.value

    def find_response(self, *, case_id: str) -> Response | None:
        """Retrieve the API response for a given test case, if available."""
        interaction = self.interactions.get(case_id)
        if interaction is None or interaction.response is None:
            return None
        return interaction.response


@dataclass
class CaseNode:
    """Represents a test case and its parent-child relationship."""

    value: Case
    parent_id: str | None
    # Transition may be absent if `parent_id` is present for cases when a case is derived inside a check
    # and outside of the implemented transition logic (e.g. Open API links)
    transition: Transition | None

    __slots__ = ("value", "parent_id", "transition")


@dataclass
class CheckNode:
    name: str
    status: Status
    failure_info: CheckFailureInfo | None

    __slots__ = ("name", "status", "failure_info")


@dataclass
class CheckFailureInfo:
    code_sample: str
    failure: Failure

    __slots__ = ("code_sample", "failure")


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


@dataclass
class Interaction:
    """Represents a single interaction with the tested application."""

    request: Request
    response: Response | None
    timestamp: float

    __slots__ = ("request", "response", "timestamp")

    def __init__(self, request: Request, response: Response | None) -> None:
        self.request = request
        self.response = response
        self.timestamp = time.time()


@dataclass
class FailureData:
    """Details about a test failure, including the case and its context."""

    case: Case
    headers: dict[str, str]
    verify: bool

    __slots__ = ("case", "headers", "verify")
