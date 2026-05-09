from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import jsonschema_rs

from schemathesis.core import media_types
from schemathesis.core.jsonschema import make_validator, make_validator_for
from schemathesis.core.jsonschema.types import JsonSchemaObject
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.specs.openapi.schemas import OpenApiSchema

if TYPE_CHECKING:
    from schemathesis.generation.case import Case


@dataclass(frozen=True, slots=True)
class BodyViolation:
    media_type: str
    body: object
    expected_valid: bool
    errors: tuple[str, ...] = ()


def evaluate_body_conformance(
    *,
    body: object,
    media_type: str,
    schema: JsonSchemaObject,
    validator_cls: type | None,
    is_negative_body: bool,
) -> BodyViolation | None:
    try:
        validator = make_validator(schema, validator_cls) if validator_cls is not None else make_validator_for(schema)
    except jsonschema_rs.ValidationError:
        return None
    try:
        is_valid = validator.is_valid(body)
    except ValueError:
        return None

    if is_negative_body:
        if is_valid:
            return BodyViolation(media_type=media_type, body=body, expected_valid=False)
        return None
    if not is_valid:
        errors = tuple(error.message for error in validator.iter_errors(body))
        return BodyViolation(media_type=media_type, body=body, expected_valid=True, errors=errors[:5])
    return None


def check_body_conformance(case: Case) -> BodyViolation | None:
    if case.meta is None or case.body is None or not case.operation.body:
        return None
    schema = case.operation.schema
    if not isinstance(schema, OpenApiSchema):
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
    body_is_target = case.meta.phase.data.parameter_location == ParameterLocation.BODY
    body_component = case.meta.components.get(ParameterLocation.BODY)
    is_negative_body = body_is_target and body_component is not None and body_component.mode == GenerationMode.NEGATIVE
    return evaluate_body_conformance(
        body=case.body,
        media_type=alternative.media_type,
        schema=alternative.optimized_schema,
        validator_cls=schema.adapter.jsonschema_validator_cls,
        is_negative_body=is_negative_body,
    )
