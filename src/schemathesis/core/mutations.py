from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from schemathesis.core.transforms import JsonValue


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

    path: tuple[str | int, ...]
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
            schema_pointer=data["schema_pointer"],
            channel=MutationChannel(data["channel"]),
            operator=OperatorKind(data["operator"]),
            keywords=tuple(data["keywords"]),
            parameter=data["parameter"],
            original_value=data["original_value"],
            new_value=data["new_value"],
        )
