from __future__ import annotations

import base64
import re
from ipaddress import IPv4Network, IPv6Network
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..graphql import nodes
from ..internal.result import Err, Ok, Result
from .models import (
    Extension,
    GraphQLScalarsExtension,
    MediaTypesExtension,
    OpenApiStringFormatsExtension,
    SchemaPatchesExtension,
    StrategyDefinition,
    TransformFunctionDefinition,
)

if TYPE_CHECKING:
    from datetime import date, datetime

    from hypothesis import strategies as st

    from ..schemas import BaseSchema


def apply(extensions: list[Extension], schema: BaseSchema) -> None:
    """Apply the given extensions."""
    for extension in extensions:
        if isinstance(extension, OpenApiStringFormatsExtension):
            _apply_string_formats_extension(extension)
        elif isinstance(extension, GraphQLScalarsExtension):
            _apply_scalars_extension(extension)
        elif isinstance(extension, MediaTypesExtension):
            _apply_media_types_extension(extension)
        elif isinstance(extension, SchemaPatchesExtension):
            _apply_schema_patches_extension(extension, schema)


def _apply_simple_extension(
    extension: OpenApiStringFormatsExtension | GraphQLScalarsExtension | MediaTypesExtension,
    collection: dict[str, Any],
    register_strategy: Callable[[str, st.SearchStrategy], None],
) -> None:
    errors = []
    for name, value in collection.items():
        strategy = strategy_from_definitions(value)
        if isinstance(strategy, Err):
            errors.append(str(strategy.err()))
        else:
            register_strategy(name, strategy.ok())

    if errors:
        extension.set_error(errors=errors)
    else:
        extension.set_success()


def _apply_string_formats_extension(extension: OpenApiStringFormatsExtension) -> None:
    from ..specs.openapi import formats

    _apply_simple_extension(extension, extension.formats, formats.register)


def _apply_scalars_extension(extension: GraphQLScalarsExtension) -> None:
    from ..specs.graphql import scalars

    _apply_simple_extension(extension, extension.scalars, scalars.scalar)


def _apply_media_types_extension(extension: MediaTypesExtension) -> None:
    from ..specs.openapi import media_types

    _apply_simple_extension(extension, extension.media_types, media_types.register_media_type)


def _find_built_in_strategy(name: str) -> Optional[st.SearchStrategy]:
    """Find a built-in Hypothesis strategy by its name."""
    from hypothesis import provisional as pr
    from hypothesis import strategies as st

    for module in (st, pr):
        if hasattr(module, name):
            return getattr(module, name)
    return None


def _apply_schema_patches_extension(extension: SchemaPatchesExtension, schema: BaseSchema) -> None:
    """Apply a set of patches to the schema."""
    for patch in extension.patches:
        current: dict[str, Any] | list = schema.raw_schema
        operation = patch["operation"]
        path = patch["path"]
        for part in path[:-1]:
            if isinstance(current, dict):
                if not isinstance(part, str):
                    extension.set_error([f"Invalid path: {path}"])
                    return
                current = current.setdefault(part, {})
            elif isinstance(current, list):
                if not isinstance(part, int):
                    extension.set_error([f"Invalid path: {path}"])
                    return
                try:
                    current = current[part]
                except IndexError:
                    extension.set_error([f"Invalid path: {path}"])
                    return
        if operation == "add":
            # Add or replace the value at the target location.
            current[path[-1]] = patch["value"]  # type: ignore
        elif operation == "remove":
            # Remove the item at the target location if it exists.
            if path:
                last = path[-1]
                if isinstance(current, dict) and isinstance(last, str) and last in current:
                    del current[last]
                elif isinstance(current, list) and isinstance(last, int) and len(current) > last:
                    del current[last]
                else:
                    extension.set_error([f"Invalid path: {path}"])
                    return
            else:
                current.clear()

    extension.set_success()


def strategy_from_definitions(definitions: list[StrategyDefinition]) -> Result[st.SearchStrategy, Exception]:
    from ..generation import combine_strategies

    strategies = []
    for definition in definitions:
        strategy = _strategy_from_definition(definition)
        if isinstance(strategy, Ok):
            strategies.append(strategy.ok())
        else:
            return strategy
    return Ok(combine_strategies(strategies))


KNOWN_ARGUMENTS = {
    "IPv4Network": IPv4Network,
    "IPv6Network": IPv6Network,
}


def check_regex(regex: str) -> Result[None, Exception]:
    try:
        re.compile(regex)
    except (re.error, OverflowError, RuntimeError):
        return Err(ValueError(f"Invalid regex: `{regex}`"))
    return Ok(None)


def check_sampled_from(elements: list) -> Result[None, Exception]:
    if not elements:
        return Err(ValueError("Invalid input for `sampled_from`: Cannot sample from a length-zero sequence"))
    return Ok(None)


STRATEGY_ARGUMENT_CHECKS = {
    "from_regex": check_regex,
    "sampled_from": check_sampled_from,
}


def _strategy_from_definition(definition: StrategyDefinition) -> Result[st.SearchStrategy, Exception]:
    base = _find_built_in_strategy(definition.name)
    if base is None:
        return Err(ValueError(f"Unknown built-in strategy: `{definition.name}`"))
    arguments = definition.arguments or {}
    arguments = arguments.copy()
    for key, value in arguments.items():
        if isinstance(value, str):
            known = KNOWN_ARGUMENTS.get(value)
            if known is not None:
                arguments[key] = known
    check = STRATEGY_ARGUMENT_CHECKS.get(definition.name)
    if check is not None:
        check_result = check(**arguments)  # type: ignore
        if isinstance(check_result, Err):
            return check_result
    strategy = base(**arguments)
    for transform in definition.transforms or []:
        if transform["kind"] == "map":
            function = _get_map_function(transform)
            if isinstance(function, Ok):
                strategy = strategy.map(function.ok())
            else:
                return function
        else:
            return Err(ValueError(f"Unknown transform kind: {transform['kind']}"))

    return Ok(strategy)


def make_strftime(format: str) -> Callable:
    def strftime(value: date | datetime) -> str:
        return value.strftime(format)

    return strftime


def _get_map_function(definition: TransformFunctionDefinition) -> Result[Callable | None, Exception]:
    from ..specs.openapi._hypothesis import Binary

    TRANSFORM_FACTORIES: dict[str, Callable] = {
        "str": lambda: str,
        "base64_encode": lambda: lambda x: Binary(base64.b64encode(x)),
        "base64_decode": lambda: lambda x: Binary(base64.b64decode(x)),
        "urlsafe_base64_encode": lambda: lambda x: Binary(base64.urlsafe_b64encode(x)),
        "strftime": make_strftime,
        "GraphQLBoolean": lambda: nodes.Boolean,
        "GraphQLFloat": lambda: nodes.Float,
        "GraphQLInt": lambda: nodes.Int,
        "GraphQLString": lambda: nodes.String,
    }
    factory = TRANSFORM_FACTORIES.get(definition["name"])
    if factory is None:
        return Err(ValueError(f"Unknown transform: {definition['name']}"))
    arguments = definition.get("arguments", {})
    return Ok(factory(**arguments))
