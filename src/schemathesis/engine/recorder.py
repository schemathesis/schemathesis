from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

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

    # Human-readable label
    label: str

    # Recorded test cases
    cases: dict[str, CaseNode]
    # Results of checks categorized by test case ID
    checks: dict[str, list[CheckNode]]
    # Network interactions by test case ID
    interactions: dict[str, Interaction]
    __slots__ = ("label", "status", "roots", "cases", "checks", "interactions")

    def __init__(self, *, label: str) -> None:
        self.label = label
        self.cases = {}
        self.checks = {}
        self.interactions = {}

    def record_case(
        self, *, parent_id: str | None, case: Case, transition: Transition | None, is_transition_applied: bool
    ) -> None:
        """Record a test case and its relationship to a parent, if applicable."""
        self.cases[case.id] = CaseNode(
            value=case,
            parent_id=parent_id,
            transition=transition,
            is_transition_applied=is_transition_applied,
        )

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
        """Iterate over all cases in the tree, starting from the root."""
        seen = {case_id}

        # First, find the root by going up
        current_id = case_id
        while True:
            current_node = self.cases.get(current_id)
            if current_node is None or current_node.parent_id is None:
                root_id = current_id
                break
            current_id = current_node.parent_id

        # Then traverse the whole tree from root
        def traverse(node_id: str) -> Iterator[Case]:
            # Get all children
            for case_id, node in self.cases.items():
                if node.parent_id == node_id and case_id not in seen:
                    seen.add(case_id)
                    yield node.value
                    # Recurse into children
                    yield from traverse(case_id)

        # Start traversal from root
        root_node = self.cases.get(root_id)
        if root_node and root_id not in seen:
            seen.add(root_id)
            yield root_node.value
        yield from traverse(root_id)

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
    is_transition_applied: bool

    __slots__ = ("value", "parent_id", "transition", "is_transition_applied")


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
