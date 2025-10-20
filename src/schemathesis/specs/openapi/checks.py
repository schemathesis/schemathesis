from __future__ import annotations

import enum
import http.client
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any, Iterator, Mapping, NoReturn, cast
from urllib.parse import parse_qs, urlparse

import schemathesis
from schemathesis.checks import CheckContext
from schemathesis.core import media_types, string_to_boolean
from schemathesis.core.failures import Failure
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.generation.meta import CoveragePhaseData, TestPhase
from schemathesis.openapi.checks import (
    AcceptedNegativeData,
    EnsureResourceAvailability,
    IgnoredAuth,
    JsonSchemaError,
    MalformedMediaType,
    MissingContentType,
    MissingHeaderNotRejected,
    MissingHeaders,
    RejectedPositiveData,
    UndefinedContentType,
    UndefinedStatusCode,
    UnsupportedMethodResponse,
    UseAfterFree,
)
from schemathesis.transport.prepare import prepare_path

from .utils import expand_status_code, expand_status_codes

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.parameters import OpenApiParameterSet


def is_unexpected_http_status_case(case: Case) -> bool:
    # Skip checks for requests using HTTP methods not defined in the API spec
    return bool(
        case.meta
        and isinstance(case.meta.phase.data, CoveragePhaseData)
        and case.meta.phase.data.description.startswith("Unspecified HTTP method")
    )


@schemathesis.check
def status_code_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema) or is_unexpected_http_status_case(case):
        return True
    status_codes = case.operation.responses.status_codes
    # "default" can be used as the default response object for all HTTP codes that are not covered individually
    if "default" in status_codes:
        return None
    allowed_status_codes = list(_expand_status_codes(status_codes))
    if response.status_code not in allowed_status_codes:
        defined_status_codes = list(map(str, status_codes))
        responses_list = ", ".join(defined_status_codes)
        raise UndefinedStatusCode(
            operation=case.operation.label,
            status_code=response.status_code,
            defined_status_codes=defined_status_codes,
            allowed_status_codes=allowed_status_codes,
            message=f"Received: {response.status_code}\nDocumented: {responses_list}",
        )
    return None  # explicitly return None for mypy


def _expand_status_codes(responses: tuple[str, ...]) -> Iterator[int]:
    for code in responses:
        yield from expand_status_code(code)


@schemathesis.check
def content_type_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema) or is_unexpected_http_status_case(case):
        return True
    documented_content_types = case.operation.schema.get_content_types(case.operation, response)
    if not documented_content_types:
        return None
    content_types = response.headers.get("content-type")
    if not content_types:
        all_media_types = [f"\n- `{content_type}`" for content_type in documented_content_types]
        raise MissingContentType(
            operation=case.operation.label,
            message=f"The following media types are documented in the schema:{''.join(all_media_types)}",
            media_types=documented_content_types,
        )
    content_type = content_types[0]
    for option in documented_content_types:
        try:
            expected_main, expected_sub = media_types.parse(option)
        except ValueError:
            _reraise_malformed_media_type(case, "Schema", option, option)
        try:
            received_main, received_sub = media_types.parse(content_type)
        except ValueError:
            _reraise_malformed_media_type(case, "Response", content_type, option)
        if (
            (expected_main == "*" and expected_sub == "*")
            or (expected_main == received_main and expected_sub == "*")
            or (expected_main == "*" and expected_sub == received_sub)
            or (expected_main == received_main and expected_sub == received_sub)
        ):
            return None
    raise UndefinedContentType(
        operation=case.operation.label,
        message=f"Received: {content_type}\nDocumented: {', '.join(documented_content_types)}",
        content_type=content_type,
        defined_content_types=documented_content_types,
    )


def _reraise_malformed_media_type(case: Case, location: str, actual: str, defined: str) -> NoReturn:
    raise MalformedMediaType(
        operation=case.operation.label,
        message=f"Media type for {location} is incorrect\n\nReceived: {actual}\nDocumented: {defined}",
        actual=actual,
        defined=defined,
    )


@schemathesis.check
def response_headers_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    import jsonschema

    from .schemas import BaseOpenAPISchema, _maybe_raise_one_or_more

    if not isinstance(case.operation.schema, BaseOpenAPISchema) or is_unexpected_http_status_case(case):
        return True

    # Find the matching response definition
    response_definition = case.operation.responses.find_by_status_code(response.status_code)
    if response_definition is None:
        return None
    # Check whether the matching response definition has headers defined
    headers = response_definition.headers
    if not headers:
        return None

    errors: list[Failure] = []

    missing_headers = []

    for name, header in headers.items():
        values = response.headers.get(name.lower())
        if values is not None:
            value = values[0]
            coerced = _coerce_header_value(value, header.schema)
            try:
                header.validator.validate(coerced)
            except jsonschema.ValidationError as exc:
                errors.append(
                    JsonSchemaError.from_exception(
                        title="Response header does not conform to the schema",
                        operation=case.operation.label,
                        exc=exc,
                        config=case.operation.schema.config.output,
                    )
                )
        elif header.is_required:
            missing_headers.append(name)

    if missing_headers:
        formatted_headers = [f"\n- `{header}`" for header in missing_headers]
        message = f"The following required headers are missing from the response:{''.join(formatted_headers)}"
        errors.append(MissingHeaders(operation=case.operation.label, message=message, missing_headers=missing_headers))

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
        return string_to_boolean(value)
    return value


@schemathesis.check
def response_schema_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema) or is_unexpected_http_status_case(case):
        return True
    return case.operation.validate_response(response)


@schemathesis.check
def negative_data_rejection(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if (
        not isinstance(case.operation.schema, BaseOpenAPISchema)
        or case.meta is None
        or is_unexpected_http_status_case(case)
    ):
        return True

    config = ctx.config.negative_data_rejection
    allowed_statuses = expand_status_codes(config.expected_statuses or [])

    if (
        case.meta.generation.mode.is_negative
        and response.status_code not in allowed_statuses
        and not has_only_additional_properties_in_non_body_parameters(case)
    ):
        extra_info = ""
        phase = case.meta.phase
        if phase and phase.name == TestPhase.COVERAGE and isinstance(phase.data, CoveragePhaseData):
            parts: list[str] = []
            if "Missing" in phase.data.description:
                extra_info = f"\nInvalid component: {phase.data.description}"
            else:
                if phase.data.parameter:
                    parts.append(f"parameter `{phase.data.parameter}`")
                location = phase.data.parameter_location
                if location:
                    parts.append(f"in {location.name.lower()}")
                description = phase.data.description.lower()
                if parts:
                    parts.append(f"({description})")
                else:
                    parts.append(f"{description}")
                extra_info = "\nInvalid component: " + " ".join(parts)
        raise AcceptedNegativeData(
            operation=case.operation.label,
            message=f"Invalid data should have been rejected\nExpected: {', '.join(config.expected_statuses)}{extra_info}",
            status_code=response.status_code,
            expected_statuses=config.expected_statuses,
        )
    return None


@schemathesis.check
def positive_data_acceptance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if (
        not isinstance(case.operation.schema, BaseOpenAPISchema)
        or case.meta is None
        or is_unexpected_http_status_case(case)
    ):
        return True

    config = ctx.config.positive_data_acceptance
    allowed_statuses = expand_status_codes(config.expected_statuses or [])

    if case.meta.generation.mode.is_positive and response.status_code not in allowed_statuses:
        raise RejectedPositiveData(
            operation=case.operation.label,
            message=f"Valid data should have been accepted\nExpected: {', '.join(config.expected_statuses)}",
            status_code=response.status_code,
            allowed_statuses=config.expected_statuses,
        )
    return None


@schemathesis.check
def missing_required_header(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    meta = case.meta
    if meta is None or not isinstance(meta.phase.data, CoveragePhaseData) or is_unexpected_http_status_case(case):
        return None
    data = meta.phase.data
    if (
        data.parameter
        and data.parameter_location == ParameterLocation.HEADER
        and data.description
        and data.description.startswith("Missing ")
    ):
        if data.parameter.lower() == "authorization":
            expected_statuses = {401}
        else:
            config = ctx.config.missing_required_header
            expected_statuses = expand_status_codes(config.expected_statuses or [])
        if response.status_code not in expected_statuses:
            allowed = ", ".join(map(str, expected_statuses))
            raise MissingHeaderNotRejected(
                operation=f"{case.method} {case.path}",
                header_name=data.parameter,
                status_code=response.status_code,
                expected_statuses=list(expected_statuses),
                message=f"Missing header not rejected (got {response.status_code}, expected {allowed})",
            )
    return None


@schemathesis.check
def unsupported_method(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    meta = case.meta
    if meta is None or not isinstance(meta.phase.data, CoveragePhaseData) or response.request.method == "OPTIONS":
        return None
    data = meta.phase.data
    if data.description and data.description.startswith("Unspecified HTTP method:"):
        if response.status_code != 405:
            raise UnsupportedMethodResponse(
                operation=case.operation.label,
                method=cast(str, response.request.method),
                status_code=response.status_code,
                failure_reason="wrong_status",
                message=f"Wrong status for unsupported method {response.request.method} (got {response.status_code}, expected 405)",
            )

        allow_header = response.headers.get("allow")
        if not allow_header:
            raise UnsupportedMethodResponse(
                operation=case.operation.label,
                method=cast(str, response.request.method),
                status_code=response.status_code,
                allow_header_present=False,
                failure_reason="missing_allow_header",
                message=f"Missing Allow header for unsupported method {response.request.method}",
            )
    return None


def has_only_additional_properties_in_non_body_parameters(case: Case) -> bool:
    # Check if the case contains only additional properties in query, headers, or cookies.
    # This function is used to determine if negation is solely in the form of extra properties,
    # which are often ignored for backward-compatibility by the tested apps
    from ._hypothesis import get_schema_for_location
    from .schemas import BaseOpenAPISchema

    meta = case.meta
    if meta is None or not isinstance(case.operation.schema, BaseOpenAPISchema):
        # Ignore manually created cases
        return False
    if (ParameterLocation.BODY in meta.components and meta.components[ParameterLocation.BODY].mode.is_negative) or (
        ParameterLocation.PATH in meta.components and meta.components[ParameterLocation.PATH].mode.is_negative
    ):
        # Body or path negations always imply other negations
        return False
    validator_cls = case.operation.schema.adapter.jsonschema_validator_cls
    for location in (ParameterLocation.QUERY, ParameterLocation.HEADER, ParameterLocation.COOKIE):
        meta_for_location = meta.components.get(location)
        value = getattr(case, location.container_name)
        if value is not None and meta_for_location is not None and meta_for_location.mode.is_negative:
            container = getattr(case.operation, location.container_name)
            schema = get_schema_for_location(location, container)

            if _has_serialization_sensitive_types(schema, container):
                # Can't reliably determine if only additional properties were added
                continue

            value_without_additional_properties = {k: v for k, v in value.items() if k in container}
            if not validator_cls(schema).is_valid(value_without_additional_properties):
                # Other types of negation found
                return False
    # Only additional properties are added
    return True


def _has_serialization_sensitive_types(schema: dict, container: OpenApiParameterSet) -> bool:
    """Check if schema contains array or object types in defined parameters.

    In query/header/cookie parameters, arrays and objects are serialized to strings.
    This makes post-serialization validation against the original schema unreliable:

    - Generated: ["foo", "bar"] (array)
    - Serialized: "foo,bar" (string)

    Validation of string against array schema fails incorrectly.
    A better approach would be to apply serialization later on in the process.

    """
    from schemathesis.core.jsonschema import get_type

    properties = schema.get("properties", {})
    for prop_name, prop_schema in properties.items():
        if prop_name in container:
            types = get_type(prop_schema)
            if "array" in types or "object" in types:
                return True
    return False


@schemathesis.check
def use_after_free(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema) or is_unexpected_http_status_case(case):
        return True

    # Only check for use-after-free on successful responses (2xx) or redirects (3xx)
    # Other status codes indicate request-level issues / server errors, not successful resource access
    if not (200 <= response.status_code < 400):
        return None

    for related_case in ctx._find_related(case_id=case.id):
        parent = ctx._find_parent(case_id=related_case.id)
        if not parent:
            continue

        parent_response = ctx._find_response(case_id=parent.id)

        if (
            related_case.operation.method.lower() == "delete"
            and parent_response is not None
            and 200 <= parent_response.status_code < 300
        ):
            if _is_prefix_operation(
                ResourcePath(related_case.path, related_case.path_parameters or {}),
                ResourcePath(case.path, case.path_parameters or {}),
            ):
                free = f"{related_case.operation.method.upper()} {prepare_path(related_case.path, related_case.path_parameters)}"
                usage = f"{case.operation.method.upper()} {prepare_path(case.path, case.path_parameters)}"
                reason = http.client.responses.get(response.status_code, "Unknown")
                raise UseAfterFree(
                    operation=related_case.operation.label,
                    message=(
                        "The API did not return a `HTTP 404 Not Found` response "
                        f"(got `HTTP {response.status_code} {reason}`) for a resource that was previously deleted.\n\nThe resource was deleted with `{free}`"
                    ),
                    free=free,
                    usage=usage,
                )

    return None


@schemathesis.check
def ensure_resource_availability(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from .schemas import BaseOpenAPISchema

    if not isinstance(case.operation.schema, BaseOpenAPISchema) or is_unexpected_http_status_case(case):
        return True

    # Only check for 404 (Not Found) responses - other 4XX are not resource availability issues
    # 422 / 400: Validation errors (bad request data)
    # 401 / 403: Auth issues (expired tokens, permissions)
    # 409: Conflict errors
    if response.status_code != 404:
        return None

    parent = ctx._find_parent(case_id=case.id)
    if parent is None:
        return None
    parent_response = ctx._find_response(case_id=parent.id)
    if parent_response is None:
        return None

    if not (
        parent.operation.method.upper() == "POST"
        and 200 <= parent_response.status_code < 400
        and _is_prefix_operation(
            ResourcePath(parent.path, parent.path_parameters or {}),
            ResourcePath(case.path, case.path_parameters or {}),
        )
    ):
        return None

    # Check if all parameters come from links
    overrides = case._override
    overrides_all_parameters = True
    for parameter in case.operation.iter_parameters():
        container = parameter.location.container_name
        if parameter.name not in getattr(overrides, container, {}):
            overrides_all_parameters = False
            break
    if not overrides_all_parameters:
        return None

    # Look for any successful DELETE operations on this resource
    for related_case in ctx._find_related(case_id=case.id):
        related_response = ctx._find_response(case_id=related_case.id)
        if (
            related_case.operation.method.upper() == "DELETE"
            and related_response is not None
            and 200 <= related_response.status_code < 300
            and _is_prefix_operation(
                ResourcePath(related_case.path, related_case.path_parameters or {}),
                ResourcePath(case.path, case.path_parameters or {}),
            )
        ):
            # Resource was properly deleted, 404 is expected
            return None

    # If we got here:
    # 1. Resource was created successfully
    # 2. Current operation returned 4XX
    # 3. All parameters come from links
    # 4. No successful DELETE operations found
    created_with = parent.operation.label
    not_available_with = case.operation.label
    reason = http.client.responses.get(response.status_code, "Unknown")
    raise EnsureResourceAvailability(
        operation=created_with,
        message=(
            f"The API returned `{response.status_code} {reason}` for a resource that was just created.\n\n"
            f"Created with      : `{created_with}`\n"
            f"Not available with: `{not_available_with}`"
        ),
        created_with=created_with,
        not_available_with=not_available_with,
    )


class AuthScenario(str, enum.Enum):
    NO_AUTH = "no_auth"
    INVALID_AUTH = "invalid_auth"
    GENERATED_AUTH = "generated_auth"


class AuthKind(str, enum.Enum):
    EXPLICIT = "explicit"
    GENERATED = "generated"


@schemathesis.check
def ignored_auth(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """Check if an operation declares authentication as a requirement but does not actually enforce it."""
    from schemathesis.specs.openapi.adapter.security import has_optional_auth
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema

    operation = case.operation
    if (
        not isinstance(operation.schema, BaseOpenAPISchema)
        or is_unexpected_http_status_case(case)
        or has_optional_auth(operation.schema.raw_schema, operation.definition.raw)
    ):
        return True
    security_parameters = _get_security_parameters(case.operation)
    # Authentication is required for this API operation and response is successful
    if security_parameters and 200 <= response.status_code < 300:
        auth = _contains_auth(ctx, case, response, security_parameters)
        if auth == AuthKind.EXPLICIT:
            # Auth is explicitly set, it is expected to be valid
            # Check if invalid auth will give an error
            no_auth_case = remove_auth(case, security_parameters)
            kwargs = ctx._transport_kwargs or {}
            kwargs.copy()
            for location, container_name in (
                ("header", "headers"),
                ("cookie", "cookies"),
                ("query", "query"),
            ):
                if container_name in kwargs:
                    container = kwargs[container_name].copy()
                    _remove_auth_from_container(container, security_parameters, location=location)
                    kwargs[container_name] = container
            kwargs.pop("session", None)
            if case.operation.app is not None:
                kwargs.setdefault("app", case.operation.app)
            ctx._record_case(parent_id=case.id, case=no_auth_case)
            no_auth_response = case.operation.schema.transport.send(no_auth_case, **kwargs)
            ctx._record_response(case_id=no_auth_case.id, response=no_auth_response)
            if no_auth_response.status_code != 401:
                _raise_no_auth_error(no_auth_response, no_auth_case, AuthScenario.NO_AUTH)
            # Try to set invalid auth and check if it succeeds
            for parameter in security_parameters:
                invalid_auth_case = remove_auth(case, security_parameters)
                _set_auth_for_case(invalid_auth_case, parameter)
                ctx._record_case(parent_id=case.id, case=invalid_auth_case)
                invalid_auth_response = case.operation.schema.transport.send(invalid_auth_case, **kwargs)
                ctx._record_response(case_id=invalid_auth_case.id, response=invalid_auth_response)
                if invalid_auth_response.status_code != 401:
                    _raise_no_auth_error(invalid_auth_response, invalid_auth_case, AuthScenario.INVALID_AUTH)
        elif auth == AuthKind.GENERATED:
            # If this auth is generated which means it is likely invalid, then
            # this request should have been an error
            _raise_no_auth_error(response, case, AuthScenario.GENERATED_AUTH)
        else:
            # Successful response when there is no auth
            _raise_no_auth_error(response, case, AuthScenario.NO_AUTH)
    return None


def _raise_no_auth_error(response: Response, case: Case, auth: AuthScenario) -> NoReturn:
    reason = http.client.responses.get(response.status_code, "Unknown")

    if auth == AuthScenario.NO_AUTH:
        title = "API accepts requests without authentication"
        detail = None
    elif auth == AuthScenario.INVALID_AUTH:
        title = "API accepts invalid authentication"
        detail = "invalid credentials provided"
    else:
        title = "API accepts invalid authentication"
        detail = "generated auth likely invalid"

    message = f"Expected 401, got `{response.status_code} {reason}` for `{case.operation.label}`"
    if detail is not None:
        message = f"{message} ({detail})"

    raise IgnoredAuth(
        operation=case.operation.label,
        message=message,
        title=title,
        case_id=case.id,
    )


def _get_security_parameters(operation: APIOperation) -> list[Mapping[str, Any]]:
    """Extract security definitions that are active for the given operation and convert them into parameters."""
    from schemathesis.specs.openapi.adapter.security import ORIGINAL_SECURITY_TYPE_KEY

    return [
        param
        for param in operation.security.iter_parameters()
        if param[ORIGINAL_SECURITY_TYPE_KEY] in ["apiKey", "basic", "http"]
    ]


def _contains_auth(
    ctx: CheckContext, case: Case, response: Response, security_parameters: list[Mapping[str, Any]]
) -> AuthKind | None:
    """Whether a request has authentication declared in the schema."""
    from requests.cookies import RequestsCookieJar

    # If auth comes from explicit `auth` option or a custom auth, it is always explicit
    if ctx._auth is not None or case._has_explicit_auth:
        return AuthKind.EXPLICIT
    request = response.request
    parsed = urlparse(request.url)
    query = parse_qs(parsed.query)  # type: ignore
    # Load the `Cookie` header separately, because it is possible that `request._cookies` and the header are out of sync
    header_cookies: SimpleCookie = SimpleCookie()
    raw_cookie = request.headers.get("Cookie")
    if raw_cookie is not None:
        header_cookies.load(raw_cookie)

    def has_header(p: Mapping[str, Any]) -> bool:
        return p["in"] == "header" and p["name"] in request.headers

    def has_query(p: Mapping[str, Any]) -> bool:
        return p["in"] == "query" and p["name"] in query

    def has_cookie(p: Mapping[str, Any]) -> bool:
        cookies = cast(RequestsCookieJar, request._cookies)  # type: ignore
        return p["in"] == "cookie" and (p["name"] in cookies or p["name"] in header_cookies)

    for parameter in security_parameters:
        name = parameter["name"]
        if has_header(parameter):
            if (
                # Explicit CLI headers
                (ctx._headers is not None and name in ctx._headers)
                # Other kinds of overrides
                or (ctx._override and name in ctx._override.headers)
                or (response._override and name in response._override.headers)
            ):
                return AuthKind.EXPLICIT
            return AuthKind.GENERATED
        if has_cookie(parameter):
            for headers in [
                ctx._headers,
                (ctx._override.headers if ctx._override else None),
                (response._override.headers if response._override else None),
            ]:
                if headers is not None and "Cookie" in headers:
                    jar = cast(RequestsCookieJar, headers["Cookie"])
                    if name in jar:
                        return AuthKind.EXPLICIT

            if (ctx._override and name in ctx._override.cookies) or (
                response._override and name in response._override.cookies
            ):
                return AuthKind.EXPLICIT
            return AuthKind.GENERATED
        if has_query(parameter):
            if (ctx._override and name in ctx._override.query) or (
                response._override and name in response._override.query
            ):
                return AuthKind.EXPLICIT
            return AuthKind.GENERATED

    return None


def remove_auth(case: Case, security_parameters: list[Mapping[str, Any]]) -> Case:
    """Remove security parameters from a generated case.

    It mutates `case` in place.
    """
    headers = case.headers.copy()
    query = case.query.copy()
    cookies = case.cookies.copy()
    for parameter in security_parameters:
        name = parameter["name"]
        if parameter["in"] == "header" and headers:
            headers.pop(name, None)
        if parameter["in"] == "query" and query:
            query.pop(name, None)
        if parameter["in"] == "cookie" and cookies:
            cookies.pop(name, None)
    return Case(
        operation=case.operation,
        method=case.method,
        path=case.path,
        path_parameters=case.path_parameters.copy(),
        headers=headers,
        cookies=cookies,
        query=query,
        body=case.body.copy() if isinstance(case.body, (list, dict)) else case.body,
        media_type=case.media_type,
        meta=case.meta,
    )


def _remove_auth_from_container(container: dict, security_parameters: list[Mapping[str, Any]], location: str) -> None:
    for parameter in security_parameters:
        name = parameter["name"]
        if parameter["in"] == location:
            container.pop(name, None)


def _set_auth_for_case(case: Case, parameter: Mapping[str, Any]) -> None:
    name = parameter["name"]
    for location, attr_name in (
        ("header", "headers"),
        ("query", "query"),
        ("cookie", "cookies"),
    ):
        if parameter["in"] == location:
            container = getattr(case, attr_name, {})
            # Could happen in the negative testing mode
            if not isinstance(container, dict):
                container = {}
            container[name] = "SCHEMATHESIS-INVALID-VALUE"
            setattr(case, attr_name, container)


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
