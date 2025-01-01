from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator, Optional, Protocol

from schemathesis.core.failures import (
    Failure,
    FailureGroup,
    MalformedJson,
    MaxResponseTimeConfig,
    ResponseTimeExceeded,
    ServerError,
)
from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict

    from schemathesis.generation.case import Case

CheckFunction = Callable[["CheckContext", "Response", "Case"], Optional[bool]]
ChecksConfig = dict[CheckFunction, Any]


class TrackerProtocol(Protocol):
    def on_new_case(self, *, parent_id: str, case: Case) -> None: ...
    def on_new_response(self, *, case_id: str, response: Response) -> None: ...
    def find_parent(self, *, case_id: str) -> Case | None: ...
    def find_ancestors_and_their_children(self, *, case_id: str) -> Iterator[Case]: ...
    def find_response(self, *, case_id: str) -> Response | None: ...


class CheckContext:
    """Context for Schemathesis checks.

    Provides access to broader test execution data beyond individual test cases.
    """

    override: Override | None
    auth: tuple[str, str] | None
    headers: CaseInsensitiveDict | None
    config: ChecksConfig
    transport_kwargs: dict[str, Any] | None
    tracker: TrackerProtocol | None

    __slots__ = ("override", "auth", "headers", "config", "transport_kwargs", "tracker")

    def __init__(
        self,
        override: Override | None,
        auth: tuple[str, str] | None,
        headers: CaseInsensitiveDict | None,
        config: ChecksConfig,
        transport_kwargs: dict[str, Any] | None,
        tracker: TrackerProtocol | None = None,
    ) -> None:
        self.override = override
        self.auth = auth
        self.headers = headers
        self.config = config
        self.transport_kwargs = transport_kwargs
        self.tracker = tracker

    def find_parent(self, *, case_id: str) -> Case | None:
        if self.tracker is not None:
            return self.tracker.find_parent(case_id=case_id)
        return None

    def find_ancestors_and_their_children(self, *, case_id: str) -> Iterator[Case]:
        if self.tracker is not None:
            yield from self.tracker.find_ancestors_and_their_children(case_id=case_id)

    def find_response(self, *, case_id: str) -> Response | None:
        if self.tracker is not None:
            return self.tracker.find_response(case_id=case_id)
        return None

    def on_new_case(self, *, parent_id: str, case: Case) -> None:
        if self.tracker is not None:
            self.tracker.on_new_case(parent_id=parent_id, case=case)

    def on_new_response(self, *, case_id: str, response: Response) -> None:
        if self.tracker is not None:
            self.tracker.on_new_response(case_id=case_id, response=response)


CHECKS = Registry[CheckFunction]()
check = CHECKS.register


@check
def not_a_server_error(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """A check to verify that the response is not a server-side error."""
    from .specs.graphql.schemas import GraphQLSchema
    from .specs.graphql.validation import validate_graphql_response

    status_code = response.status_code
    if status_code >= 500:
        raise ServerError(operation=case.operation.label, status_code=status_code)
    if isinstance(case.operation.schema, GraphQLSchema):
        try:
            data = response.json()
            validate_graphql_response(case, data)
        except json.JSONDecodeError as exc:
            raise MalformedJson.from_exception(operation=case.operation.label, exc=exc) from None
    return None


def max_response_time(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    config = ctx.config.get(max_response_time, MaxResponseTimeConfig())
    elapsed = response.elapsed
    if elapsed > config.limit:
        raise ResponseTimeExceeded(
            operation=case.operation.label,
            message=f"Actual: {elapsed:.2f}ms\nLimit: {config.limit * 1000:.2f}ms",
            elapsed=elapsed,
            deadline=config.limit,
        )
    return None


def run_checks(
    *,
    case: Case,
    response: Response,
    ctx: CheckContext,
    checks: Iterable[CheckFunction],
    on_failure: Callable[[str, set[Failure], Failure], None],
    on_success: Callable[[str, Case], None] | None = None,
) -> set[Failure]:
    """Run a set of checks against a response."""
    collected: set[Failure] = set()

    for check in checks:
        name = check.__name__
        try:
            skip_check = check(ctx, response, case)
            if not skip_check and on_success:
                on_success(name, case)
        except Failure as failure:
            on_failure(name, collected, failure.with_traceback(None))
        except AssertionError as exc:
            custom_failure = Failure.from_assertion(
                name=name,
                operation=case.operation.label,
                exc=exc,
            )
            on_failure(name, collected, custom_failure)
        except FailureGroup as group:
            for sub_failure in group.exceptions:
                on_failure(name, collected, sub_failure)

    return collected
