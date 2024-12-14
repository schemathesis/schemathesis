from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core import SCHEMATHESIS_TEST_CASE_HEADER
from schemathesis.core.transport import USER_AGENT

if TYPE_CHECKING:
    from requests.structures import CaseInsensitiveDict

    from schemathesis.models import Case


def prepare_headers(case: Case, headers: dict[str, str] | None = None) -> CaseInsensitiveDict:
    from requests.structures import CaseInsensitiveDict

    final_headers = case.headers.copy() if case.headers is not None else CaseInsensitiveDict()
    if headers:
        final_headers.update(headers)
    final_headers.setdefault("User-Agent", USER_AGENT)
    final_headers.setdefault(SCHEMATHESIS_TEST_CASE_HEADER, case.id)
    return final_headers
