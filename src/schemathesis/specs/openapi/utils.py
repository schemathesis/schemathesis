import string
from itertools import product
from typing import Generator, Union


def expand_status_code(status_code: Union[str, int]) -> Generator[int, None, None]:
    chars = [list(string.digits) if digit == "X" else [digit] for digit in str(status_code).upper()]
    for expanded in product(*chars):
        yield int("".join(expanded))


def is_header_location(location: str) -> bool:
    """Whether this location affects HTTP headers."""
    return location in ("header", "cookie")
