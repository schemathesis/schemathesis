from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from schemathesis.core.failures import Failure
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.runner import Status
from schemathesis.runner.models import Request

if TYPE_CHECKING:
    import requests


@dataclass
class DataForest:
    """Represents a complete logical block of testing."""

    # TODO: the idea is that schemathesis can generate disjoint series of test cases, therefore it is a forest

    id: uuid.UUID
    # Human-readable label
    label: str

    # Roots of all trees included in this forest
    roots: list[str]
    # Test cases within the forest
    cases: dict[str, CaseNode]
    # Results of all checks performed across the forest
    checks: dict[str, list[CheckNode]]
    # Transport-level information about interacting with the tested application
    interactions: dict[str, InteractionNode]

    __slots__ = ("id", "label", "status", "roots", "cases", "checks", "interactions")

    def __init__(self, *, label: str) -> None:
        self.id = uuid.uuid4()
        self.label = label
        self.roots = []
        self.cases = {}
        self.checks = {}
        self.interactions = {}

    def add_root(self, *, case: Case) -> None:
        self.roots.append(case.id)
        self.add_case(parent_id=None, case=case)

    def add_case(self, *, parent_id: str | None, case: Case) -> None:
        self.cases[case.id] = CaseNode(value=case, parent_id=parent_id)

    def add_response(self, *, case_id: str, response: Response) -> None:
        request = Request.from_prepared_request(response.request)
        self.interactions[case_id] = InteractionNode(request=request, response=response)

    def add_network_error(
        self, *, case_id: str, request: requests.PreparedRequest, error: requests.ConnectionError | requests.Timeout
    ) -> None:
        self.interactions[case_id] = InteractionNode(request=Request.from_prepared_request(request), response=error)

    def add_check(
        self, *, name: str, case_id: str, status: Status, code_sample: str | None, failure: Failure | None
    ) -> None:
        self.checks.setdefault(case_id, []).append(
            CheckNode(name=name, status=status, code_sample=code_sample, failure=failure)
        )

    def get_failure_data(self, *, parent_id: str, failure: Failure) -> FailureData:
        case_id = getattr(failure, "case_id", parent_id) or parent_id
        case = self.cases[case_id].value
        request = self.interactions[case_id].request
        response = self.interactions[case_id].response
        assert isinstance(response, Response)
        headers = {key: value[0] for key, value in request.headers.items()}
        return FailureData(case=case, headers=headers, verify=response.verify)

    def find_parent(self, *, case_id: str) -> Case | None:
        case = self.cases.get(case_id)
        if case is not None and case.parent_id is not None:
            parent = self.cases.get(case.parent_id)
            return parent.value if parent else None
        return None

    def find_ancestors_and_their_children(self, *, case_id: str) -> Iterator[Case]:
        """Returns all ancestors and their children for a given case."""
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


@dataclass
class CaseNode:
    value: Case
    parent_id: str | None

    __slots__ = ("value", "parent_id")


@dataclass
class CheckNode:
    name: str
    status: Status
    # TODO: rework
    code_sample: str | None
    failure: Failure | None

    __slots__ = ("name", "status", "code_sample", "failure")


TIMEZONE = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo


@dataclass
class InteractionNode:
    request: Request
    response: Response | requests.Timeout | requests.ConnectionError
    recorded_at: str

    __slots__ = ("request", "response", "recorded_at")

    def __init__(self, request: Request, response: Response | requests.Timeout | requests.ConnectionError) -> None:
        self.request = request
        self.response = response
        self.recorded_at = datetime.datetime.now(TIMEZONE).isoformat()


@dataclass
class FailureData:
    case: Case
    headers: dict[str, str]
    verify: bool

    __slots__ = ("case", "headers", "verify")
