from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING

from ..exceptions import format_exception
from ..internal.result import Err, Ok, Result
from .models import Extension, StringFormatsExtension, SchemaOptimizationExtension, Success, Error


if TYPE_CHECKING:
    from hypothesis import strategies as st


def apply(extensions: list[Extension]) -> None:
    """Apply the given extensions."""
    for extension in extensions:
        if isinstance(extension, StringFormatsExtension):
            _apply_string_formats_extension(extension)
        elif isinstance(extension, SchemaOptimizationExtension):
            # TODO:Update schema
            pass


def _apply_string_formats_extension(extension: StringFormatsExtension) -> None:
    from ..specs.openapi import formats
    from hypothesis import strategies as st

    for name, value in extension.formats.items():
        if "builtin" in value:
            key = value["builtin"]
            strat = _find_built_in_strategy(key)
            if strat is None:
                extension.set_state(Error(message=f"Unknown builtin strategy: `{key}`"))
                continue
            strategy = strat().map(str)
        elif "regex" in value:
            regex = value["regex"]
            try:
                re.compile(regex)
            except re.error as exc:
                extension.set_state(Error(message=f"Invalid regex: `{regex}`", exception=format_exception(exc)))
                continue
            strategy = st.from_regex(regex)
            if "samples" in value:
                samples = value["samples"]
                validated = _validate_sampled_from(samples)
                if isinstance(validated, Err):
                    extension.set_state(Error(message=str(validated.err())))
                    continue
                strategy |= st.sampled_from(samples)
        elif "samples" in value:
            samples = value["samples"]
            validated = _validate_sampled_from(samples)
            if isinstance(validated, Err):
                extension.set_state(Error(message=str(validated.err())))
                continue
            strategy = st.sampled_from(samples)
        else:
            extension.set_state(Error(message="Unsupported string format extension"))
            continue
        formats.register(name, strategy)
        extension.set_state(Success())


def _validate_sampled_from(elements: list[str]) -> Result[None, ValueError]:
    if not elements:
        return Err(ValueError("Cannot sample from a length-zero sequence"))
    return Ok(None)


def _find_built_in_strategy(name: str) -> Optional[st.SearchStrategy]:
    """Find a built-in Hypothesis strategy by its name."""
    from hypothesis import strategies as st, provisional as pr

    for module in (st, pr):
        if hasattr(module, name):
            return getattr(module, name)
    return None
