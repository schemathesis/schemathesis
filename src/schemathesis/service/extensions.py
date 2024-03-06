from __future__ import annotations

import base64
from ipaddress import IPv4Network, IPv6Network
from typing import TYPE_CHECKING, Callable, Optional

from ..internal.result import Err, Ok, Result
from .models import (
    Extension,
    SchemaOptimizationExtension,
    StrategyDefinition,
    StringFormatsExtension,
    Success,
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
        elif isinstance(extension, SchemaOptimizationExtension):
            _apply_schema_optimization_extension(extension, schema)


def _apply_string_formats_extension(extension: StringFormatsExtension) -> None:
    from ..specs.openapi import formats

    for name, value in extension.formats.items():
        strategy = strategy_from_definitions(value)
        # if True:
        #    extension.set_state(Error(message="Unsupported string format extension"))
        #    continue
        formats.register(name, strategy)
        extension.set_state(Success())


def _validate_sampled_from(elements: list[str]) -> Result[None, ValueError]:
    if not elements:
        return Err(ValueError("Cannot sample from a length-zero sequence"))
    return Ok(None)


def _find_built_in_strategy(name: str) -> Optional[st.SearchStrategy]:
    """Find a built-in Hypothesis strategy by its name."""
    from hypothesis import provisional as pr
    from hypothesis import strategies as st

    for module in (st, pr):
        if hasattr(module, name):
            return getattr(module, name)
    return None


def _apply_schema_optimization_extension(extension: SchemaOptimizationExtension, schema: BaseSchema) -> None:
    """Update the schema with its optimized version."""
    schema.raw_schema = extension.schema
    extension.set_state(Success())


def strategy_from_definitions(definitions: list[StrategyDefinition]) -> st.SearchStrategy:
    from ..utils import combine_strategies

    strategies = []
    for definition in definitions:
        strategies.append(_strategy_from_definition(definition))
    return combine_strategies(strategies)


KNOWN_ARGUMENTS = {
    "IPv4Network": IPv4Network,
    "IPv6Network": IPv6Network,
}


def _strategy_from_definition(definition: StrategyDefinition) -> st.SearchStrategy:
    base = _find_built_in_strategy(definition.name)
    if base is None:
        raise ValueError(f"Unknown builtin strategy: {definition.name}")
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

    return strategy


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
