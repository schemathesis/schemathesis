from __future__ import annotations

import re
from itertools import chain
from typing import TYPE_CHECKING

from packaging import version

from schemathesis.core.transport import expand_status_code

if TYPE_CHECKING:
    from schemathesis.core.transport import StatusCodePattern

_NUMERIC_PREFIX = re.compile(r"\d+(?:\.\d+)*")
# Sorts below every known spec version, so unrecognized values fall back to the oldest handling.
_UNKNOWN_VERSION = version.parse("0")


def parse_spec_version(value: str) -> version.Version:
    """Parse the numeric part of a spec version, ignoring any suffix the spec allows."""
    match = _NUMERIC_PREFIX.match(value)
    if match is None:
        return _UNKNOWN_VERSION
    return version.parse(match.group())


def expand_status_codes(status_codes: list[StatusCodePattern]) -> set[int]:
    return set(chain.from_iterable(expand_status_code(code) for code in status_codes))
