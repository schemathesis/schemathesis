from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any, Dict, Generator, NoReturn, cast
from urllib.parse import parse_qs, urlparse

from ... import failures
from ...exceptions import (
    get_ensure_resource_availability_error,
    get_headers_error,
    get_ignored_auth_error,
    get_malformed_media_type_error,
    get_missing_content_type_error,
    get_negative_rejection_error,
    get_response_type_error,
    get_schema_validation_error,
    get_status_code_error,
    get_use_after_free_error,
)
from ...internal.transformation import convert_boolean_string
from ...transports.content_types import parse_content_type
from .utils import expand_status_code

if TYPE_CHECKING:
    from requests import PreparedRequest

    from ...models import APIOperation, Case
    from ...transports.responses import GenericResponse


def status_code_conformance(response: GenericResponse, case: Case) -> bool | None:
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
        exc_class = get_status_code_error(case.operation.verbose_name, response.status_code)
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


def content_type_conformance(response: GenericResponse, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    documented_content_types = case.operation.schema.get_content_types(case.operation, response)
    if not documented_content_types:
        return None
    content_type = response.headers.get("Content-Type")
    if not content_type:
        formatted_content_types = [f"\n- `{content_type}`" for content_type in documented_content_types]
        raise get_missing_content_type_error(case.operation.verbose_name)(
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
            _reraise_malformed_media_type(case, exc, "Schema", option, option)
        try:
            received_main, received_sub = parse_content_type(content_type)
        except ValueError as exc:
            _reraise_malformed_media_type(case, exc, "Response", content_type, option)
        if (
            (expected_main == "*" and expected_sub == "*")
            or (expected_main == received_main and expected_sub == "*")
            or (expected_main == "*" and expected_sub == received_sub)
            or (expected_main == received_main and expected_sub == received_sub)
        ):
            return None
    exc_class = get_response_type_error(
        case.operation.verbose_name, f"{expected_main}_{expected_sub}", f"{received_main}_{received_sub}"
    )
    raise exc_class(
        failures.UndefinedContentType.title,
        context=failures.UndefinedContentType(
            message=f"Received: {content_type}\nDocumented: {', '.join(documented_content_types)}",
            content_type=content_type,
            defined_content_types=documented_content_types,
        ),
    )


def _reraise_malformed_media_type(case: Case, exc: ValueError, location: str, actual: str, defined: str) -> NoReturn:
    message = f"Media type for {location} is incorrect\n\nReceived: {actual}\nDocumented: {defined}"
    raise get_malformed_media_type_error(case.operation.verbose_name, message)(
        failures.MalformedMediaType.title,
        context=failures.MalformedMediaType(message=message, actual=actual, defined=defined),
    ) from exc


def response_headers_conformance(response: GenericResponse, case: Case) -> bool | None:
    import jsonschema

    from .parameters import OpenAPI20Parameter, OpenAPI30Parameter
    from .schemas import BaseOpenAPISchema, OpenApi30, _maybe_raise_one_or_more

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    resolved = case.operation.schema.get_headers(case.operation, response)
    if not resolved:
        return None
    scopes, defined_headers = resolved
    if not defined_headers:
        return None

    missing_headers = [
        header
        for header, definition in defined_headers.items()
        if header not in response.headers and definition.get(case.operation.schema.header_required_field, False)
    ]
    errors = []
    if missing_headers:
        formatted_headers = [f"\n- `{header}`" for header in missing_headers]
        message = f"The following required headers are missing from the response:{''.join(formatted_headers)}"
        exc_class = get_headers_error(case.operation.verbose_name, message)
        try:
            raise exc_class(
                failures.MissingHeaders.title,
                context=failures.MissingHeaders(message=message, missing_headers=missing_headers),
            )
        except Exception as exc:
            errors.append(exc)
    for name, definition in defined_headers.items():
        value = response.headers.get(name)
        if value is not None:
            with case.operation.schema._validating_response(scopes) as resolver:
                if "$ref" in definition:
                    _, definition = resolver.resolve(definition["$ref"])
                parameter_definition = {"in": "header", **definition}
                parameter: OpenAPI20Parameter | OpenAPI30Parameter
                if isinstance(case.operation.schema, OpenApi30):
                    parameter = OpenAPI30Parameter(parameter_definition)
                else:
                    parameter = OpenAPI20Parameter(parameter_definition)
                schema = parameter.as_json_schema(case.operation)
                coerced = _coerce_header_value(value, schema)
                try:
                    jsonschema.validate(
                        coerced,
                        schema,
                        cls=case.operation.schema.validator_cls,
                        resolver=resolver,
                        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
                    )
                except jsonschema.ValidationError as exc:
                    exc_class = get_schema_validation_error(case.operation.verbose_name, exc)
                    ctx = failures.ValidationErrorContext.from_exception(
                        exc, output_config=case.operation.schema.output_config
                    )
                    try:
                        raise exc_class("Response header does not conform to the schema", context=ctx) from exc
                    except Exception as exc:
                        errors.append(exc)
    return _maybe_raise_one_or_more(errors)  # type: ignore[func-returns-value]


def _coerce_header_value(value: str, schema: dict[str, Any]) -> str | int | float | None | bool:
    schema_type = schema.get("type")

    if schema_type == "string":
        return value
    if schema_type == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if schema_type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if schema_type == "null" and value.lower() == "null":
        return None
    if schema_type == "boolean":
        return convert_boolean_string(value)
    return value


def response_schema_conformance(response: GenericResponse, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    return case.operation.validate_response(response)


def negative_data_rejection(response: GenericResponse, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    if (
        case.data_generation_method
        and case.data_generation_method.is_negative
        and 200 <= response.status_code < 300
        and not has_only_additional_properties_in_non_body_parameters(case)
    ):
        exc_class = get_negative_rejection_error(case.operation.verbose_name, response.status_code)
        raise exc_class(
            failures.AcceptedNegativeData.title,
            context=failures.AcceptedNegativeData(message="Negative data was not rejected as expected by the API"),
        )
    return None


def has_only_additional_properties_in_non_body_parameters(case: Case) -> bool:
    # Check if the case contains only additional properties in query, headers, or cookies.
    # This function is used to determine if negation is solely in the form of extra properties,
    # which are often ignored for backward-compatibility by the tested apps
    from ._hypothesis import get_schema_for_location

    meta = case.meta
    if meta is None:
        # Ignore manually created cases
        return False
    if (meta.body and meta.body.is_negative) or (meta.path_parameters and meta.path_parameters.is_negative):
        # Body or path negations always imply other negations
        return False
    validator_cls = case.operation.schema.validator_cls  # type: ignore[attr-defined]
    for container in ("query", "headers", "cookies"):
        meta_for_location = getattr(meta, container)
        value = getattr(case, container)
        if value is not None and meta_for_location is not None and meta_for_location.is_negative:
            parameters = getattr(case.operation, container)
            value_without_additional_properties = {k: v for k, v in value.items() if k in parameters}
            schema = get_schema_for_location(case.operation, container, parameters)
            if not validator_cls(schema).is_valid(value_without_additional_properties):
                # Other types of negation found
                return False
    # Only additional properties are added
    return True


def use_after_free(response: GenericResponse, original: Case) -> bool | None:
    from ...transports.responses import get_reason
    from .schemas import BaseOpenAPISchema

    if not isinstance(original.operation.schema, BaseOpenAPISchema):
        return True
    if response.status_code == 404 or not original.source:
        return None
    response = original.source.response
    case = original.source.case
    while True:
        # Find the most recent successful DELETE call that corresponds to the current operation
        if case.operation.method.lower() == "delete" and 200 <= response.status_code < 300:
            if _is_prefix_operation(
                ResourcePath(case.path, case.path_parameters or {}),
                ResourcePath(original.path, original.path_parameters or {}),
            ):
                free = f"{case.operation.method.upper()} {case.formatted_path}"
                usage = f"{original.operation.method} {original.formatted_path}"
                exc_class = get_use_after_free_error(case.operation.verbose_name)
                reason = get_reason(response.status_code)
                message = (
                    "The API did not return a `HTTP 404 Not Found` response "
                    f"(got `HTTP {response.status_code} {reason}`) for a resource that was previously deleted.\n\nThe resource was deleted with `{free}`"
                )
                raise exc_class(
                    failures.UseAfterFree.title,
                    context=failures.UseAfterFree(
                        message=message,
                        free=free,
                        usage=usage,
                    ),
                )
        if case.source is None:
            break
        response = case.source.response
        case = case.source.case
    return None


def ensure_resource_availability(response: GenericResponse, original: Case) -> bool | None:
    from ...transports.responses import get_reason
    from .schemas import BaseOpenAPISchema

    if not isinstance(original.operation.schema, BaseOpenAPISchema):
        return True
    if (
        # Response indicates a client error, even though all available parameters were taken from links
        # and comes from a POST request. This case likely means that the POST request actually did not
        # save the resource and it is not available for subsequent operations
        400 <= response.status_code < 500
        and original.source
        and original.source.case.operation.method.upper() == "POST"
        and 200 <= original.source.response.status_code < 400
        and original.source.overrides_all_parameters
    ):
        created_with = original.source.case.operation.verbose_name
        not_available_with = original.operation.verbose_name
        exc_class = get_ensure_resource_availability_error(created_with)
        reason = get_reason(response.status_code)
        message = (
            f"The API returned `{response.status_code} {reason}` for a resource that was just created.\n\n"
            f"Created with      : `{created_with}`\n"
            f"Not available with: `{not_available_with}`"
        )
        raise exc_class(
            failures.EnsureResourceAvailability.title,
            context=failures.EnsureResourceAvailability(
                message=message, created_with=created_with, not_available_with=not_available_with
            ),
        )
    return None


def ignored_auth(response: GenericResponse, case: Case) -> bool | None:
    """Check if an operation declares authentication as a requirement but does not actually enforce it."""
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema):
        return True
    security_parameters = _get_security_parameters(case.operation)
    # Authentication is required for this API operation and response is successful
    # Will it still be successful if there is no auth?
    if security_parameters and 200 <= response.status_code < 300:
        if _contains_auth(response.request, security_parameters):
            # If there is auth in the request, then drop it and retry the call
            _remove_auth_from_case(case, security_parameters)
            new_response = case.operation.schema.transport.send(case)
            if 200 <= new_response.status_code < 300:
                # Mutate the response object in place on the best effort basis
                if hasattr(response, "__attrs__"):
                    for attribute in new_response.__attrs__:
                        setattr(response, attribute, getattr(new_response, attribute))
                else:
                    response.__dict__.update(new_response.__dict__)
                _raise_auth_error(new_response, case.operation.verbose_name)
        else:
            # Successful response when there is no auth
            _raise_auth_error(response, case.operation.verbose_name)
    return None


def _raise_auth_error(response: GenericResponse, operation: str) -> NoReturn:
    from ...transports.responses import get_reason

    exc_class = get_ignored_auth_error(operation)
    reason = get_reason(response.status_code)
    message = f"The API returned `{response.status_code} {reason}` for `{operation}` that requires authentication."
    raise exc_class(
        failures.IgnoredAuth.title,
        context=failures.IgnoredAuth(message=message),
    )


SecurityParameter = Dict[str, Any]


def _get_security_parameters(operation: APIOperation) -> list[SecurityParameter]:
    """Extract security definitions that are active for the given operation and convert them into parameters."""
    from .schemas import BaseOpenAPISchema

    schema = cast(BaseOpenAPISchema, operation.schema)
    return [
        schema.security._to_parameter(parameter)
        for parameter in schema.security._get_active_definitions(schema.raw_schema, operation, schema.resolver)
        if parameter["type"] in ("apiKey", "basic", "http")
    ]


def _contains_auth(request: PreparedRequest, security_parameters: list[SecurityParameter]) -> bool:
    """Whether a request has authentication declared in the schema."""
    from requests.cookies import RequestsCookieJar

    parsed = urlparse(request.url)
    query = parse_qs(parsed.query)  # type: ignore
    # Load the `Cookie` header separately, because it is possible that `request._cookies` and the header are out of sync
    header_cookies: SimpleCookie = SimpleCookie()
    raw_cookie = request.headers.get("Cookie")
    if raw_cookie is not None:
        header_cookies.load(raw_cookie)

    def has_header(p: dict[str, Any]) -> bool:
        return p["in"] == "header" and p["name"] in request.headers

    def has_query(p: dict[str, Any]) -> bool:
        return p["in"] == "query" and p["name"] in query

    def has_cookie(p: dict[str, Any]) -> bool:
        cookies = cast(RequestsCookieJar, request._cookies)  # type: ignore
        return p["in"] == "cookie" and (p["name"] in cookies or p["name"] in header_cookies)

    for parameter in security_parameters:
        if has_header(parameter) or has_query(parameter) or has_cookie(parameter):
            return True

    return False


def _remove_auth_from_case(case: Case, security_parameters: list[SecurityParameter]) -> None:
    """Remove security parameters from a generated case.

    It mutates `case` in place.
    """
    for parameter in security_parameters:
        name = parameter["name"]
        if parameter["in"] == "header" and case.headers:
            case.headers.pop(name, None)
        if parameter["in"] == "query" and case.query:
            case.query.pop(name, None)
        if parameter["in"] == "cookie" and case.cookies:
            case.cookies.pop(name, None)


@dataclass
class ResourcePath:
    """A path to a resource with variables."""

    value: str
    variables: dict[str, str]

    __slots__ = ("value", "variables")

    def get(self, key: str) -> str:
        return self.variables[key.lstrip("{").rstrip("}")]


def _is_prefix_operation(lhs: ResourcePath, rhs: ResourcePath) -> bool:
    lhs_parts = lhs.value.rstrip("/").split("/")
    rhs_parts = rhs.value.rstrip("/").split("/")

    # Left has more parts, can't be a prefix
    if len(lhs_parts) > len(rhs_parts):
        return False

    for left, right in zip(lhs_parts, rhs_parts):
        if left.startswith("{") and right.startswith("{"):
            if str(lhs.get(left)) != str(rhs.get(right)):
                return False
        elif left != right and left.rstrip("s") != right.rstrip("s"):
            # Parts don't match, not a prefix
            return False

    # If we've reached this point, the LHS path is a prefix of the RHS path
    return True
