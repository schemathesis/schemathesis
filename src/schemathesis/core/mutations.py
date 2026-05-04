from __future__ import annotations

from enum import Enum


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
