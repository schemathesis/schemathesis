from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hypothesis import strategies as st


STRING_FORMATS: dict[str, st.SearchStrategy] = {}


def register_string_format(name: str, strategy: st.SearchStrategy) -> None:
    """Register a new strategy for generating data for specific string "format".

    :param str name: Format name. It should correspond the one used in the API schema as the "format" keyword value.
    :param strategy: Hypothesis strategy you'd like to use to generate values for this format.
    """
    from hypothesis.strategies import SearchStrategy

    if not isinstance(name, str):
        raise TypeError(f"name must be of type {str}, not {type(name)}")
    if not isinstance(strategy, SearchStrategy):
        raise TypeError(f"strategy must be of type {SearchStrategy}, not {type(strategy)}")

    STRING_FORMATS[name] = strategy


def unregister_string_format(name: str) -> None:
    """Remove format strategy from the registry."""
    try:
        del STRING_FORMATS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown Open API format: {name}") from exc


register = register_string_format
unregister = unregister_string_format
