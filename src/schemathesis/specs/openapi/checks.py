from typing import TYPE_CHECKING, Any, Dict, Generator, NoReturn, Optional, Union

from ... import failures
from ...exceptions import (
    get_headers_error,
    get_malformed_media_type_error,
    get_missing_content_type_error,
    get_response_type_error,
    get_status_code_error,
)
from ...utils import GenericResponse, parse_content_type
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
    allowed_status_codes = list(_expand_responses(responses))
    if response.status_code not in allowed_status_codes:
        defined_status_codes = list(map(str, responses))
        responses_list = ", ".join(defined_status_codes)
        message = (
            f"Received a response with a status code, which is not defined in the schema: "
            f"{response.status_code}\n\nDeclared status codes: {responses_list}"
        )
        exc_class = get_status_code_error(response.status_code)
        raise exc_class(
            message,
            context=failures.UndefinedStatusCode(
                status_code=response.status_code,
                defined_status_codes=defined_status_codes,
                allowed_status_codes=allowed_status_codes,
            ),
        )
    return None  # explicitly return None for mypy


def _expand_responses(responses: Dict[Union[str, int], Any]) -> Generator[int, None, None]:
    for code in responses:
        yield from expand_status_code(code)


def content_type_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    defined_content_types = case.operation.schema.get_content_types(case.operation, response)
    if not defined_content_types:
        return None
    content_type = response.headers.get("Content-Type")
    if not content_type:
        formatted_media_types = "\n    ".join(defined_content_types)
        raise get_missing_content_type_error()(
            "The response is missing the `Content-Type` header. The schema defines the following media types:\n\n"
            f"    {formatted_media_types}",
            context=failures.MissingContentType(defined_content_types),
        )
    for option in defined_content_types:
        try:
            expected_main, expected_sub = parse_content_type(option)
        except ValueError as exc:
            _reraise_malformed_media_type(exc, "Schema", option, option)
        try:
            received_main, received_sub = parse_content_type(content_type)
        except ValueError as exc:
            _reraise_malformed_media_type(exc, "Response", content_type, option)
        if (expected_main, expected_sub) == (received_main, received_sub):
            return None
    exc_class = get_response_type_error(f"{expected_main}_{expected_sub}", f"{received_main}_{received_sub}")
    raise exc_class(
        f"Received a response with '{content_type}' Content-Type, "
        f"but it is not declared in the schema.\n\n"
        f"Defined content types: {', '.join(defined_content_types)}",
        context=failures.UndefinedContentType(content_type=content_type, defined_content_types=defined_content_types),
    )


def _reraise_malformed_media_type(exc: ValueError, location: str, actual: str, defined: str) -> NoReturn:
    message = (
        f"{location} has a malformed media type: `{actual}`. Please, ensure that this media type conforms to "
        f"the `type-name/subtype-name` format defined by RFC 6838."
    )
    raise get_malformed_media_type_error(message)(
        message, context=failures.MalformedMediaType(actual=actual, defined=defined)
    ) from exc


def response_headers_conformance(response: GenericResponse, case: "Case") -> Optional[bool]:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    defined_headers = case.operation.schema.get_headers(case.operation, response)
    if not defined_headers:
        return None

    missing_headers = [
        header
        for header, definition in defined_headers.items()
        if header not in response.headers and definition.get(case.operation.schema.header_required_field, False)
    ]
    if not missing_headers:
        return None
    message = ",".join(missing_headers)
    exc_class = get_headers_error(message)
    raise exc_class(
        f"Received a response with missing headers: {message}",
        context=failures.MissingHeaders(missing_headers=missing_headers),
    )


def response_schema_conformance(response: GenericResponse, case: "Case") -> None:
    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        raise TypeError("This check can be used only with Open API schemas")
    return case.operation.validate_response(response)
