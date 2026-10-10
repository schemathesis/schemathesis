from __future__ import annotations

import enum
import http.client
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP
from functools import wraps
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING, Any, NoReturn, cast
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

import schemathesis
from schemathesis.checks import CheckContext, CheckFunction
from schemathesis.core import NOT_SET, media_types, string_to_boolean
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.failures import AcceptedNegativeData, Failure
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, get_type, make_validator
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.mutations import Mutation, OperatorKind, render_mutations
from schemathesis.core.parameters import ParameterLocation, plain_str_values
from schemathesis.core.transport import HTTP_METHODS_SCHEMA, Response, expand_status_code, expand_status_codes
from schemathesis.generation.case import Case
from schemathesis.generation.meta import (
    REQUEST_SHAPE_PROBES,
    CoveragePhaseData,
    CoverageScenario,
    FuzzingPhaseData,
    coverage_scenario,
)
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
    AuthScenario,
    EnsureResourceAvailability,
    IgnoredAuth,
    JsonSchemaError,
    MalformedMediaType,
    MissingContentType,
    MissingHeaderNotRejected,
    MissingHeaders,
    ObjectLevelAuthorizationViolation,
    RejectedPositiveData,
    UndefinedContentType,
    UndefinedStatusCode,
    UnsupportedMethodResponse,
    UseAfterFree,
)
from schemathesis.specs.openapi._auth_retry import (
    build_retry_transport_kwargs,
    get_security_parameters,
    remove_auth,
    set_auth_for_case,
)
from schemathesis.specs.openapi.utils import (
    coerce_wire_string,
    numeric_wire_value_is_valid,
    parameter_types,
    reads_as_null,
    sent_text_is_valid,
)
from schemathesis.transport.prepare import prepare_path
from schemathesis.transport.serialization import contains_binary

if TYPE_CHECKING:
    import jsonschema_rs

    from schemathesis.engine.recorder import RecordedScenario
    from schemathesis.generation.meta import CaseMetadata
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter, OpenApiParameterSet
    from schemathesis.specs.openapi.schemas import OpenApiSchema


def is_unexpected_http_status_case(case: Case) -> bool:
    # Skip checks for request-shape probes whose response conformance is irrelevant.
    return coverage_scenario(case) in REQUEST_SHAPE_PROBES


def requires_openapi_schema(func: CheckFunction) -> CheckFunction:
    """Skip the check if the operation does not belong to an OpenAPI schema."""

    @wraps(func)
    def wrapper(ctx: CheckContext, response: Response, case: Case) -> bool | None:
        from schemathesis.specs.openapi.schemas import OpenApiSchema

        if not isinstance(case.operation.schema, OpenApiSchema):
            return True
        return func(ctx, response, case)

    return wrapper


def skips_on_unexpected_http_status(func: CheckFunction) -> CheckFunction:
    """Skip the check when a coverage request-shape probe targets the server."""

    @wraps(func)
    def wrapper(ctx: CheckContext, response: Response, case: Case) -> bool | None:
        if is_unexpected_http_status_case(case):
            return True
        return func(ctx, response, case)

    return wrapper


def requires_case_meta(func: CheckFunction) -> CheckFunction:
    """Skip the check when `case.meta` is not available."""

    @wraps(func)
    def wrapper(ctx: CheckContext, response: Response, case: Case) -> bool | None:
        if case.meta is None:
            return True
        return func(ctx, response, case)

    return wrapper


def _get_openapi_schema(case: Case) -> OpenApiSchema:
    return cast("OpenApiSchema", case.operation.schema)


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
def status_code_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
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
@requires_openapi_schema
@skips_on_unexpected_http_status
def content_type_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    schema = _get_openapi_schema(case)
    documented_content_types = schema.get_content_types(case.operation, response)
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
        if media_types.matches_parts((expected_main, expected_sub), (received_main, received_sub)):
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
@requires_openapi_schema
@skips_on_unexpected_http_status
def response_headers_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    from schemathesis.specs.openapi.validation import _maybe_raise_one_or_more

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
        if values is None:
            if header.is_required:
                missing_headers.append(name)
            continue
        # A header whose schema names a missing component has nothing to validate against.
        if header.unresolvable_reference is not None:
            continue
        coerced = _coerce_header_value(values[0], header.schema)
        for exc in header.validator.iter_errors(coerced):
            errors.append(
                JsonSchemaError.from_exception(
                    title="Response header does not conform to the schema",
                    operation=case.operation.label,
                    exc=exc,
                    root_schema=header.schema,
                    config=case.operation.schema.config.output,
                    name_to_uri=header.name_to_uri,
                )
            )

    if missing_headers:
        formatted_headers = [f"\n- `{header}`" for header in missing_headers]
        message = f"The following required headers are missing from the response:{''.join(formatted_headers)}"
        errors.append(MissingHeaders(operation=case.operation.label, message=message, missing_headers=missing_headers))

    return _maybe_raise_one_or_more(errors)  # type: ignore[func-returns-value]


_COLLECTION_FORMAT_DELIMITERS = {
    "csv": ",",
    "ssv": " ",
    "tsv": "\t",
    "pipes": "|",
}


def _coerce_header_value(value: str, schema: dict[str, Any]) -> Any:
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
    if schema_type == "array":
        # Swagger 2.0: array headers use `collectionFormat` (default `csv`) to define
        # how items are joined into a single header value. Split the wire form into
        # items, then coerce each one against `items` so non-string element types validate.
        collection_format = schema.get("collectionFormat", "csv")
        delimiter = _COLLECTION_FORMAT_DELIMITERS.get(collection_format)
        if delimiter is None:
            return value
        items_schema = schema.get("items") or {}
        return [_coerce_header_value(item, items_schema) for item in value.split(delimiter)]
    return value


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
def response_schema_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    return case.operation.validate_response(response, case=case)


def _is_stringifying_media_type(media_type: str) -> bool:
    """Check if media type serializes all values to strings.

    Media types like text/plain and application/octet-stream convert any value
    to a string representation during serialization (via str(value)).
    This means negative-generated non-string values become valid strings after serialization.
    """
    return media_types.is_plain_text(media_type) or media_type == "application/octet-stream"


def _declared_parameters_are_valid(case: Case, location: ParameterLocation) -> bool:
    """Whether the only difference from a valid request at `location` is undeclared extras, which servers ignore."""
    # Every non-body container defaults to an empty mapping, and the caller never passes `BODY`.
    value = case.get_container(location)
    assert isinstance(value, Mapping)
    container = getattr(case.operation, location.container_name)
    schema = container.schema
    if _has_serialization_sensitive_types(schema, container):
        # Arrays and objects are rewritten on the wire, so post-serialization validity is unknowable.
        return False
    declared = {name: item for name, item in value.items() if name in container}
    try:
        return make_validator(schema, case.operation.schema.adapter.jsonschema_validator_cls).is_valid(declared)
    except Exception:
        # Schema the validator cannot read (e.g. a pattern valid in Python but not ECMA 262).
        return False


def _has_other_negated_location(case: Case, *locations: ParameterLocation) -> bool:
    """Whether a location outside `locations` carries a negation the server could act on."""
    meta = case.meta
    assert meta is not None
    for other in (
        ParameterLocation.PATH,
        ParameterLocation.QUERY,
        ParameterLocation.HEADER,
        ParameterLocation.COOKIE,
        ParameterLocation.BODY,
    ):
        if other in locations:
            continue
        component = meta.components.get(other)
        if component is None or not component.mode.is_negative:
            continue
        if other != ParameterLocation.BODY and _declared_parameters_are_valid(case, other):
            continue
        return True
    return False


def _body_negation_becomes_valid_after_serialization(response: Response, case: Case) -> bool:
    """Check if body negation becomes valid after serialization.

    For media types like text/plain, any value gets stringified during serialization,
    making it valid for string schemas. Skip negative_data_rejection ONLY if:
    1. The body is the only negative component
    2. AND the media type is stringifying

    If there are other negative components (query, headers, etc.), we should still
    check them even if the body negation is neutralized by serialization.
    """
    meta = case.meta
    if meta is None:
        return False

    body_meta = meta.components.get(ParameterLocation.BODY)
    if body_meta is None or not body_meta.mode.is_negative:
        return False

    media_type = case.media_type
    if media_type is None:
        return False

    if not _is_stringifying_media_type(media_type) and not _form_body_is_valid_as_sent(response, case, media_type):
        return False

    # Only the body is negative and it becomes valid once serialized
    return not _has_other_negated_location(case, ParameterLocation.BODY)


def _form_body_is_valid_as_sent(response: Response, case: Case, media_type: str) -> bool:
    """Whether a form body satisfies its schema as the server reads the fields actually sent."""
    body = case.body
    if not isinstance(body, dict):
        return False
    fields = _sent_form_fields(response, media_type)
    if fields is None:
        return False
    alternative = next((item for item in case.operation.body if item.media_type == media_type), None)
    if alternative is None:
        return False
    # Urlencoded names round-trip exactly, while multipart escapes some characters in part names.
    names_are_exact = media_types.is_form_urlencoded(media_type)
    try:
        validator = make_validator(
            alternative.optimized_schema, _get_openapi_schema(case).adapter.jsonschema_validator_cls
        )
        # A field missing from the wire was never received; empty lists and objects send nothing at all.
        sent: dict[str, object] = {
            name: value
            for name, value in body.items()
            if name not in fields and not names_are_exact and value not in ([], {})
        }
        for name, texts in fields.items():
            if None in texts:
                # Typed or file parts are not plain text, so they keep the generated value.
                if name in body:
                    sent[name] = body[name]
                continue
            readings = [reading for text in texts for reading in _form_text_readings(cast(str, text))]
            # Servers read one of the repeated values and may parse it into the declared type.
            sent[name] = next(
                (reading for reading in readings if not _has_errors_at(validator, {**sent, name: reading}, name)),
                readings[0],
            )
        return validator.is_valid(sent)
    except Exception:
        # Schemas the validator cannot read - can't tell whether the sent body is valid
        return False


def _form_text_readings(text: str) -> list[object]:
    """The ways a server may read a form field: as text, or parsed as JSON into a number, boolean, or null."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return [text]
    return [text, parsed]


def _has_errors_at(validator: jsonschema_rs.Validator, instance: dict[str, object], name: str) -> bool:
    return any(error.instance_path[:1] == [name] for error in validator.iter_errors(instance))


def _sent_form_fields(response: Response, media_type: str) -> Mapping[str, Sequence[str | None]] | None:
    """Field values in the sent form body; `None` marks a value not sent as plain text."""
    content = response.request.body
    if isinstance(content, str):
        content = content.encode()
    if not isinstance(content, bytes):
        return None
    if media_types.is_form_urlencoded(media_type):
        return parse_qs(content.decode("ascii", errors="replace"), keep_blank_values=True)
    if media_types.parse(media_type) != ("multipart", "form-data"):
        return None
    content_type = response.request.headers.get("Content-Type", "")
    message = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + content)
    fields: dict[str, list[str | None]] = {}
    for part in message.iter_parts():
        # The encoder names every part.
        name = cast(str, part.get_param("name", header="content-disposition"))
        text = None
        payload = part.get_payload(decode=True)
        # Parts with a filename or their own content type are files or typed values, not plain text.
        if isinstance(payload, bytes) and part.get_filename() is None and "content-type" not in part:
            # Servers decode form text leniently, replacing bytes that are not valid UTF-8.
            text = payload.decode(errors="replace")
        fields.setdefault(name, []).append(text)
    return fields


def _body_negation_is_only_forbidden_property(case: Case) -> bool:
    """Check if the body violates nothing but properties the request schema forbids outright.

    Read-only properties are rewritten to a schema nothing satisfies. The spec lets the owning
    authority ignore such input instead of rejecting it, so accepting it is not a failure.
    """
    meta = case.meta
    assert meta is not None

    body_meta = meta.components.get(ParameterLocation.BODY)
    if body_meta is None or not body_meta.mode.is_negative:
        return False

    # Another negative component carries its own expectation, so the check still applies.
    if _has_other_negated_location(case, ParameterLocation.BODY):
        return False

    if case.body is NOT_SET:
        return False

    validator_cls = _get_openapi_schema(case).adapter.jsonschema_validator_cls
    for alternative in case.operation.body:
        if alternative.media_type != case.media_type:
            continue
        schema = alternative.optimized_schema
        permissive = alternative.permissive_schema
        # No property was forbidden, so validating against the permissive schema would repeat the check below.
        if permissive is schema:
            return False
        try:
            if make_validator(schema, validator_cls).is_valid(case.body):
                return False
            return make_validator(permissive, validator_cls).is_valid(case.body)
        except Exception:
            # Schemas or values the validator cannot read — can't tell what was negated
            return False
    return False


def _single_element_array_becomes_valid_after_serialization(response: Response, case: Case) -> bool:
    """Check if an array value for a scalar query parameter becomes valid after serialization.

    Query arrays are serialized as repeated keys:
    - Single-element [67] -> "?page=67" (identical to scalar 67)
    - Multi-element [True, 1] -> "?page_size=True&page_size=1"

    For single-element arrays, the serialized form is indistinguishable from a scalar,
    so the server accepts it only when that element is valid for the original schema.

    For multi-element arrays, some frameworks pick one value from repeated keys (e.g.
    the last one). If any element in the array is valid for the original scalar schema,
    the server may accept the request, making it an unreliable negative test.

    Query values are also judged by the text actually sent, as nested lists and objects
    serialize into repeated keys or their string representation.
    """
    from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter

    meta = case.meta
    if meta is None:
        return False

    location = ParameterLocation.QUERY
    component = meta.components.get(location)
    if component is None or not component.mode.is_negative:
        return False
    query: object = case.query
    if not isinstance(query, Mapping):
        return False

    neutralized: set[tuple[ParameterLocation, str]] = set()
    sent_query = _sent_query_values(response, case)

    for param_name, param_value in query.items():
        param = case.operation.query.get(param_name)
        if param is None:
            # This is an additional property, not a schema-defined parameter
            continue
        assert isinstance(param, OpenApiParameter)

        # An optional parameter that was not sent at all can't make the request invalid.
        if sent_query is not None and param_name not in sent_query and not param.is_required:
            neutralized.add((location, param_name))
            continue

        elements = param_value if isinstance(param_value, list) else []
        sent_texts = sent_query.get(param_name, []) if sent_query is not None else []
        if not elements and not sent_texts:
            continue

        schema = param.definition.get("schema", {})
        expected_types = get_type(schema)

        if _declares_type(schema, "array"):
            continue

        # A single element serializes identically to a scalar; multiple elements become repeated keys and
        # some frameworks pick one of them. Either way, the request is valid if any element is.
        try:
            validator = make_validator(param.validation_schema, param.adapter.jsonschema_validator_cls)
        except Exception:
            neutralized.add((location, param_name))
            continue
        for element in elements:
            if validator.is_valid(element):
                neutralized.add((location, param_name))
                break
            # Query values are transmitted as strings, so a string element like "44" produces the same wire
            # form as int 44. Frameworks that coerce the raw query value to integer/number/boolean will accept it.
            if isinstance(element, str):
                coerced = coerce_wire_string(element, expected_types)
                if coerced is not None and numeric_wire_value_is_valid(coerced, validator.is_valid):
                    neutralized.add((location, param_name))
                    break
        # Nested lists and objects reach the server as repeated keys or their string representation.
        if any(sent_text_is_valid(text, validator.is_valid, expected_types) for text in sent_texts):
            neutralized.add((location, param_name))

    if not neutralized:
        return False
    mutations = meta.phase.data.mutations
    if not mutations:
        # Without mutation records, any negative component outside the query still makes the request invalid.
        return all(
            component_location == location or not component.mode.is_negative
            for component_location, component in meta.components.items()
        )
    # Any other mutated parameter or location still makes the request invalid.
    return all((mutation.parameter_location, mutation.parameter) in neutralized for mutation in mutations)


def _wire_value_matches_parameter(parameter: OpenApiParameter, expected_types: list[str], wire_value: str) -> bool:
    """Check whether the text actually sent for `parameter` satisfies its original schema."""
    coerced = coerce_wire_string(wire_value, expected_types)
    try:
        validator = make_validator(parameter.validation_schema, parameter.adapter.jsonschema_validator_cls)
    except Exception:
        # Schema rejected by jsonschema_rs - validity is unknown, so don't report a failure.
        return True
    if coerced is not None:
        return numeric_wire_value_is_valid(coerced, validator.is_valid)
    return validator.is_valid(wire_value)


def _type_mutations_become_valid_after_serialization(case: Case) -> bool:
    """Check if every type mutation of a numeric path/query parameter becomes valid after serialization.

    Both path and query parameters are transmitted as strings on the wire, so a negative
    type mutation for an integer/number parameter can still be accepted when the serialized
    value is parseable as that numeric type.

    - String mutations: the value is a string (e.g. "5") -> directly parseable.
    - Object mutations for query: urlencode(doseq=True) iterates over dict keys, so a dict
      like {"5": "x"} produces ?param=5, which is integer-parseable.
    - Path parameters are additionally URL-decoded by servers (e.g. `%2B1` -> `+1`).
    """
    meta = case.meta
    if meta is None:
        return False

    phase_data = meta.phase.data
    if not isinstance(phase_data, FuzzingPhaseData) or not phase_data.mutations:
        return False

    locations = set()
    for mutation in phase_data.mutations:
        location = mutation.parameter_location
        component = meta.components.get(location) if location is not None else None
        if (
            mutation.operator != OperatorKind.CHANGE_TYPE
            or location not in (ParameterLocation.PATH, ParameterLocation.QUERY)
            or component is None
            or not component.mode.is_negative
            or mutation.parameter is None
            or not _type_mutation_is_valid_on_wire(case, location, mutation.parameter)
        ):
            return False
        locations.add(location)

    # If there are other negative components, we should still validate them.
    return not _has_other_negated_location(case, *locations)


def _type_mutation_is_valid_on_wire(case: Case, location: ParameterLocation, name: str) -> bool:
    from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter

    case_container = case.get_container(location)
    parameter = getattr(case.operation, location.container_name).get(name)
    if not isinstance(case_container, Mapping) or name not in case_container:
        return False
    if not isinstance(parameter, OpenApiParameter):
        return False

    value = case_container[name]
    expected_types = get_type(parameter.definition.get("schema", {}))
    if isinstance(value, (str, int, float)):
        # `str` subclasses like already-encoded path values are rejected by the validator.
        wire_value = str(value)
        # Path parameters are URL-encoded; decode before parsing.
        if location == ParameterLocation.PATH:
            wire_value = unquote(wire_value)
        return _wire_value_matches_parameter(parameter, expected_types, wire_value)
    if location == ParameterLocation.QUERY and isinstance(value, dict):
        # urlencode(doseq=True) iterates over dict keys, producing one query value per key.
        # e.g. {"5": "x"} becomes ?page_size=5, which the server sees as a valid integer.
        return any(_wire_value_matches_parameter(parameter, expected_types, str(key)) for key in value)
    return False


def _path_array_becomes_valid_after_serialization(case: Case) -> bool:
    """Check if a negative array path parameter reaches the server as a valid value.

    Array path parameters serialize comma-joined (`simple` style). A negative value, whether a
    string-typed example like `"hello,world"` or a fuzzing type mutation, whose comma-split form
    validates against the original array schema is wire-identical to a valid array, so the server
    may accept it.
    """
    from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter

    meta = case.meta
    if meta is None:
        return False

    component = meta.components.get(ParameterLocation.PATH)
    if component is None or not component.mode.is_negative:
        return False

    # If other locations are also negative, the acceptance can't be attributed to the path round-trip.
    if _has_other_negated_location(case, ParameterLocation.PATH):
        return False

    container = case.path_parameters or {}
    operation_container = case.operation.path_parameters
    for param_name, value in container.items():
        if param_name not in operation_container or not isinstance(value, str):
            continue
        parameter = operation_container.get(param_name)
        if not isinstance(parameter, OpenApiParameter):
            continue
        schema = parameter.definition.get("schema", {})
        if "array" not in get_type(schema) or parameter.definition.get("style", "simple") != "simple":
            continue
        try:
            validator = make_validator(schema, parameter.adapter.jsonschema_validator_cls)
        except Exception:
            return True
        # `unquote` keeps `str` subclasses intact, and splitting an empty string keeps the input
        # object; the validator rejects anything but a plain `str`.
        items = unquote(str(value)).split(",")
        if validator.is_valid(items):
            return True
        # Items arrive as text, so `18` is the wire form of `[18]` for an integer array.
        item_types = get_type(schema.get("items", {}))
        coerced = [coerce_wire_string(item, item_types) for item in items]
        numbers = [value for value in coerced if value is not None]
        if len(numbers) == len(coerced) and numeric_wire_value_is_valid(numbers, validator.is_valid):
            return True

    return False


def _declares_type(schema: JsonSchema, name: str) -> bool:
    declared = schema.get("type") if isinstance(schema, dict) else None
    return declared == name or isinstance(declared, list) and name in declared


def _url_sent_to_operation(response: Response, case: Case) -> ParseResult | None:
    """The URL the response came from, or `None` if it is not this case's operation, e.g. after a redirect."""
    request_url = urlparse(response.request.url)
    try:
        expected_path = _get_openapi_schema(case).get_full_path(prepare_path(case.path, case.path_parameters))
    except InvalidSchema:
        # Path parameters are missing, so the request could not have been sent to this operation.
        return None
    if request_url.path != expected_path:
        return None
    return request_url


def _sent_query_values(response: Response, case: Case) -> dict[str, list[str]] | None:
    """Parse the query actually sent, or `None` if the request was not sent to this case's operation."""
    request_url = _url_sent_to_operation(response, case)
    if request_url is None:
        return None
    return parse_qs(request_url.query, keep_blank_values=True)


def _query_as_sent(
    response: Response, case: Case, query: Mapping[str, object], properties: dict[str, JsonSchema]
) -> dict[str, object] | None:
    """Replace generated query values with the text actually sent wherever the server reads that text as is."""
    sent_values = _sent_query_values(response, case)
    if sent_values is None:
        return None
    sent: dict[str, object] = {}
    # Iterating the generated query skips keys added outside of generation, e.g. query-based auth.
    for name, value in query.items():
        # Values like `[]` or `None` are not sent at all.
        if name not in sent_values:
            continue
        texts = sent_values[name]
        # Other types keep their generated value, as the server parses the text back into it.
        schema = properties.get(name, {})
        if texts == [""] or _declares_type(schema, "string"):
            sent[name] = texts[0] if len(texts) == 1 else texts
        elif len(texts) == 1 and _declares_type(schema, "boolean"):
            sent[name] = string_to_boolean(texts[0])
        else:
            sent[name] = value
    return sent


def _non_body_negative_values_match_schema(response: Response, case: Case) -> bool:
    """Check if all negative non-body parameter values are still valid against their original schema."""
    from schemathesis.specs.openapi.schemas import OpenApiSchema

    meta = case.meta
    if meta is None or not isinstance(case.operation.schema, OpenApiSchema):
        return False

    # If body is also negative, this guard does not apply.
    body_component = meta.components.get(ParameterLocation.BODY)
    if body_component is not None and body_component.mode.is_negative:
        return False

    has_negative = False

    for location in (
        ParameterLocation.PATH,
        ParameterLocation.QUERY,
        ParameterLocation.HEADER,
        ParameterLocation.COOKIE,
    ):
        component = meta.components.get(location)
        if component is None or not component.mode.is_negative:
            continue

        value = case.get_container(location)
        if not isinstance(value, Mapping):
            continue

        has_negative = True
        container = getattr(case.operation, location.container_name)
        if not container:
            continue
        if location == ParameterLocation.QUERY:
            sent = _query_as_sent(response, case, value, container.schema.get("properties", {}))
            if sent is not None:
                value = sent
        v = dict(value) if location == ParameterLocation.HEADER else value
        if isinstance(v, dict):
            v = plain_str_values(v)
        try:
            if not container.get_strict_validator().is_valid(v):
                # At least one parameter is invalid
                return False
        except Exception:
            # Schema rejected by jsonschema_rs (e.g. `{,3}` as an incomplete quantifier)
            # — can't determine validity, so skip this location
            continue

    return has_negative


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
@requires_case_meta
def negative_data_rejection(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    meta = case.meta
    assert meta is not None

    config = ctx.config.negative_data_rejection
    allowed_statuses = expand_status_codes(config.expected_statuses or [])

    if (
        meta.generation.mode.is_negative
        and response.status_code not in allowed_statuses
        and not has_only_additional_properties_in_non_body_parameters(case)
        and not _body_negation_becomes_valid_after_serialization(response, case)
        and not _body_negation_is_only_forbidden_property(case)
        and not _single_element_array_becomes_valid_after_serialization(response, case)
        and not _type_mutations_become_valid_after_serialization(case)
        and not _path_array_becomes_valid_after_serialization(case)
        and not _non_body_negative_values_match_schema(response, case)
    ):
        extra_info = ""
        phase = meta.phase
        if phase.data.description:
            parts: list[str] = []
            # Special case: CoveragePhaseData descriptions for "Missing" scenarios are already complete
            if isinstance(phase.data, CoveragePhaseData) and phase.data.scenario in (
                CoverageScenario.MISSING_PARAMETER,
                CoverageScenario.OBJECT_MISSING_REQUIRED_PROPERTY,
            ):
                extra_info = f"\nInvalid component: {phase.data.description}"
            else:
                # Build structured message: parameter `name` in location - description
                # For body, don't show parameter name (it's the media type, not useful)
                location = phase.data.parameter_location
                if phase.data.parameter:
                    names = [phase.data.parameter]
                elif location is None:
                    # Each rendered line names its own location, so a flat list of names would mislead.
                    names = []
                else:
                    names = list(dict.fromkeys(m.parameter for m in phase.data.mutations if m.parameter))
                if location != ParameterLocation.BODY and names:
                    label = "parameter" if len(names) == 1 else "parameters"
                    parts.append(f"{label} " + ", ".join(f"`{name}`" for name in names))
                if location:
                    parts.append(f"in {location.name.lower()}")
                if len(phase.data.mutations) > 1:
                    # Render each mutation on its own indented bullet under the header.
                    header = " ".join(parts)
                    body = "\n".join(f"  - {line}" for line in render_mutations(phase.data.mutations))
                    extra_info = f"\nInvalid component: {header}\n{body}" if header else f"\nInvalid component:\n{body}"
                else:
                    # Lowercase first letter of description for consistency
                    description = phase.data.description[0].lower() + phase.data.description[1:]
                    parts.append(f"- {description}" if parts else description)
                    extra_info = "\nInvalid component: " + " ".join(parts)
        raise AcceptedNegativeData(
            operation=case.operation.label,
            message=f"Invalid data should have been rejected\nExpected: {', '.join(config.expected_statuses)}{extra_info}",
            status_code=response.status_code,
            expected_statuses=config.expected_statuses,
        )
    return None


def _collect_declared_properties(
    schema: JsonSchema, bundle: dict[str, JsonSchema], visited: set[str]
) -> tuple[set[str], bool]:
    """Property names declared anywhere in the schema, plus whether extras are already forbidden."""
    if not isinstance(schema, dict):
        return set(), False
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in visited:
            return set(), False
        target = bundle.get(reference.rsplit("/", 1)[-1])
        if target is None:
            return set(), False
        return _collect_declared_properties(target, bundle, visited | {reference})
    properties = schema.get("properties")
    declared = set(properties) if isinstance(properties, dict) else set()
    forbids_extras = schema.get("additionalProperties") is False
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                branch_declared, branch_forbids = _collect_declared_properties(branch, bundle, visited)
                declared |= branch_declared
                forbids_extras = forbids_extras or branch_forbids
    return declared, forbids_extras


def _blamed_body_properties(case: Case, response: Response) -> set[str]:
    """Top-level body property names the server's own error message pinned the rejection on."""
    from schemathesis.core.error_feedback.collector import parse_observations

    names: set[str] = set()
    for observation in parse_observations(operation=case.operation, case=case, response=response):
        path = observation.parameter_path
        if observation.location is ParameterLocation.BODY and path and isinstance(path[0], str):
            names.add(path[0])
    return names


def _contains_nul(value: object) -> bool:
    """Whether any string key or value in a JSON-shaped value holds a NUL character."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _additional_properties_hint(case: Case, response: Response) -> str | None:
    """Return a hint if extra body properties are the likely cause of server rejection."""
    if not isinstance(case.body, dict):
        return None

    from schemathesis.specs.openapi.schemas import OpenApiSchema

    if not isinstance(case.operation.schema, OpenApiSchema):
        return None

    validator_cls = case.operation.schema.adapter.jsonschema_validator_cls

    for alternative in case.operation.body:
        if alternative.media_type != case.media_type:
            continue
        raw = alternative.raw_schema
        if not isinstance(raw, dict):
            return None
        declared, forbids_extras = _collect_declared_properties(raw, raw.get(BUNDLE_STORAGE_KEY) or {}, set())
        if forbids_extras:
            return None

        extra = set(case.body.keys()) - declared
        if not extra:
            return None
        # Many servers reject any string with a NUL character, so the extras are not the only plausible cause.
        if _contains_nul(case.body):
            return None

        stripped = {k: v for k, v in case.body.items() if k not in extra}
        # `format: binary` fields hold raw bytes the JSON Schema validator cannot accept.
        if contains_binary(stripped):
            return None
        if not make_validator(alternative.optimized_schema, validator_cls).is_valid(stripped):
            return None

        # The server named a declared field and none of the extras, so the extras did not cause the rejection.
        blamed = _blamed_body_properties(case, response)
        if blamed & declared and not blamed & extra:
            return None

        count = len(extra)
        examples = ", ".join(f"`{k}`" for k in sorted(extra)[:3])
        if count > 3:
            examples += f" and {count - 3} more"
        noun = "property" if count == 1 else "properties"
        return (
            f"\nHint: The request body contains {count} additional {noun} not defined in the schema "
            f"({examples}). The server appears to reject properties the schema allows. "
            "Declare `additionalProperties: false` if extras are not accepted, or make the server ignore unknown fields."
        )
    return None


# Statuses a credential-granting operation may answer to a well-formed request carrying credentials that do not exist.
CREDENTIAL_REJECTION_STATUSES = frozenset({400, 422})


def _token_urls(operation: APIOperation) -> Iterator[str]:
    for definition in operation.schema.security.security_definitions.values():
        # Swagger 2.0 puts `tokenUrl` on the scheme; Open API 3 nests it under each flow.
        token_url = definition.get("tokenUrl")
        if isinstance(token_url, str):
            yield token_url
        flows = definition.get("flows")
        if isinstance(flows, Mapping):
            for flow in flows.values():
                token_url = flow.get("tokenUrl") if isinstance(flow, Mapping) else None
                if isinstance(token_url, str):
                    yield token_url
    for scheme in operation.schema.config.auth.dynamic.schemes.values():
        yield scheme.path


def _grants_credentials(operation: APIOperation) -> bool:
    """Whether this operation mints credentials, per the schema's own `tokenUrl` or a configured dynamic-auth path."""
    for token_url in _token_urls(operation):
        path = urlparse(token_url).path
        # An empty token URL yields no path, which would otherwise normalize to "/" and claim a root operation.
        if not path:
            continue
        if not path.startswith("/"):
            path = f"/{path}"
        # `operation.path` carries no `basePath`, so a prefixed token URL matches on a segment boundary.
        if path == operation.path or path.endswith(f"/{operation.path.lstrip('/')}"):
            return True
    return False


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
@requires_case_meta
def positive_data_acceptance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    meta = case.meta
    assert meta is not None

    config = ctx.config.positive_data_acceptance
    allowed_statuses = expand_status_codes(config.expected_statuses or [])

    if meta.generation.mode.is_positive and response.status_code not in allowed_statuses:
        # A schema promises which requests are well formed, not which credentials exist.
        if response.status_code in CREDENTIAL_REJECTION_STATUSES and _grants_credentials(case.operation):
            return None
        message = f"Valid data should have been accepted\nExpected: {', '.join(config.expected_statuses)}"
        hint = _additional_properties_hint(case, response)
        if hint:
            message += hint
        raise RejectedPositiveData(
            operation=case.operation.label,
            message=message,
            status_code=response.status_code,
            allowed_statuses=config.expected_statuses,
        )
    return None


@schemathesis.check
@requires_openapi_schema
def missing_required_header(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    meta = case.meta
    if meta is None:
        return None
    data = meta.phase.data
    if not isinstance(data, CoveragePhaseData) or is_unexpected_http_status_case(case):
        return None
    if (
        data.parameter
        and data.parameter_location == ParameterLocation.HEADER
        and data.scenario == CoverageScenario.MISSING_PARAMETER
    ):
        if data.parameter.lower() == "authorization":
            expected_statuses = {401}
        else:
            config = ctx.config.missing_required_header
            expected_statuses = expand_status_codes(config.expected_statuses or [])
        if response.status_code not in expected_statuses:
            # A drawn identifier rarely exists, so the server can reject the request before reading headers.
            if response.status_code == 404 and _targets_generated_resource(ctx, case.operation):
                return None
            allowed = ", ".join(map(str, expected_statuses))
            raise MissingHeaderNotRejected(
                operation=f"{case.method} {case.path}",
                header_name=data.parameter,
                status_code=response.status_code,
                expected_statuses=list(expected_statuses),
                message=f"Got {response.status_code} when missing required '{data.parameter}' header, expected {allowed}",
            )
    return None


# Statuses that mean the server rejected the request at the authentication layer.
AUTH_REJECTION_STATUSES = frozenset({401, 403})


def _requires_authentication(operation: APIOperation) -> bool:
    from schemathesis.specs.openapi.adapter.security import get_effective_security_scheme_names

    return bool(get_effective_security_scheme_names(operation, operation.schema.raw_schema))


def _targets_generated_resource(ctx: CheckContext, operation: APIOperation) -> bool:
    """Whether the resource this request targets was drawn rather than supplied by the user."""
    if "{" not in operation.path:
        return False
    pinned = ctx._override.path_parameters if ctx._override is not None else {}
    return any(
        parameter.name not in pinned
        for parameter in operation.iter_parameters()
        if parameter.location == ParameterLocation.PATH
    )


@schemathesis.check
@requires_openapi_schema
@requires_case_meta
def unsupported_method(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    meta = case.meta
    assert meta is not None
    if not isinstance(meta.phase.data, CoveragePhaseData) or response.request.method == "OPTIONS":
        return None
    data = meta.phase.data
    if data.scenario == CoverageScenario.UNSPECIFIED_HTTP_METHOD:
        if response.status_code != 405:
            # Generated path parameters rarely point at an existing resource, and routing 404s before
            # method dispatch. 405 is only guaranteed when the target resource exists.
            if response.status_code == 404 and _targets_generated_resource(ctx, case.operation):
                return None
            # Most frameworks authenticate before method dispatch, so a protected operation rejects an
            # undeclared method with 401/403 without ever reaching routing.
            if response.status_code in AUTH_REJECTION_STATUSES and _requires_authentication(case.operation):
                return None
            # Rate limiters usually run as middleware ahead of routing and throttle every method alike.
            if response.status_code == 429:
                return None
            raise UnsupportedMethodResponse(
                operation=case.operation.label,
                method=cast(str, response.request.method),
                status_code=response.status_code,
                failure_reason="wrong_status",
                message=f"Unsupported method {response.request.method} returned {response.status_code}, expected 405 Method Not Allowed\n\nReturn 405 for methods not listed in the OpenAPI spec",
            )

        allow_header = response.headers.get("allow")
        if not allow_header:
            raise UnsupportedMethodResponse(
                operation=case.operation.label,
                method=cast(str, response.request.method),
                status_code=response.status_code,
                allow_header_present=False,
                failure_reason="missing_allow_header",
                message=f"{response.request.method} returned 405 without required `Allow` header\n\nAdd `Allow` header listing supported methods (required by RFC 9110)",
            )
    return None


# `HEAD` and `OPTIONS` are commonly handled by the HTTP framework rather than declared in the schema.
IMPLICIT_METHODS = frozenset({"head", "options"})


@schemathesis.check
@requires_openapi_schema
def allow_header_conformance(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    if response.request.method != "OPTIONS":
        return None
    values = response.headers.get("allow")
    if not values:
        return None
    allow_header = ", ".join(values)
    advertised = {method.strip().lower() for method in allow_header.split(",") if method.strip()}
    if not advertised:
        return None
    declared = {method.lower() for method in case.operation.schema[case.operation.path]}
    declared &= HTTP_METHODS_SCHEMA
    missing = sorted(declared - advertised - IMPLICIT_METHODS)
    undocumented = sorted(advertised - declared - IMPLICIT_METHODS)
    if not missing and not undocumented:
        return None
    parts = []
    if missing:
        parts.append(f"missing documented methods: {', '.join(method.upper() for method in missing)}")
    if undocumented:
        parts.append(f"undocumented methods advertised: {', '.join(method.upper() for method in undocumented)}")
    raise AllowHeaderMismatch(
        operation=case.operation.label,
        allow_header=allow_header,
        missing_methods=[method.upper() for method in missing],
        undocumented_methods=[method.upper() for method in undocumented],
        message=f"`Allow` header does not match the schema — {'; '.join(parts)}\n\nList exactly the methods this resource supports in `Allow`",
    )


def has_only_additional_properties_in_non_body_parameters(case: Case) -> bool:
    # Check if the case contains only additional properties in query, headers, or cookies.
    # This function is used to determine if negation is solely in the form of extra properties,
    # which are often ignored for backward-compatibility by the tested apps
    from schemathesis.specs.openapi.schemas import OpenApiSchema

    meta = case.meta
    if meta is None or not isinstance(case.operation.schema, OpenApiSchema):
        # Ignore manually created cases
        return False
    # Component-mode flags overestimate negation: the engine flips a location's mode
    # to negative whenever it tries to negate, even when it falls back to positive
    # (e.g. path params that can't be negated). When per-case mutation metadata is
    # available, trust the actually-targeted location over the coarse flags.
    phase_data = meta.phase.data
    if isinstance(phase_data, FuzzingPhaseData) and phase_data.mutations:
        targeted = {phase_data.parameter_location, *(mutation.parameter_location for mutation in phase_data.mutations)}
        if ParameterLocation.BODY in targeted or ParameterLocation.PATH in targeted:
            return False
    elif (ParameterLocation.BODY in meta.components and meta.components[ParameterLocation.BODY].mode.is_negative) or (
        ParameterLocation.PATH in meta.components and meta.components[ParameterLocation.PATH].mode.is_negative
    ):
        # Body or path negations always imply other negations
        return False
    validator_cls = case.operation.schema.adapter.jsonschema_validator_cls
    for location in (ParameterLocation.QUERY, ParameterLocation.HEADER, ParameterLocation.COOKIE):
        meta_for_location = meta.components.get(location)
        value = case.get_container(location)
        if isinstance(value, Mapping) and meta_for_location is not None and meta_for_location.mode.is_negative:
            container = getattr(case.operation, location.container_name)
            schema = container.schema

            if _has_serialization_sensitive_types(schema, container):
                # Wire-serialized arrays and objects can't be re-validated, so only mutation metadata can tell.
                if isinstance(phase_data, FuzzingPhaseData) and any(
                    _negates_declared_parameter(mutation, location, schema) for mutation in phase_data.mutations
                ):
                    return False
                continue

            properties = schema.get("properties", {})
            try:
                value_without_additional_properties = {
                    k: _boolean_from_wire_spelling(v, properties.get(k, {}), validator_cls)
                    for k, v in value.items()
                    if k in container
                }
                if isinstance(phase_data, FuzzingPhaseData):
                    # Generated nulls are sent as the text `null`, but in a mutated parameter it may be a negated string.
                    mutated = {
                        mutation.parameter
                        for mutation in phase_data.mutations
                        if mutation.parameter_location == location
                    }
                    for name, item in value_without_additional_properties.items():
                        if (
                            name not in mutated
                            and isinstance(item, str)
                            and reads_as_null(item, make_validator(properties.get(name, {}), validator_cls).is_valid)
                        ):
                            value_without_additional_properties[name] = None
                is_valid = make_validator(schema, validator_cls).is_valid(value_without_additional_properties)
            except Exception:
                # Schema has an invalid pattern (e.g., valid Python regex but invalid ECMA 262)
                # — can't determine validity, so skip this location
                continue
            if not is_valid:
                # Other types of negation found
                return False
    # Only additional properties are added
    return True


def _negates_declared_parameter(mutation: Mutation, location: ParameterLocation, schema: dict) -> bool:
    """Whether `mutation` invalidates a declared parameter in a way that survives wire serialization."""
    if mutation.parameter_location != location or mutation.parameter is None:
        return False
    if not _allows_container_type(schema.get("properties", {}).get(mutation.parameter, {})):
        return True
    # Other types arrive as a string that reads as a one-element array, and an empty value is sent as no value.
    return mutation.operator != OperatorKind.CHANGE_TYPE and "required" not in mutation.keywords


def _boolean_from_wire_spelling(
    value: object, schema: JsonSchema, validator_cls: type[jsonschema_rs.Validator]
) -> object:
    """Booleans reach the check spelled as the wire sends them, so read spellings like `true`, `0`, or `yes` back."""
    # A schema that admits strings may also admit booleans, e.g. through `allowEmptyValue`; a valid string stays one.
    if (
        isinstance(value, str)
        and "boolean" in get_type(schema)
        and not make_validator(schema, validator_cls).is_valid(value)
    ):
        return string_to_boolean(value)
    return value


def _has_serialization_sensitive_types(schema: dict, container: OpenApiParameterSet) -> bool:
    """Check if schema contains array or object types in defined parameters.

    In query/header/cookie parameters, arrays and objects are serialized to strings.
    This makes post-serialization validation against the original schema unreliable:

    - Generated: ["foo", "bar"] (array)
    - Serialized: "foo,bar" (string)

    Validation of string against array schema fails incorrectly.
    A better approach would be to apply serialization later on in the process.
    """
    properties = schema.get("properties", {})
    return any(
        _allows_container_type(prop_schema) for prop_name, prop_schema in properties.items() if prop_name in container
    )


def _allows_container_type(schema: JsonSchema) -> bool:
    """Whether a parameter schema admits arrays or objects."""
    types = parameter_types(schema)
    return "array" in types or "object" in types


# Methods that cannot re-create a resource — reading, modifying-in-place, or removing.
# Only POST and PUT (and custom verbs not in this set) are considered potential re-creation methods.
_NON_CREATION_METHODS = frozenset(("get", "head", "options", "query", "delete", "patch"))


def _resource_recreated_after_delete(
    ctx: CheckContext,
    *,
    delete_case_id: str,
    current_case_id: str,
    delete_path: ResourcePath,
) -> bool:
    """Return True if a resource was re-created after the DELETE and before the current case.

    Scans all recorded cases in execution order (across every branch and every root
    transition in the scenario).  Any successful creation whose path is a prefix of
    the DELETE path that occurs *between* the DELETE step and the current step means
    the resource may have been re-created — for example via a circular link
    (DELETE -> POST) or via a second root POST that reuses a freed resource ID.
    """
    found_delete = False
    for case in ctx._find_all_cases():
        if case.id == delete_case_id:
            found_delete = True
            continue
        if not found_delete:
            continue
        if case.id == current_case_id:
            return False
        resp = ctx._find_response(case_id=case.id)
        if (
            case.operation.method.lower() not in _NON_CREATION_METHODS
            and resp is not None
            and 200 <= resp.status_code < 300
            and _is_prefix_operation(
                ResourcePath(case.path, case.path_parameters or {}),
                delete_path,
            )
        ):
            return True
    return False


def _created_a_resource(*, parent: Case, parent_response: Response, case: Case) -> bool:
    """Whether a successful POST means the resource the follow-up reads was created.

    A POST to a collection creates the item below it. A POST to the item's own URI could equally
    be a rename, an action, or a delete spelled with the wrong verb, so it has to say that it
    created something.
    """
    if len(parent.path.rstrip("/").split("/")) < len(case.path.rstrip("/").split("/")):
        return True
    return parent_response.status_code == 201 or "Location" in parent_response.headers


def _stale_cache_hint(response: Response) -> str:
    # A shared cache can keep serving the pre-write state, so the failure may belong to the intermediary, not the API.
    age = response.headers.get("age", [""])[0]
    if age.isdigit() and int(age) > 0:
        return f"\n\nThe response came from a cache (`Age: {age}`) and may be stale"
    x_cache = response.headers.get("x-cache", [""])[0]
    if "HIT" in x_cache.upper():
        return f"\n\nThe response came from a cache (`X-Cache: {x_cache}`) and may be stale"
    return ""


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
def use_after_free(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    # Only check for use-after-free on successful responses (2xx) or redirects (3xx)
    # Other status codes indicate request-level issues / server errors, not successful resource access
    if not (200 <= response.status_code < 400):
        return None

    # DELETE is idempotent (RFC 7231 §4.2.2 / §4.3.5): a repeated DELETE may return 200/204, not 404.
    if case.operation.method.lower() == "delete":
        return None

    # PUT, POST, and other creation-capable verbs re-create the resource at the target URI,
    # so a successful response is a re-creation, not a use-after-free.
    if case.operation.method.lower() not in _NON_CREATION_METHODS:
        return None

    for related_case in ctx._find_related(case_id=case.id):
        parent = ctx._find_parent(case_id=related_case.id)
        if not parent:
            continue

        parent_response = ctx._find_response(case_id=parent.id)

        # The DELETE itself must have succeeded for a subsequent read to be a use-after-free —
        # a 5xx (server crash) or 404 (nothing to delete) leaves the resource intact.
        delete_response = ctx._find_response(case_id=related_case.id)
        if (
            related_case.operation.method.lower() == "delete"
            and parent_response is not None
            and 200 <= parent_response.status_code < 300
            and delete_response is not None
            and 200 <= delete_response.status_code < 300
            and related_case.path_parameters
        ):
            # A DELETE without path parameters targets a collection, not a specific
            # resource — a follow-up read on the same path is a list read, not
            # use-after-free.
            delete_path = ResourcePath(related_case.path, related_case.path_parameters or {})
            if _is_prefix_operation(
                delete_path,
                ResourcePath(case.path, case.path_parameters or {}),
            ):
                recreated = _resource_recreated_after_delete(
                    ctx,
                    delete_case_id=related_case.id,
                    current_case_id=case.id,
                    delete_path=delete_path,
                )
                if recreated:
                    continue
                free = f"{related_case.operation.method.upper()} {prepare_path(related_case.path, related_case.path_parameters)}"
                usage = f"{case.operation.method.upper()} {prepare_path(case.path, case.path_parameters)}"
                reason = http.client.responses.get(response.status_code, "Unknown")
                raise UseAfterFree(
                    operation=related_case.operation.label,
                    message=(
                        "The API did not return a `HTTP 404 Not Found` response "
                        f"(got `HTTP {response.status_code} {reason}`) for a resource that was previously deleted.\n\nThe resource was deleted with `{free}`"
                        f"{_stale_cache_hint(response)}"
                    ),
                    free=free,
                    usage=usage,
                    deleted_case_id=related_case.id,
                )

    return None


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
def ensure_resource_availability(ctx: CheckContext, response: Response, case: Case) -> bool | None:
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
        and _created_a_resource(parent=parent, parent_response=parent_response, case=case)
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

    # Look for any successful DELETE operations on this resource across all recorded cases,
    # not just the current root's subtree.
    if ctx._recorder is not None and resource_was_deleted(ctx._recorder, case):
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
            f"{_stale_cache_hint(response)}"
        ),
        created_with=created_with,
        not_available_with=not_available_with,
    )


class AuthKind(str, enum.Enum):
    EXPLICIT = "explicit"
    GENERATED = "generated"


# Statuses that mean the API refused an unauthenticated or badly authenticated request. Frameworks whose
# first authentication scheme offers no challenge answer 403 instead of 401, and gateways do the same.
AUTH_ENFORCED_STATUSES = frozenset({401, 403})


@schemathesis.check
@requires_openapi_schema
@skips_on_unexpected_http_status
def ignored_auth(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """Check if an operation declares authentication as a requirement but does not actually enforce it."""
    from schemathesis.specs.openapi.adapter.security import has_effective_optional_auth

    operation = case.operation
    if has_effective_optional_auth(operation, operation.schema.raw_schema):
        return True
    # An invalid path parameter can miss the operation's route entirely, so the response says nothing about its auth.
    path_component = case.meta.components.get(ParameterLocation.PATH) if case.meta is not None else None
    if path_component is not None and path_component.mode.is_negative:
        return None
    security_parameters = get_security_parameters(case.operation)
    # Authentication is required for this API operation and response is successful
    if security_parameters and 200 <= response.status_code < 300:
        # Probes can't remove credentials the schema doesn't declare, so their outcome proves nothing
        if _has_undeclared_explicit_authorization(ctx, response, security_parameters):
            return None
        auth = _contains_auth(ctx, case, response, security_parameters)
        # Without valid credentials, a redirect elsewhere (e.g. to a sign-in page) is how the server refuses.
        if auth != AuthKind.EXPLICIT and _url_sent_to_operation(response, case) is None:
            return None
        if auth == AuthKind.EXPLICIT:
            enforced = ctx.auth_enforced_operations
            # Enforcement is a property of the operation, so one confirmation per run is enough.
            if enforced is not None and case.operation.label in enforced:
                return None
            # Auth is explicitly set, it is expected to be valid
            # Check if invalid auth will give an error
            no_auth_case = remove_auth(case, security_parameters)
            no_auth_response = _send_probe(ctx, case, no_auth_case)
            if not _enforces_auth(no_auth_response, response):
                _raise_no_auth_error(no_auth_response, no_auth_case, AuthScenario.NO_AUTH)
            # Try to set invalid auth and check if it succeeds
            for parameter in security_parameters:
                invalid_auth_case = remove_auth(case, security_parameters)
                set_auth_for_case(invalid_auth_case, parameter)
                invalid_auth_response = _send_probe(ctx, case, invalid_auth_case)
                if not _enforces_auth(invalid_auth_response, response):
                    _raise_no_auth_error(invalid_auth_response, invalid_auth_case, AuthScenario.INVALID_AUTH)
            if enforced is not None:
                enforced.add(case.operation.label)
        elif auth == AuthKind.GENERATED:
            # If this auth is generated which means it is likely invalid, then
            # this request should have been an error
            _raise_no_auth_error(response, case, AuthScenario.GENERATED_AUTH)
        else:
            # Successful response when there is no auth
            _raise_no_auth_error(response, case, AuthScenario.NO_AUTH)
    return None


@schemathesis.check
@requires_openapi_schema
def object_level_authorization(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """Check that one identity cannot read an object another identity created."""
    from schemathesis.specs.openapi.extra_data_source import declared_response_schema
    from schemathesis.specs.openapi.object_authorization import (
        apply_as,
        owner_provider,
        strip_credentials,
        value_paths,
    )
    from schemathesis.wfc.escalation import escalating_provider

    # Replaying a write as another identity would change the owner's data.
    if case.method != "GET" or not 200 <= response.status_code < 300 or case.meta is None:
        return None
    provider = escalating_provider(case.operation.schema)
    owner = case._auth_identity
    if provider is None or owner not in provider.peers or not _requires_authentication(case.operation):
        return None
    content_types = response.headers.get("content-type")
    content_type = content_types[0] if content_types else None
    # Without a declared schema, every field of the body counts.
    schema = declared_response_schema(case.operation, response.status_code, content_type) or {}
    owner_body = _json_body(response)
    for resource, value in _owned_values(ctx, case, case.meta, owner):
        if not value_paths(owner_body, value):
            continue
        for peer in provider.peers:
            if peer == owner or not provider.claim_probe((case.operation.label, resource, peer)):
                continue
            peer_case = apply_as(case, provider.provider_for(peer), peer)
            peer_response = _send_probe(ctx, case, peer_case)
            if not _equivalent_response(peer_response, owner_body, value, schema):
                continue
            anonymous_response = _send_probe(ctx, case, strip_credentials(case, owner_provider(case)))
            # A body anyone gets is public; an unenforced declared scheme is `ignored_auth`'s to report.
            if _equivalent_response(anonymous_response, owner_body, value, schema):
                continue
            raise ObjectLevelAuthorizationViolation(
                operation=case.operation.label,
                message=(
                    f"`{peer}` received the `{resource}` that `{owner}` created\n\n"
                    f"Owner: {case.method} {case.formatted_path} as {owner} -> {response.status_code}\n"
                    f"Peer:  {peer_case.method} {peer_case.formatted_path} as {peer} -> "
                    f"{peer_response.status_code} (equivalent body)"
                ),
                owner=owner,
                peer=peer,
                owner_case_id=case.id,
                case_id=peer_case.id,
            )
    return None


def _owned_values(ctx: CheckContext, case: Case, meta: CaseMetadata, owner: str) -> Iterator[tuple[str, object]]:
    """Resource names and values in `case` that an earlier write by `owner` produced."""
    from schemathesis.specs.openapi.object_authorization import value_paths

    # A read shows other users' objects too, so only a write proves who created one.
    for draw in meta.pool_draws:
        container = case.get_container(ParameterLocation(draw.location))
        if (
            draw.source_identity == owner
            and draw.source_operation.split(" ", 1)[0] not in SAFE_METHODS
            and isinstance(container, Mapping)
            and draw.parameter_name in container
        ):
            yield draw.resource_name, container[draw.parameter_name]
    # Stateful steps get ids through links: the previous step either returned them or sent them.
    parent = ctx._find_parent(case_id=case.id)
    if parent is None or parent._auth_identity != owner or parent.method.upper() in SAFE_METHODS:
        return
    parent_response = ctx._find_response(case_id=parent.id)
    parent_body = _json_body(parent_response) if parent_response is not None else None
    for name, value in (case.path_parameters or {}).items():
        if value_paths(parent_body, value) or value_paths(parent.body, value):
            yield name, value


def _send_probe(ctx: CheckContext, parent: Case, probe: Case) -> Response:
    kwargs = build_retry_transport_kwargs(ctx._transport_kwargs, get_security_parameters(parent.operation))
    if parent.operation.app is not None:
        kwargs.setdefault("app", parent.operation.app)
    ctx._record_case(parent_id=parent.id, case=probe)
    probe_response = parent.operation.schema.transport.send(probe, **kwargs)
    ctx._record_response(case_id=probe.id, response=probe_response)
    return probe_response


def _json_body(response: Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _equivalent_response(response: Response, owner_body: object, value: object, schema: dict[str, Any]) -> bool:
    from schemathesis.specs.openapi.object_authorization import is_equivalent

    return 200 <= response.status_code < 300 and is_equivalent(owner_body, _json_body(response), value, schema)


def _has_undeclared_explicit_authorization(
    ctx: CheckContext, response: Response, security_parameters: list[Mapping[str, Any]]
) -> bool:
    if any(p["in"] == "header" and p["name"].lower() == "authorization" for p in security_parameters):
        return False
    sources = [
        ctx._headers,
        ctx._override.headers if ctx._override else None,
        response._override.headers if response._override else None,
    ]
    return any(headers is not None and any(name.lower() == "authorization" for name in headers) for headers in sources)


def _enforces_auth(response: Response, authenticated: Response) -> bool:
    if response.status_code in AUTH_ENFORCED_STATUSES or 300 <= response.status_code < 400:
        return True
    # Session-based auth redirects to a sign-in page; a followed redirect ends on a different path.
    return urlparse(response.request.url).path != urlparse(authenticated.request.url).path


def _raise_no_auth_error(response: Response, case: Case, auth: AuthScenario) -> NoReturn:
    reason = http.client.responses.get(response.status_code, "Unknown")
    accepted = 200 <= response.status_code < 300

    if auth == AuthScenario.NO_AUTH:
        title = (
            "API accepts requests without authentication"
            if accepted
            else "Unexpected response to a request without authentication"
        )
        detail = None
    elif auth == AuthScenario.INVALID_AUTH:
        title = "API accepts invalid authentication" if accepted else "Unexpected response to invalid authentication"
        detail = "invalid credentials provided"
    else:
        title = "API accepts invalid authentication"
        detail = "generated auth likely invalid"

    message = f"Expected 401 or 403, got `{response.status_code} {reason}` for `{case.operation.label}`"
    if detail is not None:
        message = f"{message} ({detail})"

    raise IgnoredAuth(
        operation=case.operation.label,
        message=message,
        scenario=auth,
        title=title,
        case_id=case.id,
    )


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
    query = parse_qs(parsed.query)  # type: ignore[type-var]
    # Load the `Cookie` header separately, because it is possible that `request._cookies` and the header are out of sync
    header_cookies: SimpleCookie = SimpleCookie()
    raw_cookie = request.headers.get("Cookie")
    if raw_cookie is not None:
        try:
            header_cookies.load(raw_cookie)
        except CookieError:
            # Generated values may render a header the stdlib parser rejects; fall back to the cookie jar.
            header_cookies = SimpleCookie()

    def has_header(p: Mapping[str, Any]) -> bool:
        return p["in"] == "header" and p["name"] in request.headers

    def has_query(p: Mapping[str, Any]) -> bool:
        return p["in"] == "query" and p["name"] in query

    def has_cookie(p: Mapping[str, Any]) -> bool:
        cookies = cast(RequestsCookieJar, request._cookies)  # type: ignore[attr-defined]
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
            transport_params = ctx._transport_kwargs.get("params") if ctx._transport_kwargs else None
            if (
                (ctx._override and name in ctx._override.query)
                or (response._override and name in response._override.query)
                or (isinstance(transport_params, dict) and name in transport_params)
            ):
                return AuthKind.EXPLICIT
            return AuthKind.GENERATED

    return None


@dataclass
class ResourcePath:
    """A path to a resource with variables."""

    value: str
    variables: dict[str, str]

    __slots__ = ("value", "variables")

    def render(self, segment: str) -> str:
        # Segments may mix variables with literal text, like `{id}:cancel` or `{name}.{ext}`.
        return _PATH_VARIABLE.sub(lambda match: str(self.variables[match.group(1)]), segment)


_PATH_VARIABLE = re.compile(r"\{([^{}]+)\}")


def _is_prefix_operation(lhs: ResourcePath, rhs: ResourcePath) -> bool:
    lhs_parts = lhs.value.rstrip("/").split("/")
    rhs_parts = rhs.value.rstrip("/").split("/")

    # Left has more parts, can't be a prefix
    if len(lhs_parts) > len(rhs_parts):
        return False

    for left, right in zip(lhs_parts, rhs_parts, strict=False):
        if "{" in left and "{" in right:
            resource = lhs.render(left)
            target = rhs.render(right)
            # A custom method like `{id}:cancel` acts on the resource named before the colon.
            if target != resource and not target.startswith(f"{resource}:"):
                return False
        elif left != right and left.rstrip("s") != right.rstrip("s"):
            # Parts don't match, not a prefix
            return False

    # If we've reached this point, the LHS path is a prefix of the RHS path
    return True


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def resource_was_deleted(recorder: RecordedScenario, case: Case) -> bool:
    """Return True if a successful prior request in the scenario removed this case's resource.

    A DELETE covers every path below the one it targets. Any other unsafe verb may be a removal
    spelled with the wrong method, so it only counts against the resource's own URI. Used to
    suppress false positives in checks and in link-calibration observations.
    """
    case_path = ResourcePath(case.path, case.path_parameters or {})
    for prior_case in recorder.find_all_cases():
        if prior_case.id == case.id:
            continue
        method = prior_case.operation.method.upper()
        if method in SAFE_METHODS:
            continue
        prior_response = recorder.find_response(case_id=prior_case.id)
        if prior_response is None or not (200 <= prior_response.status_code < 300):
            continue
        prior_path = ResourcePath(prior_case.path, prior_case.path_parameters or {})
        if method == "DELETE":
            if _is_prefix_operation(prior_path, case_path):
                return True
        elif prior_case.path == case.path and _is_prefix_operation(prior_path, case_path):
            return True
    return False
