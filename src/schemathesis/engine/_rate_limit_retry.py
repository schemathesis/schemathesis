from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure, FailureGroup
from schemathesis.core.rate_limit import (
    RATE_LIMIT_AUTO_MAX_RETRIES,
    RATE_LIMIT_AUTO_REPORT_THRESHOLD,
    parse_retry_after,
)

if TYPE_CHECKING:
    from schemathesis.core.transport import Response


def _get_retry_after(response: Response) -> str:
    value = response.headers.get("retry-after") or response.headers.get("Retry-After")
    if isinstance(value, list):
        return value[0] if value else ""
    return value or ""


def call_with_retry(
    *,
    call_fn: Callable[[], Response],
    auto_mode: bool,
    on_delay: Callable[[float, int], None],
) -> tuple[list[Response], Response]:
    """Call an API, retrying on 429 + Retry-After.

    Returns (rate_limited_responses, final_response).
    rate_limited_responses contains each 429 received before the final response.
    """
    retries_left = RATE_LIMIT_AUTO_MAX_RETRIES
    rate_limited: list[Response] = []

    while True:
        response = call_fn()
        if response.status_code == 429 and auto_mode and retries_left > 0:
            delay = parse_retry_after(_get_retry_after(response))
            if delay is not None:
                rate_limited.append(response)
                retries_left -= 1
                if delay >= RATE_LIMIT_AUTO_REPORT_THRESHOLD:
                    on_delay(delay, retries_left)
                time.sleep(delay)
                continue
        break

    return rate_limited, response


def call_and_validate_with_retry(
    *,
    call_fn: Callable[[], Response],
    validate_fn: Callable[[Response], None],
    auto_mode: bool,
    on_delay: Callable[[float, int], None],
) -> Response:
    """Call and validate an API response, retrying on 429 + Retry-After.

    All 429 responses are validated through the normal check pipeline.
    Failures from 429s are merged with failures from the final response.
    """
    rate_limited, final = call_with_retry(call_fn=call_fn, auto_mode=auto_mode, on_delay=on_delay)
    pending_failures: list[Failure] = []

    for response in rate_limited:
        try:
            validate_fn(response)
        except FailureGroup as exc:
            pending_failures.extend(f for f in exc.exceptions if isinstance(f, Failure))

    try:
        validate_fn(final)
    except FailureGroup as exc:
        if pending_failures:
            final_failures = [f for f in exc.exceptions if isinstance(f, Failure)]
            raise FailureGroup(pending_failures + final_failures) from None
        raise

    if pending_failures:
        raise FailureGroup(pending_failures)
    return final
