from typing import TYPE_CHECKING, Any, Dict, Generator, Optional, Union

from ...exceptions import (
    get_headers_error,
    get_malformed_media_type_error,
    get_missing_content_type_error,
    get_response_type_error,
    get_status_code_error,
)
from ...utils import GenericResponse, are_content_types_equal, parse_content_type
from .schemas import BaseOpenAPISchema
from .utils import expand_status_code

if TYPE_CHECKING:
    from ...models import Case


def status_code_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    responses = case.operation.definition.raw.get("responses", {})
    # "default" can be used as the default response object for all HTTP codes that are not covered individually
    if "default" in responses:
        return None
    allowed_response_statuses = list(_expand_responses(responses))
    if response.status_code not in allowed_response_statuses:
        responses_list = ", ".join(map(str, responses))
        message = (
            f"Received a response with a status code, which is not defined in the schema: "
            f"{response.status_code}\n\nDeclared status codes: {responses_list}"
        )
        exc_class = get_status_code_error(response.status_code)
        raise exc_class(message)
    return None  # explicitly return None for mypy


def _expand_responses(responses: Dict[Union[str, int], Any]) -> Generator[int, None, None]:
    for code in responses:
        yield from expand_status_code(code)


def content_type_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    content_types = case.operation.schema.get_content_types(case.operation, response)
    if not content_types:
        return None
    content_type = response.headers.get("Content-Type")
    if not content_type:
        raise get_missing_content_type_error()("Response is missing the `Content-Type` header")
    for option in content_types:
        try:
            if are_content_types_equal(option, content_type):
                return None
        except ValueError as exc:
            raise get_malformed_media_type_error(str(exc))(str(exc)) from exc
        expected_main, expected_sub = parse_content_type(option)
        received_main, received_sub = parse_content_type(content_type)
    exc_class = get_response_type_error(f"{expected_main}_{expected_sub}", f"{received_main}_{received_sub}")
    raise exc_class(
        f"Received a response with '{content_type}' Content-Type, "
        f"but it is not declared in the schema.\n\n"
        f"Defined content types: {', '.join(content_types)}"
    )


def response_headers_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    defined_headers = case.operation.schema.get_headers(case.operation, response)
    if not defined_headers:
        return None

    missing_headers = [header for header in defined_headers if header not in response.headers]
    if not missing_headers:
        return None
    message = ",".join(missing_headers)
    exc_class = get_headers_error(message)
    raise exc_class(f"Received a response with missing headers: {message}")


def response_schema_conformance(response: GenericResponse, case: "Case") -> None:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    return case.operation.validate_response(response)
