from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from schemathesis.core.transport import expand_status_code

if TYPE_CHECKING:
    from schemathesis.core.transport import StatusCodePattern


def expand_status_codes(status_codes: list[StatusCodePattern]) -> set[int]:
    return set(chain.from_iterable(expand_status_code(code) for code in status_codes))
