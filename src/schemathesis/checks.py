from __future__ import annotations

import json
from typing import TYPE_CHECKING

from schemathesis.core.failures import MalformedJson, ServerError
from schemathesis.core.registries import Registry
from schemathesis.internal.checks import CheckFunction

if TYPE_CHECKING:
    from .internal.checks import CheckContext
    from .models import Case
    from .transports.responses import GenericResponse

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
