from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from hypothesis import HealthCheck
from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable
from hypothesis.reporting import with_reporter

from schemathesis.config import OutputConfig
from schemathesis.core.jsonschema.bundler import unbundle
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.output import truncate_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.hypothesis.examples import generate_one

if TYPE_CHECKING:
    from schemathesis.schemas import APIOperation


def ignore(_: str) -> None:
    pass


@contextmanager
def ignore_hypothesis_output() -> Generator:
    with with_reporter(ignore):
        yield


UNSATISFIABILITY_CAUSE = """  - Type mismatch (e.g., enum with strings but type: integer)
  - Contradictory constraints (e.g., minimum > maximum)
  - Regex that's too complex to generate values for"""

GENERIC_UNSATISFIABLE_MESSAGE = f"""Cannot generate test data for this operation

Unable to identify the specific parameter. Common causes:
{UNSATISFIABILITY_CAUSE}"""


@dataclass
class UnsatisfiableParameter:
    location: ParameterLocation
    name: str
    schema: JsonSchema

    __slots__ = ("location", "name", "schema")

    def get_error_message(self, config: OutputConfig) -> str:
        formatted_schema = truncate_json(self.schema, config=config)

        if self.location == ParameterLocation.BODY:
            # For body, name is the media type
            location = f"request body ({self.name})"
        else:
            location = f"{self.location.value} parameter '{self.name}'"

        return f"""Cannot generate test data for {location}
Schema:

{formatted_schema}

This usually means:
{UNSATISFIABILITY_CAUSE}"""


def find_unsatisfiable_parameter(operation: APIOperation) -> UnsatisfiableParameter | None:
    from hypothesis_jsonschema import from_schema

    for location, container in (
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.PATH, operation.path_parameters),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
        (ParameterLocation.BODY, operation.body),
    ):
        for parameter in container:
            try:
                generate_one(from_schema(parameter.optimized_schema))
            except Unsatisfiable:
                if location == ParameterLocation.BODY:
                    name = parameter.media_type
                else:
                    name = parameter.name
                schema = unbundle(parameter.optimized_schema, parameter.name_to_uri)
                return UnsatisfiableParameter(location=location, name=name, schema=schema)
    return None


def build_unsatisfiable_error(operation: APIOperation, *, with_tip: bool) -> Unsatisfiable:
    __tracebackhide__ = True
    unsatisfiable = find_unsatisfiable_parameter(operation)

    if unsatisfiable is not None:
        message = unsatisfiable.get_error_message(operation.schema.config.output)
    else:
        message = GENERIC_UNSATISFIABLE_MESSAGE

    if with_tip:
        message += "\n\nTip: Review all parameters and request body schemas for conflicting constraints"

    return Unsatisfiable(message)


HEALTH_CHECK_CAUSES = {
    HealthCheck.data_too_large: """  - Arrays with large minItems (e.g., minItems: 1000)
  - Strings with large minLength (e.g., minLength: 10000)
  - Deeply nested objects with many required properties""",
    HealthCheck.filter_too_much: """  - Complex regex patterns that match few strings
  - Multiple overlapping constraints (pattern + format + enum)""",
    HealthCheck.too_slow: """  - Regex with excessive backtracking (e.g., (a+)+b)
  - Many interdependent constraints
  - Large combinatorial complexity""",
    HealthCheck.large_base_example: """  - Arrays with large minimum size (e.g., minItems: 100)
  - Many required properties with their own large minimums
  - Nested structures that multiply size requirements""",
}

HEALTH_CHECK_ACTIONS = {
    HealthCheck.data_too_large: "Reduce minItems, minLength, or size constraints to realistic values",
    HealthCheck.filter_too_much: "Simplify constraints or widen acceptable value ranges",
    HealthCheck.too_slow: "Simplify regex patterns or reduce constraint complexity",
    HealthCheck.large_base_example: "Reduce minimum size requirements or number of required properties",
}

HEALTH_CHECK_TITLES = {
    HealthCheck.data_too_large: "Generated examples exceed size limits",
    HealthCheck.filter_too_much: "Too many generated examples are filtered out",
    HealthCheck.too_slow: "Data generation is too slow",
    HealthCheck.large_base_example: "Minimum possible example is too large",
}


@dataclass
class SlowParameter:
    """Information about a parameter with slow or problematic data generation."""

    location: ParameterLocation
    name: str
    schema: JsonSchema
    original: HealthCheck

    __slots__ = ("location", "name", "schema", "original")

    def get_error_message(self, config: OutputConfig) -> str:
        formatted_schema = truncate_json(self.schema, config=config)
        if self.location == ParameterLocation.BODY:
            # For body, name is the media type
            location = f"request body ({self.name})"
        else:
            location = f"{self.location.value} parameter '{self.name}'"
        title = HEALTH_CHECK_TITLES[self.original]
        causes = HEALTH_CHECK_CAUSES[self.original]

        return f"""{title} for {location}
Schema:

{formatted_schema}

This usually means:
{causes}"""


def _extract_health_check_reason(exc: FailedHealthCheck | InvalidArgument) -> HealthCheck | None:
    message = str(exc).lower()
    if "data_too_large" in message or "too large" in message:
        return HealthCheck.data_too_large
    elif "filter_too_much" in message or "filtered out" in message:
        return HealthCheck.filter_too_much
    elif "too_slow" in message or "too slow" in message:
        return HealthCheck.too_slow
    elif ("large_base_example" in message or "can never generate an example, because min_size" in message) or (
        isinstance(exc, InvalidArgument)
        and message.endswith("larger than hypothesis is designed to handle")
        or "can never generate an example, because min_size is larger than hypothesis supports" in message
    ):
        return HealthCheck.large_base_example

    return None


def find_slow_parameter(operation: APIOperation, reason: HealthCheck) -> SlowParameter | None:
    from hypothesis.errors import FailedHealthCheck
    from hypothesis_jsonschema import from_schema

    for location, container in (
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.PATH, operation.path_parameters),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
        (ParameterLocation.BODY, operation.body),
    ):
        for parameter in container:
            try:
                generate_one(from_schema(parameter.optimized_schema), suppress_health_check=[])
            except (FailedHealthCheck, Unsatisfiable, InvalidArgument):
                if location == ParameterLocation.BODY:
                    name = parameter.media_type
                else:
                    name = parameter.name

                schema = unbundle(parameter.optimized_schema, parameter.name_to_uri)
                return SlowParameter(location=location, name=name, schema=schema, original=reason)
    return None


def _get_generic_health_check_message(reason: HealthCheck) -> str:
    title = HEALTH_CHECK_TITLES[reason]
    causes = HEALTH_CHECK_CAUSES[reason]
    return f"{title} for this operation\n\nUnable to identify the specific parameter. Common causes:\n{causes}"


class HealthCheckTipStyle(Enum):
    DEFAULT = "default"
    PYTEST = "pytest"


def build_health_check_error(
    operation: APIOperation,
    original: FailedHealthCheck | InvalidArgument,
    with_tip: bool,
    tip_style: HealthCheckTipStyle = HealthCheckTipStyle.DEFAULT,
) -> FailedHealthCheck | InvalidArgument:
    __tracebackhide__ = True
    reason = _extract_health_check_reason(original)
    if reason is None:
        return original
    slow_param = find_slow_parameter(operation, reason)

    if slow_param is not None:
        message = slow_param.get_error_message(operation.schema.config.output)
    else:
        message = _get_generic_health_check_message(reason)

    if with_tip:
        message += f"\n\nTip: {HEALTH_CHECK_ACTIONS[reason]}"
        if tip_style == HealthCheckTipStyle.PYTEST:
            message += f". You can disable this health check with @settings(suppress_health_check=[{reason!r}])"

    return FailedHealthCheck(message)
