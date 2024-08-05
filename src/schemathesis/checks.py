from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import failures
from .exceptions import get_response_parsing_error, get_server_error
from .specs.openapi.checks import (
    content_type_conformance,
    ignored_auth,
    negative_data_rejection,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)

if TYPE_CHECKING:
    from .models import Case, CheckFunction
    from .transports.responses import GenericResponse


def not_a_server_error(response: GenericResponse, case: Case) -> bool | None:
    """A check to verify that the response is not a server-side error."""
    from .specs.graphql.schemas import GraphQLCase
    from .specs.graphql.validation import validate_graphql_response
    from .transports.responses import get_json

    status_code = response.status_code
    if status_code >= 500:
        exc_class = get_server_error(case.operation.verbose_name, status_code)
        raise exc_class(failures.ServerError.title, context=failures.ServerError(status_code=status_code))
    if isinstance(case, GraphQLCase):
        try:
            data = get_json(response)
            validate_graphql_response(data)
        except json.JSONDecodeError as exc:
            exc_class = get_response_parsing_error(case.operation.verbose_name, exc)
            context = failures.JSONDecodeErrorContext.from_exception(exc)
            raise exc_class(context.title, context=context) from exc
    return None


def _make_max_response_time_failure_message(elapsed_time: float, max_response_time: int) -> str:
    return f"Actual: {elapsed_time:.2f}ms\nLimit: {max_response_time}.00ms"


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
        def new_check(response, case):
            # some awesome assertions!
            ...
    """
    from . import cli

    global ALL_CHECKS

    ALL_CHECKS += (check,)
    cli.CHECKS_TYPE.choices += (check.__name__,)  # type: ignore
    return check
