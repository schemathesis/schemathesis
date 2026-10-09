from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable

from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema.bundler import unbundle
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.hypothesis.examples import generate_one
from schemathesis.generation.hypothesis.reporting import (
    UNSATISFIABILITY_CAUSE,
    SlowParameter,
    UnsatisfiableParameter,
    UnsatisfiableSchema,
    describe_unsatisfiable,
)

if TYPE_CHECKING:
    from hypothesis import HealthCheck
    from hypothesis.strategies import SearchStrategy

    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiComponent, OpenApiParameter


def _parameter_strategy(
    operation: APIOperation, parameter: OpenApiComponent, location: ParameterLocation
) -> SearchStrategy:
    from schemathesis.specs.openapi._hypothesis import make_positive_strategy

    return make_positive_strategy(
        parameter.optimized_schema,
        operation.label,
        location,
        parameter.media_type,
        operation.schema.config.generation_for(operation=operation, phase="fuzzing"),
        operation.schema.adapter.jsonschema_validator_cls,
        name_to_uri=parameter.name_to_uri,
    )


def _iter_parameters(
    operation: APIOperation,
) -> Generator[tuple[ParameterLocation, OpenApiParameter | OpenApiBody], None, None]:
    for location, container in (
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.PATH, operation.path_parameters),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
        (ParameterLocation.BODY, operation.body),
    ):
        for parameter in container:
            yield location, parameter


def _build_unsatisfiable_parameter(
    operation: APIOperation, location: ParameterLocation, parameter: OpenApiParameter | OpenApiBody
) -> UnsatisfiableParameter:
    return UnsatisfiableParameter(
        location=location,
        # A body is identified by its media type, every other parameter by its name.
        name=parameter.media_type or parameter.name,
        schema=unbundle(parameter.optimized_schema, parameter.name_to_uri),
        detail=describe_unsatisfiable(
            parameter.optimized_schema,
            parameter.name_to_uri,
            operation.schema.adapter.jsonschema_validator_cls,
        ),
    )


def find_unsatisfiable_parameter(operation: APIOperation) -> UnsatisfiableParameter | None:
    for location, parameter in _iter_parameters(operation):
        try:
            generate_one(_parameter_strategy(operation, parameter, location))
        except (Unsatisfiable, InvalidArgument, InvalidSchema):
            return _build_unsatisfiable_parameter(operation, location, parameter)
    return None


def find_slow_parameter(operation: APIOperation, reason: HealthCheck) -> SlowParameter | None:
    for location, parameter in _iter_parameters(operation):
        try:
            generate_one(_parameter_strategy(operation, parameter, location), suppress_health_check=[])
        except (FailedHealthCheck, Unsatisfiable, InvalidArgument, InvalidSchema):
            return SlowParameter(
                location=location,
                # A body is identified by its media type, every other parameter by its name.
                name=parameter.media_type or parameter.name,
                schema=unbundle(parameter.optimized_schema, parameter.name_to_uri),
                original=reason,
            )
    return None


def build_unsatisfiable_schema_error(operation: APIOperation) -> UnsatisfiableSchema:
    # Drawing is already under way here, so the parameter is found by reading the schemas rather than
    # by generating from each of them.
    for location, parameter in _iter_parameters(operation):
        unsatisfiable = _build_unsatisfiable_parameter(operation, location, parameter)
        if unsatisfiable.detail is not None:
            return UnsatisfiableSchema(unsatisfiable.get_error_message(operation.schema.config.output))
    return UnsatisfiableSchema(f"""Cannot generate test data for {operation.label}

This usually means:
{UNSATISFIABILITY_CAUSE}""")
