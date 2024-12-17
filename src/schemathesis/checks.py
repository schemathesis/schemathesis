from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Optional

from schemathesis._override import CaseOverride
from schemathesis.core.failures import MalformedJson, MaxResponseTimeConfig, ResponseTimeExceeded, ServerError
from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict

    from schemathesis.stateful.graph import ExecutionGraph, ExecutionMetadata

    from .models import Case

CheckFunction = Callable[["CheckContext", "Response", "Case"], Optional[bool]]
ChecksConfig = dict[CheckFunction, Any]


class CheckContext:
    """Context for Schemathesis checks.

    Provides access to broader test execution data beyond individual test cases.
    """

    override: CaseOverride | None
    auth: tuple[str, str] | None
    headers: CaseInsensitiveDict | None
    config: ChecksConfig
    transport_kwargs: dict[str, Any] | None
    execution_graph: ExecutionGraph

    __slots__ = ("override", "auth", "headers", "config", "transport_kwargs", "execution_graph")

    def __init__(
        self,
        override: CaseOverride | None,
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
        return self.execution_graph.get_parent(case)

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
        raise ServerError(operation=case.operation.verbose_name, status_code=status_code)
    if isinstance(case.operation.schema, GraphQLSchema):
        try:
            data = response.json()
            validate_graphql_response(case, data)
        except json.JSONDecodeError as exc:
            raise MalformedJson.from_exception(operation=case.operation.verbose_name, exc=exc) from None
    return None


def max_response_time(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    config = ctx.config.get(max_response_time, MaxResponseTimeConfig())
    elapsed = response.elapsed
    if elapsed > config.limit:
        raise ResponseTimeExceeded(
            operation=case.operation.verbose_name,
            message=f"Actual: {elapsed:.2f}ms\nLimit: {config.limit * 1000:.2f}ms",
            elapsed=elapsed,
            deadline=config.limit,
        )
    return None
