from __future__ import annotations
from typing import TYPE_CHECKING

from . import failures
from .exceptions import get_server_error
from .specs.openapi.checks import (
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
    status_code_conformance,
)

if TYPE_CHECKING:
    from .transports.responses import GenericResponse
    from .models import Case, CheckFunction


def not_a_server_error(response: GenericResponse, case: Case) -> bool | None:
    """A check to verify that the response is not a server-side error."""
    status_code = response.status_code
    if status_code >= 500:
        exc_class = get_server_error(status_code)
        raise exc_class(failures.ServerError.title, context=failures.ServerError(status_code=status_code))
    return None


DEFAULT_CHECKS: tuple[CheckFunction, ...] = (not_a_server_error,)
OPTIONAL_CHECKS = (
    status_code_conformance,
    content_type_conformance,
    response_headers_conformance,
    response_schema_conformance,
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
