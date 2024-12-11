from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Optional

from schemathesis._override import CaseOverride
from schemathesis.core.failures import MalformedJson, MaxResponseTimeConfig, ResponseTimeExceeded, ServerError
from schemathesis.core.registries import Registry

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict

    from .models import Case
    from .transports.responses import GenericResponse

CheckFunction = Callable[["CheckContext", "GenericResponse", "Case"], Optional[bool]]
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

    __slots__ = ("override", "auth", "headers", "config", "transport_kwargs")

    def __init__(
        self,
        override: CaseOverride | None,
        auth: tuple[str, str] | None,
        headers: CaseInsensitiveDict | None,
        config: ChecksConfig,
        transport_kwargs: dict[str, Any] | None,
    ) -> None:
        self.override = override
        self.auth = auth
        self.headers = headers
        self.config = config
        self.transport_kwargs = transport_kwargs


CHECKS = Registry[CheckFunction]()
check = CHECKS.register


@check
def not_a_server_error(ctx: CheckContext, response: GenericResponse, case: Case) -> bool | None:
    """A check to verify that the response is not a server-side error."""
    from .specs.graphql.schemas import GraphQLCase
    from .specs.graphql.validation import validate_graphql_response
    from .transports.responses import get_json

    status_code = response.status_code
    if status_code >= 500:
        raise ServerError(operation=case.operation.verbose_name, status_code=status_code)
    if isinstance(case, GraphQLCase):
        try:
            data = get_json(response)
            validate_graphql_response(case, data)
        except json.JSONDecodeError as exc:
            raise MalformedJson.from_exception(operation=case.operation.verbose_name, exc=exc) from None
    return None


def max_response_time(ctx: CheckContext, response: GenericResponse, case: Case) -> bool | None:
    config = ctx.config.get(max_response_time, MaxResponseTimeConfig())
    elapsed = response.elapsed.total_seconds()
    if elapsed > config.limit:
        raise ResponseTimeExceeded(
            operation=case.operation.verbose_name,
            message=f"Actual: {elapsed:.2f}ms\nLimit: {config.limit * 1000:.2f}ms",
            elapsed=elapsed,
            deadline=config.limit,
        )
    return None
