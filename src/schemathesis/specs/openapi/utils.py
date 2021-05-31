import string
from itertools import product
from typing import Any, Callable, Dict, Generator, Union


def expand_status_code(status_code: Union[str, int]) -> Generator[int, None, None]:
    chars = [list(string.digits) if digit == "X" else [digit] for digit in str(status_code).upper()]
    for expanded in product(*chars):
        yield int("".join(expanded))


def set_keyword_on_properties(
    schema: Dict[str, Any], keyword: str, value: Any, predicate: Callable[[Dict[str, Any]], bool]
) -> None:
    """Set JSON Schema keyword on all properties in the schema.

    Useful if all properties should have the same keyword. No-op if this keyword is already set.
    """
    for sub_schema in schema.get("properties", {}).values():
        if predicate(sub_schema):
            sub_schema.setdefault(keyword, value)


def is_header_location(location: str) -> bool:
    """Whether this location affects HTTP headers."""
    return location in ("header", "cookie")
