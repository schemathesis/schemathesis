from __future__ import annotations

import string
from base64 import b64encode
from functools import lru_cache
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


def header_values(blacklist_characters: str = "\n\r") -> st.SearchStrategy[str]:
    from hypothesis import strategies as st

    return st.text(
        alphabet=st.characters(min_codepoint=0, max_codepoint=255, blacklist_characters=blacklist_characters)
        # Header values with leading non-visible chars can't be sent with `requests`
    ).map(str.lstrip)


HEADER_FORMAT = "_header_value"


@lru_cache
def get_default_format_strategies() -> dict[str, st.SearchStrategy]:
    """Get all default "format" strategies."""
    from hypothesis import strategies as st
    from requests.auth import _basic_auth_str

    from ...serializers import Binary

    def make_basic_auth_str(item: tuple[str, str]) -> str:
        return _basic_auth_str(*item)

    latin1_text = st.text(alphabet=st.characters(min_codepoint=0, max_codepoint=255))

    # Define valid characters here to avoid filtering them out in `is_valid_header` later
    header_value = header_values()

    return {
        "binary": st.binary().map(Binary),
        "byte": st.binary().map(lambda x: b64encode(x).decode()),
        # RFC 7230, Section 3.2.6
        "_header_name": st.text(
            min_size=1, alphabet=st.sampled_from("!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters)
        ),
        HEADER_FORMAT: header_value,
        "_basic_auth": st.tuples(latin1_text, latin1_text).map(make_basic_auth_str),
        "_bearer_auth": header_value.map("Bearer {}".format),
    }


register = register_string_format
unregister = unregister_string_format
