from __future__ import annotations

import json
from typing import TYPE_CHECKING

from schemathesis.core.failures import MalformedJson, ServerError

from .specs.openapi.checks import (
    content_type_conformance,
    ignored_auth,
    negative_data_rejection,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)

if TYPE_CHECKING:
    from .internal.checks import CheckContext, CheckFunction
    from .models import Case
    from .transports.responses import GenericResponse


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


DEFAULT_CHECKS: tuple[CheckFunction, ...] = (not_a_server_error,)
OPTIONAL_CHECKS = (
    status_code_conformance,
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    negative_data_rejection,
    ignored_auth,
)
ALL_CHECKS: tuple[CheckFunction, ...] = DEFAULT_CHECKS + OPTIONAL_CHECKS


def register(check: CheckFunction) -> CheckFunction:
    """Register a new check for schemathesis CLI.

    :param check: A function to validate API responses.

    .. code-block:: python

        @schemathesis.check
        def new_check(ctx, response, case):
            # some awesome assertions!
            ...
    """
    from . import cli

    global ALL_CHECKS

    ALL_CHECKS += (check,)
    cli.CHECKS_TYPE.choices += (check.__name__,)  # type: ignore
    return check
