from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import jsonschema_rs

from schemathesis.core import NotSet, media_types
from schemathesis.core.jsonschema import make_validator, make_validator_for
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.core.parameters import ParameterLocation, plain_str_values
from schemathesis.generation import GenerationMode
from schemathesis.generation.meta import FuzzingPhaseData
from schemathesis.specs.openapi.adapter.parameters import OpenApiParameterSet
from schemathesis.specs.openapi.schemas import OpenApiSchema

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
    return evaluate_conformance(
        value=plain_str_values(dict(value)),
        location=location,
        media_type=None,
        schema=parameters.validation_schema,
        validator_cls=validator_cls,
        is_negative=is_negative,
    )


def _parameter_set(operation: APIOperation, location: ParameterLocation) -> OpenApiParameterSet:
    if location == ParameterLocation.QUERY:
        return cast("OpenApiParameterSet", operation.query)
    if location == ParameterLocation.PATH:
        return cast("OpenApiParameterSet", operation.path_parameters)
    if location == ParameterLocation.HEADER:
        return cast("OpenApiParameterSet", operation.headers)
    return cast("OpenApiParameterSet", operation.cookies)
