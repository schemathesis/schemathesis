from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from schemathesis.core.error_feedback.store import ParameterPath
from schemathesis.core.parameters import ParameterLocation

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.types import JsonValue


class OperatorKind(str, Enum):
    # Swaps the JSON-Schema `type` keyword.
    CHANGE_TYPE = "change_type"
    # Wraps the schema in `not:` to violate one or more keywords at once.
    NEGATE_CONSTRAINTS = "negate_constraints"
    # Drops a name from a `required` list.
    REMOVE_REQUIRED_PROPERTY = "remove_required_property"
    # Keeps the schema valid but rewrites a generated leaf so it fails one keyword
    # (UUID near-miss, off-by-one numeric, pattern violation).
    VALUE_VIOLATOR = "value_violator"
    # Replaces the body with random bytes that aren't valid JSON.
    SYNTAX_FUZZING = "syntax_fuzzing"


class MutationChannel(str, Enum):
    """Where a mutation lives in the per-case pipeline."""

    SCHEMA = "schema"
    VALUE = "value"


@dataclass(slots=True)
class Mutation:
    """One mutation applied during a negative-fuzzing case.

    Records the schema/value alteration so callers can attribute the case to a
    specific path, operator, and keyword set.
    """

    path: ParameterPath
    parameter_location: ParameterLocation
    schema_pointer: str
    channel: MutationChannel
    operator: OperatorKind
    keywords: tuple[str, ...]
    parameter: str | None
    original_value: JsonValue | None
    new_value: JsonValue | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": list(self.path),
            "parameter_location": self.parameter_location.value,
            "schema_pointer": self.schema_pointer,
            "channel": self.channel.value,
            "operator": self.operator.value,
            "keywords": list(self.keywords),
            "parameter": self.parameter,
            "original_value": self.original_value,
            "new_value": self.new_value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mutation:
        return cls(
            path=tuple(data["path"]),
            parameter_location=ParameterLocation(data.get("parameter_location")),
            schema_pointer=data["schema_pointer"],
            channel=MutationChannel(data["channel"]),
            operator=OperatorKind(data["operator"]),
            keywords=tuple(data["keywords"]),
            parameter=data["parameter"],
            original_value=data["original_value"],
            new_value=data["new_value"],
        )


def _render_mutation_value(value: JsonValue, *, bare: bool) -> str:
    """Render a mutation's before/after value for display in a failure message.

    `bare=True` skips string quoting; use it for `type`-keyword mutations where the
    value is a type name (`object`, `integer`) rather than a string literal.
    """
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, str) and not bare:
        return f'"{value}"'
    return str(value)


def _render_mutation_description(mutation: Mutation) -> str:
    """Render a single mutation as `violates <keywords> [at <pointer>] [(was X[, became Y])]`."""
    if mutation.operator == OperatorKind.SYNTAX_FUZZING:
        # Random bytes violate no keyword, so the operator alone carries the message.
        return "Invalid syntax: random bytes"
    keywords = ", ".join(f"`{k}`" for k in mutation.keywords)
    message = f"violates {keywords}"
    if mutation.schema_pointer:
        message += f" at {mutation.schema_pointer}"
    bare = mutation.keywords == ("type",)
    parts: list[str] = []
    if mutation.original_value is not None:
        parts.append(f"was {_render_mutation_value(mutation.original_value, bare=bare)}")
    # Dict `new_value` would dump the entire mutated body into the failure message; skip it.
    if mutation.new_value is not None and not isinstance(mutation.new_value, dict):
        parts.append(f"became {_render_mutation_value(mutation.new_value, bare=bare)}")
    if parts:
        message += f" ({', '.join(parts)})"
    return message


def render_mutations(mutations: Sequence[Mutation]) -> list[str]:
    """One line per mutation, each naming its location when the case spans several."""
    spans_locations = len({mutation.parameter_location for mutation in mutations}) > 1
    lines = []
    for mutation in mutations:
        description = _render_mutation_description(mutation)
        label = mutation.parameter_location.value if spans_locations else None
        lines.append(f"{label}: {description}" if label else description)
    return lines
