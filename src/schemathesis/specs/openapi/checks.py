from __future__ import annotations
from typing import TYPE_CHECKING, Any, Generator, NoReturn

from ... import failures
from ...exceptions import (
    get_headers_error,
    get_malformed_media_type_error,
    get_missing_content_type_error,
    get_response_type_error,
    get_status_code_error,
)
from ...transports.content_types import parse_content_type
from .utils import expand_status_code

if TYPE_CHECKING:
    from ...transports import Response
    from ...models import Case


def status_code_conformance(response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    responses = case.operation.definition.raw.get("responses", {})
    # "default" can be used as the default response object for all HTTP codes that are not covered individually
    if "default" in responses:
        return None
    allowed_status_codes = list(_expand_responses(responses))
    if response.status_code not in allowed_status_codes:
        defined_status_codes = list(map(str, responses))
        responses_list = ", ".join(defined_status_codes)
        exc_class = get_status_code_error(response.status_code)
        raise exc_class(
            failures.UndefinedStatusCode.title,
            context=failures.UndefinedStatusCode(
                message=f"Received: {response.status_code}\nDocumented: {responses_list}",
                status_code=response.status_code,
                defined_status_codes=defined_status_codes,
                allowed_status_codes=allowed_status_codes,
            ),
        )
    return None  # explicitly return None for mypy


def _expand_responses(responses: dict[str | int, Any]) -> Generator[int, None, None]:
    for code in responses:
        yield from expand_status_code(code)


def content_type_conformance(response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    documented_content_types = case.operation.schema.get_content_types(case.operation, response)
    if not documented_content_types:
        return None
    content_type = response.headers.get("Content-Type")
    if not content_type:
        formatted_content_types = [f"\n- `{content_type}`" for content_type in documented_content_types]
        raise get_missing_content_type_error()(
            failures.MissingContentType.title,
            context=failures.MissingContentType(
                message=f"The following media types are documented in the schema:{''.join(formatted_content_types)}",
                media_types=documented_content_types,
            ),
        )
    for option in documented_content_types:
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
        failures.UndefinedContentType.title,
        context=failures.UndefinedContentType(
            message=f"Received: {content_type}\nDocumented: {', '.join(documented_content_types)}",
            content_type=content_type,
            defined_content_types=documented_content_types,
        ),
    )


def _reraise_malformed_media_type(exc: ValueError, location: str, actual: str, defined: str) -> NoReturn:
    message = f"Media type for {location} is incorrect\n\nReceived: {actual}\nDocumented: {defined}"
    raise get_malformed_media_type_error(message)(
        failures.MalformedMediaType.title,
        context=failures.MalformedMediaType(message=message, actual=actual, defined=defined),
    ) from exc


def response_headers_conformance(response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
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
    formatted_headers = [f"\n- `{header}`" for header in missing_headers]
    message = f"The following required headers are missing from the response:{''.join(formatted_headers)}"
    exc_class = get_headers_error(message)
    raise exc_class(
        failures.MissingHeaders.title,
        context=failures.MissingHeaders(message=message, missing_headers=missing_headers),
    )


def response_schema_conformance(response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    return case.operation.validate_response(response)
