from __future__ import annotations

import base64
from ipaddress import IPv4Network, IPv6Network
from typing import TYPE_CHECKING, Callable, Optional, Any

from ..internal.result import Result, Ok, Err
from .models import (
    Extension,
    SchemaPatchesExtension,
    StrategyDefinition,
    StringFormatsExtension,
    Success,
    Error,
    TransformFunctionDefinition,
)

if TYPE_CHECKING:
    from datetime import date, datetime

    from hypothesis import strategies as st

    from ..schemas import BaseSchema


def apply(extensions: list[Extension], schema: BaseSchema) -> None:
    """Apply the given extensions."""
    for extension in extensions:
        if isinstance(extension, StringFormatsExtension):
            _apply_string_formats_extension(extension)
        elif isinstance(extension, SchemaPatchesExtension):
            _apply_schema_optimization_extension(extension, schema)


def _apply_string_formats_extension(extension: StringFormatsExtension) -> None:
    from ..specs.openapi import formats

    for name, value in extension.formats.items():
        strategy = strategy_from_definitions(value)
        if isinstance(strategy, Err):
            extension.set_state(Error(message=f"Unsupported string format extension: {strategy.err()}"))
            continue
        formats.register(name, strategy.ok())
        extension.set_state(Success())


def _find_built_in_strategy(name: str) -> Optional[st.SearchStrategy]:
    """Find a built-in Hypothesis strategy by its name."""
    from hypothesis import provisional as pr
    from hypothesis import strategies as st

    for module in (st, pr):
        if hasattr(module, name):
            return getattr(module, name)
    return None


def _apply_schema_optimization_extension(extension: SchemaPatchesExtension, schema: BaseSchema) -> None:
    """Apply a set of patches to the schema."""
    for patch in extension.patches:
        current: dict[str, Any] | list = schema.raw_schema
        operation = patch["operation"]
        path = patch["path"]
        for part in path[:-1]:
            if isinstance(current, dict):
                if not isinstance(part, str):
                    extension.set_state(Error(message=f"Invalid path: {path}"))
                    return
                current = current.setdefault(part, {})
            elif isinstance(current, list):
                if not isinstance(part, int):
                    extension.set_state(Error(message=f"Invalid path: {path}"))
                    return
                try:
                    current = current[part]
                except IndexError:
                    extension.set_state(Error(message=f"Invalid path: {path}"))
                    return
        if operation == "add":
            # Add or replace the value at the target location.
            current[path[-1]] = patch["value"]  # type: ignore
        elif operation == "remove":
            # Remove the item at the target location if it exists.
            if path:
                last = path[-1]
                if last in current:
                    if isinstance(current, dict) and isinstance(last, str):
                        del current[last]
                    elif isinstance(current, list) and isinstance(last, int):
                        del current[last]
                    else:
                        extension.set_state(Error(message=f"Invalid path: {path}"))
                        return
            else:
                current.clear()

    extension.set_state(Success())


def strategy_from_definitions(definitions: list[StrategyDefinition]) -> Result[st.SearchStrategy, Exception]:
    from ..utils import combine_strategies

    strategies = []
    for definition in definitions:
        strategy = _strategy_from_definition(definition)
        if isinstance(strategy, Ok):
            strategies.append(strategy.ok())
        elif isinstance(strategy, Err):
            return strategy
    return Ok(combine_strategies(strategies))


KNOWN_ARGUMENTS = {
    "IPv4Network": IPv4Network,
    "IPv6Network": IPv6Network,
}


def _strategy_from_definition(definition: StrategyDefinition) -> Result[st.SearchStrategy, Exception]:
    base = _find_built_in_strategy(definition.name)
    if base is None:
        return Err(ValueError(f"Unknown built-in strategy: {definition.name}"))
    arguments = definition.arguments or {}
    arguments = arguments.copy()
    for key, value in arguments.items():
        known = KNOWN_ARGUMENTS.get(value)
        if known is not None:
            arguments[key] = known
    strategy = base(**arguments)
    for transform in definition.transforms or []:
        function = _get_transform_function(transform)
        if transform["kind"] == "map":
            strategy = strategy.map(function)
        elif transform["kind"] == "filter":
            strategy = strategy.filter(function)

    return Ok(strategy)


def make_strftime(format: str) -> Callable:
    def strftime(value: date | datetime) -> str:
        return value.strftime(format)

    return strftime


def _get_transform_function(definition: TransformFunctionDefinition) -> Callable | None:
    from ..specs.openapi._hypothesis import Binary

    TRANSFORM_FACTORIES: dict[str, Callable] = {
        "str": lambda: str,
        "base64_encode": lambda: lambda x: Binary(base64.b64encode(x)),
        "urlsafe_base64_encode": lambda: lambda x: Binary(base64.urlsafe_b64encode(x)),
        "strftime": make_strftime,
    }
    factory = TRANSFORM_FACTORIES.get(definition["name"])
    if factory is None:
        raise ValueError(f"Unknown transform: {definition['name']}")
    arguments = definition.get("arguments", {})
    return factory(**arguments)
