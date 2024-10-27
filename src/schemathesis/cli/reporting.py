from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING, Generator, Iterator

from ..runner.models import Check, deduplicate_failures
from ..sanitization import sanitize_value

if TYPE_CHECKING:
    from ..code_samples import CodeSampleStyle

TEST_CASE_ID_TITLE = "Test Case ID"


def group_by_case(
    checks: list[Check], code_sample_style: CodeSampleStyle
) -> Generator[tuple[str, Iterator[Check]], None, None]:
    checks = deduplicate_failures(checks)
    checks = sorted(checks, key=lambda c: _by_unique_key(c, code_sample_style))
    for (sample, _, _), gen in groupby(checks, lambda c: _by_unique_key(c, code_sample_style)):
        yield (sample, gen)


def _by_unique_key(check: Check, code_sample_style: CodeSampleStyle) -> tuple[str, int, bytes]:
    data = check.prepare_code_sample_data()

    headers = None
    if check.case.headers is not None:
        headers = dict(check.case.headers)
        if check.case.operation.schema.sanitize_output:
            sanitize_value(headers)

    return (
        code_sample_style.generate(
            method=check.case.method,
            url=data.url,
            body=data.body,
            headers=headers,
            verify=check.response.verify if check.response is not None else True,
            extra_headers=data.headers,
        ),
        0 if not check.response else check.response.status_code,
        b"SCHEMATHESIS-INTERNAL-NO-RESPONSE"
        if not check.response
        else check.response.body or b"SCHEMATHESIS-INTERNAL-EMPTY-BODY",
    )
