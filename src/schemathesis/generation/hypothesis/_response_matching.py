"""Heuristics for matching parameter values against response example shapes."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

NOT_FOUND = object()


def find_matching_in_responses(examples: list[tuple[str, object]], param: str) -> Iterator[Any]:
    """Find matching parameter examples."""
    normalized = param.lower()
    is_id_param = normalized.endswith("id")
    # Extract values from response examples that match input parameters.
    # E.g., for `GET /orders/{id}/`, use "id" or "orderId" from `Order` response
    # as examples for the "id" path parameter.
    for schema_name, example in examples:
        if not isinstance(example, dict):
            continue
        # Unwrapping example from `{"item": [{...}]}`
        inner = next((value for key, value in example.items() if key.lower() == schema_name.lower()), None)
        if inner is not None:
            if isinstance(inner, list):
                for sub_example in inner:
                    if isinstance(sub_example, dict):
                        for found in _find_matching_in_responses(
                            sub_example, schema_name, param, normalized, is_id_param
                        ):
                            if found is not NOT_FOUND:
                                yield found
                continue
            if isinstance(inner, dict):
                example = inner
        for found in _find_matching_in_responses(example, schema_name, param, normalized, is_id_param):
            if found is not NOT_FOUND:
                yield found


def _find_matching_in_responses(
    example: dict[str, Any], schema_name: str, param: str, normalized: str, is_id_param: bool
) -> Iterator[Any]:
    # Check for exact match
    if param in example:
        yield example[param]
        return
    if is_id_param and param[:-2] in example:
        value = example[param[:-2]]
        if isinstance(value, list):
            for sub_example in value:
                for found in _find_matching_in_responses(sub_example, schema_name, param, normalized, is_id_param):
                    if found is not NOT_FOUND:
                        yield found
            return
        else:
            yield value
            return

    # Check for case-insensitive match
    for key in example:
        if key.lower() == normalized:
            yield example[key]
            return
    # If no match found and it's an ID parameter, try additional checks
    if is_id_param:
        # Check for 'id' if parameter is '{something}Id'
        if "id" in example:
            yield example["id"]
            return
        # Check for '{schemaName}Id' or '{schemaName}_id'
        if normalized == "id" or normalized.startswith(schema_name.lower()):
            for key in (schema_name, schema_name.lower()):
                for suffix in ("_id", "Id"):
                    with_suffix = f"{key}{suffix}"
                    if with_suffix in example:
                        yield example[with_suffix]
                        return
