from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional

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
    from schemathesis.stateful.graph import ExecutionGraph, ExecutionMetadata

CheckFunction = Callable[["CheckContext", "Response", "Case"], Optional[bool]]
ChecksConfig = dict[CheckFunction, Any]


class CheckContext:
    """Context for Schemathesis checks.

    Provides access to broader test execution data beyond individual test cases.
    """

    override: Override | None
    auth: tuple[str, str] | None
    headers: CaseInsensitiveDict | None
    config: ChecksConfig
    transport_kwargs: dict[str, Any] | None
    execution_graph: ExecutionGraph

    __slots__ = ("override", "auth", "headers", "config", "transport_kwargs", "execution_graph")

    def __init__(
        self,
        override: Override | None,
        auth: tuple[str, str] | None,
        headers: CaseInsensitiveDict | None,
        config: ChecksConfig,
        transport_kwargs: dict[str, Any] | None,
        execution_graph: ExecutionGraph,
    ) -> None:
        self.override = override
        self.auth = auth
        self.headers = headers
        self.config = config
        self.transport_kwargs = transport_kwargs
        self.execution_graph = execution_graph

    def find_parent(self, case: Case) -> Case | None:
        return self.execution_graph.find_parent(case)

    def get_metadata(self, case: Case) -> ExecutionMetadata | None:
        node = self.execution_graph._nodes.get(case.id)
        return node.metadata if node else None


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
