from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

import jsonschema_rs

from schemathesis.core import NotSet, media_types
from schemathesis.core.jsonschema import make_validator, make_validator_for
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.core.parameters import ParameterLocation, plain_str_values
from schemathesis.core.transforms import to_wire_string
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import CONTENT_TYPE_PROBES, FuzzingPhaseData, coverage_scenario
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameter, OpenApiParameterSet
from schemathesis.specs.openapi.schemas import OpenApiSchema
from schemathesis.specs.openapi.utils import coerce_wire_string, parameter_types, sent_text_is_valid

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.generation.meta import CaseMetadata
    from schemathesis.schemas import APIOperation


@dataclass(frozen=True, slots=True)
class ConformanceViolation:
    location: ParameterLocation
    media_type: str | None
    value: object
    expected_valid: bool
    errors: tuple[str, ...] = ()
    # The value is what the server reads off the wire, not the generated one.
    after_serialization: bool = False


def evaluate_conformance(
    *,
    value: object,
    location: ParameterLocation,
    media_type: str | None,
    schema: JsonSchemaObject,
    validator_cls: type | None,
    is_negative: bool,
) -> ConformanceViolation | None:
    try:
        validator = make_validator(schema, validator_cls) if validator_cls is not None else make_validator_for(schema)
    except jsonschema_rs.ValidationError:
        return None
    try:
        is_valid = validator.is_valid(value)
    except ValueError:
        return None

    if is_negative:
        if is_valid:
            return ConformanceViolation(location=location, media_type=media_type, value=value, expected_valid=False)
        return None
    if not is_valid:
        errors = tuple(error.message for error in validator.iter_errors(value))
        return ConformanceViolation(
            location=location, media_type=media_type, value=value, expected_valid=True, errors=errors[:5]
        )
    return None


# Body first so the longer-standing allow-list keeps matching on operations that violate in both places.
_CHECKED_LOCATIONS = (
    ParameterLocation.BODY,
    ParameterLocation.QUERY,
    ParameterLocation.PATH,
    ParameterLocation.HEADER,
    ParameterLocation.COOKIE,
)


def check_conformance(case: Case) -> ConformanceViolation | None:
    """First mismatch between generated data and the schema production validates it against."""
    meta = case.meta
    schema = case.operation.schema
    if meta is None or not isinstance(schema, OpenApiSchema):
        return None
    validator_cls = schema.adapter.jsonschema_validator_cls
    case_is_negative = meta.generation.mode == GenerationMode.NEGATIVE
    # A location's mode says the engine tried to negate it, not that it succeeded; the mutation names the one it hit.
    phase_data = meta.phase.data
    in_fuzzing = isinstance(phase_data, FuzzingPhaseData)
    negated_location = phase_data.parameter_location if in_fuzzing and phase_data.mutations else None
    # Content-Type probes negate the declared request media types, not the header parameter schema.
    is_content_type_probe = coverage_scenario(case) in CONTENT_TYPE_PROBES
    for location in _CHECKED_LOCATIONS:
        component = meta.components.get(location)
        if component is None:
            continue
        is_negative = component.mode == GenerationMode.NEGATIVE
        # Only the container the mutation hit is really negated; a flag without one means the attempt fell back.
        if in_fuzzing and is_negative and location != negated_location:
            continue
        # Negative generation leaves untouched containers incomplete, so they carry no expectation.
        if case_is_negative and not is_negative and location != ParameterLocation.BODY:
            continue
        if is_content_type_probe and location == ParameterLocation.HEADER:
            continue
        if location == ParameterLocation.BODY:
            violation = _check_body(case, validator_cls=validator_cls, is_negative=is_negative)
        else:
            violation = _check_container(case, meta, location, validator_cls=validator_cls, is_negative=is_negative)
        if violation is not None:
            return violation
    return None


def _check_body(case: Case, *, validator_cls: type, is_negative: bool) -> ConformanceViolation | None:
    if isinstance(case.body, NotSet) or case.body is None:
        return None
    alternative = next(
        (
            alt
            for alt in case.operation.body
            if alt.media_type == case.media_type and media_types.is_json(alt.media_type)
        ),
        None,
    )
    if alternative is None:
        return None
    return evaluate_conformance(
        value=case.body,
        location=ParameterLocation.BODY,
        media_type=alternative.media_type,
        schema=alternative.validation_schema,
        validator_cls=validator_cls,
        is_negative=is_negative,
    )


def _check_container(
    case: Case,
    meta: CaseMetadata,
    location: ParameterLocation,
    *,
    validator_cls: type,
    is_negative: bool,
) -> ConformanceViolation | None:
    parameters = _parameter_set(case.operation, location)
    if not parameters.items:
        return None
    # Only the typed snapshot is comparable; the wire form is strings, spread keys and percent-encoding.
    value = meta.raw_containers.get(location)
    if not isinstance(value, Mapping):
        return None
    judge = partial(
        evaluate_conformance,
        location=location,
        media_type=None,
        schema=parameters.validation_schema,
        validator_cls=validator_cls,
        is_negative=is_negative,
    )
    generated = plain_str_values(dict(value))
    violation = judge(value=generated)
    if violation is not None:
        return violation
    violation = judge(value=_read_from_wire(case, location, parameters, generated))
    return replace(violation, after_serialization=True) if violation is not None else None


def _read_from_wire(
    case: Case, location: ParameterLocation, parameters: OpenApiParameterSet, generated: dict[str, object]
) -> dict[str, object]:
    """The container a server reads back from the text each scalar parameter is sent as."""
    sent = case.get_container(location)
    if not isinstance(sent, Mapping):
        return generated
    result = dict(generated)
    for parameter in parameters.items:
        name = parameter.name
        if name not in result or name not in sent:
            continue
        value = sent[name]
        # Arrays and objects keep the generated value; their `style` spellings have no reader here.
        if value is not None and not isinstance(value, (str, int, float)):
            continue
        if {"array", "object"} & set(parameter_types(parameter.validation_schema)):
            continue
        text = to_wire_string(value)
        # A value sent as generated is spelled the way the schema declares it, even when that spelling is encoded.
        if location == ParameterLocation.PATH and text != generated[name]:
            text = unquote(text)
        if next(iter(parameter.definition.get("content", {})), None) == "application/json":
            result[name] = _read_json(text)
        else:
            result[name] = _read_text(text, parameter)
    return result


def _read_json(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _read_text(text: str, parameter: OpenApiParameter) -> object:
    """The value a server reads from `text`: a reading the schema accepts if there is one, else the text itself."""
    schema = parameter.validation_schema
    try:
        validator = make_validator(schema, parameter.adapter.jsonschema_validator_cls)
    except jsonschema_rs.ValidationError:
        return text
    types = parameter_types(schema)
    if not sent_text_is_valid(text, validator.is_valid, types):
        return text
    # A non-finite number counts as valid on the wire yet fails the schema, so it falls through to the text.
    for reading in (text, _coerce(text, types), None):
        if validator.is_valid(reading):
            return reading
    return text


def _coerce(text: str, types: list[str]) -> object:
    # An integer parser keeps every digit a float would round away.
    if "number" in types and text.lstrip("-").isdigit():
        return int(text)
    return coerce_wire_string(text, types)


def _parameter_set(operation: APIOperation, location: ParameterLocation) -> OpenApiParameterSet:
    if location == ParameterLocation.QUERY:
        return cast("OpenApiParameterSet", operation.query)
    if location == ParameterLocation.PATH:
        return cast("OpenApiParameterSet", operation.path_parameters)
    if location == ParameterLocation.HEADER:
        return cast("OpenApiParameterSet", operation.headers)
    return cast("OpenApiParameterSet", operation.cookies)
