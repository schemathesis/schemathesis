from typing import TYPE_CHECKING, Optional, Tuple

from .exceptions import get_status_code_error
from .protocols import CaseProtocol
from .specs.openapi.checks import content_type_conformance, response_schema_conformance, status_code_conformance
from .utils import GenericResponse

if TYPE_CHECKING:
    from .models import CheckFunction


def not_a_server_error(  # pylint: disable=useless-return
    response: GenericResponse, case: CaseProtocol
) -> Optional[bool]:
    """A check to verify that the response is not a server-side error."""
    if response.status_code >= 500:
        exc_class = get_status_code_error(response.status_code)
        raise exc_class(f"Received a response with 5xx status code: {response.status_code}")
    # This "return" is needed for mypy
    return None


DEFAULT_CHECKS = (not_a_server_error,)
OPTIONAL_CHECKS = (status_code_conformance, content_type_conformance, response_schema_conformance)
ALL_CHECKS: Tuple["CheckFunction", ...] = DEFAULT_CHECKS + OPTIONAL_CHECKS
