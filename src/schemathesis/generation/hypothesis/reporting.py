from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import jsonschema_rs
from hypothesis import HealthCheck
from hypothesis.errors import FailedHealthCheck, InvalidArgument, Unsatisfiable
from hypothesis.reporting import with_reporter
from jsonschema_rs import canonical

from schemathesis.config import OutputConfig
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import CANONICALIZE_DRAFT_BY_VALIDATOR, FANCY_REGEX_OPTIONS
from schemathesis.core.jsonschema.bundler import BUNDLE_STORAGE_KEY, unbundle, unbundle_path
from schemathesis.core.jsonschema.types import JsonSchema
from schemathesis.core.output import truncate_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import decode_pointer, encode_pointer, resolve_pointer
from schemathesis.generation.hypothesis.examples import generate_one

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.adapter.parameters import OpenApiBody, OpenApiComponent, OpenApiParameter


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

FILTER_CASE_UNSATISFIABLE_MESSAGE = """Your `filter_case` hook rejected all generated test cases

Schemathesis generated test data, but every case was rejected by your hook."""


@dataclass(slots=True)
class FilterCaseTracker:
    total: int = 0
    rejected: int = 0

    def record(self, accepted: bool) -> None:
        self.total += 1
        if not accepted:
            self.rejected += 1

    @property
    def has_data(self) -> bool:
        return self.total > 0

    @property
    def all_rejected(self) -> bool:
        return self.has_data and self.rejected == self.total


# A keyword value longer than this says less than its name does.
MAX_KEYWORD_VALUE_LENGTH = 30
# A chain longer than this is a cycle the reported pointers cannot close.
MAX_BLAME_HOPS = 10
# Keywords whose branches are worth naming one by one when none of them survives its siblings.
BRANCH_KEYWORDS = ("oneOf", "anyOf")
# Past this many branches the list stops reading as a sentence.
MAX_REPORTED_BRANCHES = 3
# Where a branch sits inside the schema each branch is checked with.
BRANCH_PROBE_PREFIX = "/allOf/1"


def _display_pointer(pointer: str, name_to_uri: dict[str, str]) -> str:
    segments = [decode_pointer(segment) for segment in pointer[1:].split("/")]
    return "/" + "/".join(encode_pointer(str(segment)) for segment in unbundle_path(list(segments), name_to_uri))


def _describe_keyword(schema: JsonSchema, pointer: str, keyword: str) -> str:
    """The keyword with its value, or just its name when the value says less than the name."""
    node = resolve_pointer(schema, pointer)
    if not isinstance(node, dict) or keyword not in node:
        return f"`{keyword}`"
    value = json.dumps(node[keyword])
    if len(value) > MAX_KEYWORD_VALUE_LENGTH:
        return f"`{keyword}`"
    return f"`{keyword}: {value}`"


def _find_unsatisfiable(schema: JsonSchema, draft: int) -> dict[str, canonical.UnsatisfiableReason] | None:
    try:
        return canonical.find_unsatisfiable(
            schema, draft=draft, pattern_options=FANCY_REGEX_OPTIONS, validate_formats=True
        )
    except canonical.InvalidPattern:
        # The error names the pattern, so the caller can say what stopped the analysis.
        raise
    except Exception:
        # Nothing is provably empty when the document cannot be read at all.
        return None


def _describe_branches(
    schema: JsonSchema, cause: canonical.Cause, name_to_uri: dict[str, str], draft: int
) -> str | None:
    """Each branch of a `oneOf`/`anyOf` that its siblings leave empty, or `None` when one survives.

    The branch itself admits values, so it is never reported on its own; it is checked here against
    the keywords beside it.
    """
    node = resolve_pointer(schema, cause.pointer)
    if not isinstance(node, dict):
        return None  # pragma: no cover
    keyword = next((keyword for keyword in cause.keywords if keyword in BRANCH_KEYWORDS), None)
    branches = node.get(keyword) if keyword is not None else None
    if not isinstance(branches, list) or not branches:
        return None
    siblings = {key: value for key, value in node.items() if key not in (keyword, BUNDLE_STORAGE_KEY)}
    bundle = schema.get(BUNDLE_STORAGE_KEY) if isinstance(schema, dict) else None
    described = []
    for index, branch in enumerate(branches):
        probe: dict[str, Any] = {"allOf": [siblings, branch]}
        if bundle is not None:
            probe[BUNDLE_STORAGE_KEY] = bundle
        reasons = _find_unsatisfiable(probe, draft)
        if reasons is None or "" not in reasons:
            return None
        at = f"{cause.pointer}/{keyword}/{index}"
        described.append(_describe_branch(branch, reasons[""], at, name_to_uri))
    remaining = len(described) - MAX_REPORTED_BRANCHES
    if remaining > 0:
        noun = "branch" if remaining == 1 else "branches"
        described = described[:MAX_REPORTED_BRANCHES] + [f"{remaining} more {noun}"]
    return _join_prose(described)


def _join_prose(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _describe_branch(
    branch: JsonSchema, reason: canonical.UnsatisfiableReason, pointer: str, name_to_uri: dict[str, str]
) -> str:
    """What the branch itself brings to the conflict, named at its own pointer."""
    if isinstance(reason, canonical.UnsatisfiableReason.Empty):
        causes = [reason.cause]
    elif isinstance(reason, canonical.UnsatisfiableReason.Conflict):
        causes = list(reason.causes)
    else:
        causes = []  # pragma: no cover
    # The probe puts the siblings first and the branch second, so only the second side is the branch.
    described = " and ".join(
        _describe_keyword(branch, cause.pointer[len(BRANCH_PROBE_PREFIX) :], keyword)
        for cause in causes
        if cause.pointer.startswith(BRANCH_PROBE_PREFIX)
        for keyword in cause.keywords
    )
    if not described:
        return f"the branch at {_display_pointer(pointer, name_to_uri)}"
    return f"{described} at {_display_pointer(pointer, name_to_uri)}"


def _describe_cause(
    schema: JsonSchema, cause: canonical.Cause, pointer: str, name_to_uri: dict[str, str], draft: int
) -> str:
    branches = _describe_branches(schema, cause, name_to_uri, draft)
    if branches is not None:
        return branches
    described = " and ".join(_describe_keyword(schema, cause.pointer, keyword) for keyword in cause.keywords)
    if not described:
        return f"the schema at {_display_pointer(cause.pointer, name_to_uri)}"
    if cause.pointer != pointer:
        described += f" at {_display_pointer(cause.pointer, name_to_uri)}"
    return described


def _blame(schema: JsonSchema, reasons: dict[str, canonical.UnsatisfiableReason], pointer: str) -> str:
    """The pointer that carries the conflict itself, following the keywords that only pass it on."""
    seen = {pointer}
    for _ in range(MAX_BLAME_HOPS):
        reason = reasons[pointer]
        if isinstance(reason, canonical.UnsatisfiableReason.Literal):
            return pointer
        causes = [reason.cause] if isinstance(reason, canonical.UnsatisfiableReason.Empty) else reason.causes
        for cause in causes:
            for candidate in _passed_on_to(schema, cause):
                if candidate in reasons and candidate not in seen:
                    seen.add(candidate)
                    pointer = candidate
                    break
            else:
                continue
            break
        else:
            return pointer
    return pointer


def _passed_on_to(schema: JsonSchema, cause: canonical.Cause) -> Generator[str, None, None]:
    """Pointers a keyword hands the emptiness over from - a reference, a required key, an element."""
    node = resolve_pointer(schema, cause.pointer)
    if not isinstance(node, dict):
        return  # pragma: no cover
    for keyword in cause.keywords:
        if keyword == "$ref":
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#"):  # pragma: no branch
                yield reference[1:]
        elif keyword == "required":
            required = node.get("required")
            if isinstance(required, list):  # pragma: no branch
                for name in required:
                    yield f"{cause.pointer}/properties/{encode_pointer(str(name))}"
        elif keyword == "prefixItems":
            elements = node.get("prefixItems")
            if isinstance(elements, list):  # pragma: no branch
                for index in range(len(elements)):
                    yield f"{cause.pointer}/prefixItems/{index}"
        elif keyword in ("items", "contains"):
            yield f"{cause.pointer}/{keyword}"


def describe_unsatisfiable(
    schema: JsonSchema, name_to_uri: dict[str, str], validator_cls: type[jsonschema_rs.Validator]
) -> str | None:
    """Why no value satisfies this schema, named at the subschema that carries the conflict.

    Emptiness that does not reach the root is routine - `additionalProperties: false` is an empty
    position and says nothing is wrong - so only a root that admits nothing is reported. A pattern
    the analysis cannot read is named instead, with the conflict left unlocated.
    """
    draft = CANONICALIZE_DRAFT_BY_VALIDATOR.get(validator_cls)
    if draft is None:
        return None  # pragma: no cover
    try:
        reasons = _find_unsatisfiable(schema, draft)
    except canonical.InvalidPattern as exc:
        return (
            f"A pattern could not be analyzed ({exc}), so the conflict is not located. "
            f"This usually means:\n{UNSATISFIABILITY_CAUSE}"
        )
    if reasons is None or "" not in reasons:
        return None
    pointer = _blame(schema, reasons, "")
    reason = reasons[pointer]
    if isinstance(reason, canonical.UnsatisfiableReason.Literal):
        detail = "The schema is `false`"
        located = False
    elif isinstance(reason, canonical.UnsatisfiableReason.Empty):
        detail = f"Nothing satisfies {_describe_cause(schema, reason.cause, pointer, name_to_uri, draft)}"
        located = reason.cause.pointer != pointer
    else:
        parts = [_describe_cause(schema, cause, pointer, name_to_uri, draft) for cause in reason.causes]
        # The subject is plural when the first part names more than one keyword or branch.
        detail = (" conflict with " if " and " in parts[0] else " conflicts with ").join(parts)
        located = all(cause.pointer != pointer for cause in reason.causes)
    # Every part already named where it sits, so the subschema holding them adds nothing.
    if pointer and not located:
        detail += f" at {_display_pointer(pointer, name_to_uri)}"
    return detail


@dataclass(slots=True)
class UnsatisfiableParameter:
    location: ParameterLocation
    name: str
    schema: JsonSchema
    detail: str | None

    def get_error_message(self, config: OutputConfig) -> str:
        formatted_schema = truncate_json(self.schema, config=config)

        if self.location == ParameterLocation.BODY:
            # For body, name is the media type
            location = f"request body ({self.name})"
        else:
            location = f"{self.location.value} parameter '{self.name}'"

        if self.detail is not None:
            explanation = self.detail
        else:
            explanation = f"This usually means:\n{UNSATISFIABILITY_CAUSE}"

        return f"""Cannot generate test data for {location}
Schema:

{formatted_schema}

{explanation}"""


def is_empty_strategy_error(exc: InvalidArgument) -> bool:
    """Whether `exc` reports a required strategy that can never produce a value.

    Hypothesis raises this when a non-empty collection is built from an empty element
    strategy - e.g. an array with `minItems` >= 1 whose items schema is unsatisfiable.
    """
    message = str(exc)
    return "because it has no values" in message or "no elements can be drawn from the element strategy" in message


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


def build_unsatisfiable_error(
    operation: APIOperation, *, with_tip: bool, filter_tracker: FilterCaseTracker | None = None
) -> Unsatisfiable:
    __tracebackhide__ = True

    if filter_tracker is not None and filter_tracker.all_rejected:
        message = FILTER_CASE_UNSATISFIABLE_MESSAGE
        if with_tip:
            message += "\n\nTip: Review your `filter_case` hook to ensure it accepts at least some generated cases"
        return Unsatisfiable(message)

    unsatisfiable = find_unsatisfiable_parameter(operation)

    if unsatisfiable is not None:
        message = unsatisfiable.get_error_message(operation.schema.config.output)
    else:
        message = GENERIC_UNSATISFIABLE_MESSAGE

    if with_tip:
        message += "\n\nTip: Review all parameters and request body schemas for conflicting constraints"

    return Unsatisfiable(message)


class UnsatisfiableSchema(Unsatisfiable):
    """A schema that admits no value at all, as opposed to one Hypothesis gave up on."""


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


@dataclass(slots=True)
class SlowParameter:
    """Information about a parameter with slow or problematic data generation."""

    location: ParameterLocation
    name: str
    schema: JsonSchema
    original: HealthCheck

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
    elif (
        "large_base_example" in message
        or "because min_size is larger than hypothesis supports" in message
        or (isinstance(exc, InvalidArgument) and message.endswith("larger than hypothesis is designed to handle"))
    ):
        return HealthCheck.large_base_example

    return None


def find_slow_parameter(operation: APIOperation, reason: HealthCheck) -> SlowParameter | None:
    from hypothesis.errors import FailedHealthCheck

    for location, container in (
        (ParameterLocation.QUERY, operation.query),
        (ParameterLocation.PATH, operation.path_parameters),
        (ParameterLocation.HEADER, operation.headers),
        (ParameterLocation.COOKIE, operation.cookies),
        (ParameterLocation.BODY, operation.body),
    ):
        for parameter in container:
            try:
                generate_one(_parameter_strategy(operation, parameter, location), suppress_health_check=[])
            except (FailedHealthCheck, Unsatisfiable, InvalidArgument, InvalidSchema):
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
