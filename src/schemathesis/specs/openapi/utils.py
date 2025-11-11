from __future__ import annotations

import string
from collections.abc import Generator
from itertools import chain, product


def expand_status_code(status_code: str | int) -> Generator[int, None, None]:
    chars = [list(string.digits) if digit == "X" else [digit] for digit in str(status_code).upper()]
    for expanded in product(*chars):
        yield int("".join(expanded))


def expand_status_codes(status_codes: list[str]) -> set[int]:
    return set(chain.from_iterable(expand_status_code(code) for code in status_codes))
