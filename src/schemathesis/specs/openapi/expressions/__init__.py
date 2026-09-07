"""Runtime expressions support.

https://swagger.io/docs/specification/links/#runtime-expressions
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from schemathesis.core.deserialization import DeserializationContext, deserialize_response
from schemathesis.core.transforms import UNRESOLVABLE, Unresolvable, resolve_pointer_all
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.specs.openapi.expressions import lexer, nodes, parser
from schemathesis.specs.openapi.expressions.nodes import BodyResponse

__all__ = ["MultiMatch", "lexer", "nodes", "parser"]


@dataclass(slots=True)
class MultiMatch:
    """Multiple wildcard candidates; the substitution layer picks one via `draw(sampled_from(...))`."""

    values: list[Any]


def evaluate(expr: Any, output: StepOutput, evaluate_nested: bool = False) -> Any:
    """Evaluate runtime expression in context."""
    if isinstance(expr, (dict, list)) and evaluate_nested:
        return _evaluate_nested(expr, output)
    if not isinstance(expr, str):
        # Can be a non-string constant
        return expr
    parts = [node.evaluate(output) for node in parser.parse(expr)]
    if len(parts) == 1:
        return parts[0]  # keep the return type the same as the internal value type
    if any(isinstance(part, Unresolvable) for part in parts):
        return UNRESOLVABLE
    return "".join(str(part) for part in parts if part is not None)


def evaluate_wildcard(expr: Any, output: StepOutput) -> Any:
    """Evaluate a runtime expression, treating `*` segments in JSON Pointers as wildcards.

    Returns the single resolved value on one match, a `MultiMatch` on N>1 matches
    (the substitution layer picks via Hypothesis `sampled_from`), or UNRESOLVABLE
    on zero matches or structural failure.

    Inference produces single-node `$response.body#/.../*/...` expressions; that's
    the only shape this function is contracted to handle.
    """
    if not isinstance(expr, str) or "/*" not in expr:
        return evaluate(expr, output)
    try:
        [node] = parser.parse(expr)
    except ValueError:
        return UNRESOLVABLE
    if not isinstance(node, BodyResponse) or node.pointer is None:
        return UNRESOLVABLE
    response = output.response
    content_type = response.headers.get("content-type", ["application/json"])[0]
    context = DeserializationContext(operation=output.case.operation, case=output.case)
    document = deserialize_response(response, content_type, context=context)
    matches = resolve_pointer_all(document, node.pointer[1:])
    if isinstance(matches, list):
        if len(matches) == 1:
            return matches[0]
        if matches:
            return MultiMatch(matches)
    return UNRESOLVABLE


def _evaluate_nested(expr: dict[str, Any] | list, output: StepOutput) -> Any:
    if isinstance(expr, dict):
        result_dict = {}
        for key, value in expr.items():
            new_key = _evaluate_object_key(key, output)
            if new_key is UNRESOLVABLE:
                return new_key
            new_value = evaluate(value, output, evaluate_nested=True)
            if new_value is UNRESOLVABLE:
                return new_value
            result_dict[new_key] = new_value
        return result_dict
    result_list = []
    for item in expr:
        new_value = evaluate(item, output, evaluate_nested=True)
        if new_value is UNRESOLVABLE:
            return new_value
        result_list.append(new_value)
    return result_list


def evaluate_nested_wildcard(expr: dict[str, Any] | list, output: StepOutput) -> Any:
    """Wildcard-aware variant of _evaluate_nested.

    Routes string leaves through `evaluate_wildcard` so `$response.body#/.../*/...`
    expressions land a `MultiMatch` instead of `UNRESOLVABLE`. Used by inferred
    links produced by dependency analysis.
    """
    if isinstance(expr, dict):
        result_dict: dict[str, Any] = {}
        for key, value in expr.items():
            new_key = _evaluate_object_key(key, output)
            if new_key is UNRESOLVABLE:
                return new_key
            if isinstance(value, (dict, list)):
                new_value = evaluate_nested_wildcard(value, output)
            else:
                new_value = evaluate_wildcard(value, output)
            if new_value is UNRESOLVABLE:
                return new_value
            result_dict[new_key] = new_value
        return result_dict
    result_list: list[Any] = []
    for item in expr:
        if isinstance(item, (dict, list)):
            new_value = evaluate_nested_wildcard(item, output)
        else:
            new_value = evaluate_wildcard(item, output)
        if new_value is UNRESOLVABLE:
            return new_value
        result_list.append(new_value)
    return result_list


def _evaluate_object_key(key: str, output: StepOutput) -> Any:
    evaluated = evaluate(key, output)
    if evaluated is UNRESOLVABLE:
        return evaluated
    if isinstance(evaluated, str):
        return evaluated
    if isinstance(evaluated, bool):
        return "true" if evaluated else "false"
    if isinstance(evaluated, int | float):
        return str(evaluated)
    if evaluated is None:
        return "null"
    return json.dumps(evaluated)
