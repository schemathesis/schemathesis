from __future__ import annotations

import http.client
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, cast

from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt
from tenacity.wait import wait_base

from schemathesis.config._retry import RequestRetryConfig, RetryExceptionKind, RetryJitter
from schemathesis.core.transport import Response

try:
    import urllib3
except ModuleNotFoundError:  # pragma: no cover
    urllib3 = cast(Any, None)


class RetryableHTTPStatus(Exception):
    """Internal control exception used to re-execute a request on specific HTTP statuses."""

    def __init__(self, response: Response, *, retry_after: float | None = None):
        super().__init__(f"Retry due to HTTP {response.status_code}")
        self.response = response
        self.retry_after = retry_after


class RequestRetryWait(wait_base):
    """Tenacity wait strategy that implements exponential backoff with optional jitter."""

    def __init__(self, config: RequestRetryConfig):
        self.config = config

    def __call__(self, retry_state: RetryCallState) -> float:
        attempt_index = max(retry_state.attempt_number - 1, 0)
        exception = retry_state.outcome.exception()
        retry_after = exception.retry_after if isinstance(exception, RetryableHTTPStatus) else None
        return _compute_delay(self.config, attempt_index, retry_after)


def execute_with_retry(
    func: Callable[[], Response],
    *,
    config: RequestRetryConfig,
    method: str,
    allow_exception_retry: bool,
    on_retry: Callable[[int, int, float, BaseException], None] | None = None,
) -> Response:
    """Execute callable respecting the configured retry policy."""

    if not config.is_enabled or config.max_attempts <= 1:
        return func()

    return _execute_with_tenacity(
        func,
        config=config,
        method=method,
        allow_exception_retry=allow_exception_retry,
        on_retry=on_retry,
    )


def _retry_after_seconds(response: Response) -> float | None:
    values = response.headers.get("retry-after")
    if not values:
        return None
    value = values[0].strip()
    if value.isdigit():
        return float(value)

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = (parsed - datetime.now(tz=timezone.utc)).total_seconds()
    return max(delta, 0.0)


def _exception_types_for(kinds: Iterable[RetryExceptionKind]) -> tuple[type[BaseException], ...]:
    import requests

    mapping: list[type[BaseException]] = []
    for kind in kinds:
        if kind is RetryExceptionKind.CONNECTION:
            mapping.append(requests.exceptions.ConnectionError)
        elif kind is RetryExceptionKind.TIMEOUT:
            mapping.append(requests.exceptions.Timeout)
        elif kind is RetryExceptionKind.READ:
            mapping.extend(
                [
                    requests.exceptions.ReadTimeout,
                    requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ContentDecodingError,
                    http.client.IncompleteRead,
                ]
            )
            if urllib3 is not None:
                protocol_error = getattr(urllib3.exceptions, "ProtocolError", None)
                if protocol_error is not None:
                    mapping.append(protocol_error)
    return tuple({cls for cls in mapping})


def _execute_with_tenacity(
    func: Callable[[], Response],
    *,
    config: RequestRetryConfig,
    method: str,
    allow_exception_retry: bool,
    on_retry: Callable[[int, int, float, BaseException], None] | None,
) -> Response:
    retry_condition = retry_if_exception_type(RetryableHTTPStatus)

    exception_types: tuple[type[BaseException], ...] = ()
    if allow_exception_retry and config.has_exception_retry():
        exception_types = _exception_types_for(config.retry_on_exceptions)
        if exception_types:
            retry_condition = retry_condition | retry_if_exception_type(exception_types)

    wait_strategy = RequestRetryWait(config)

    def before_sleep(retry_state: RetryCallState) -> None:
        if on_retry is None:
            return
        exception = retry_state.outcome.exception()
        if exception is None:
            return
        delay = retry_state.next_action.sleep if retry_state.next_action is not None else 0.0
        on_retry(retry_state.attempt_number, config.max_attempts, delay, exception)

    def call_once_impl() -> Response:
        response = func()
        if config.should_retry_status(response.status_code, method):
            retry_after = _retry_after_seconds(response)
            raise RetryableHTTPStatus(response, retry_after=retry_after)
        return response

    call_once = retry(
        retry=retry_condition,
        wait=wait_strategy,
        stop=stop_after_attempt(config.max_attempts),
        before_sleep=before_sleep,
        reraise=True,
    )(call_once_impl)

    try:
        return call_once()
    except RetryableHTTPStatus as exc:
        return exc.response


def _compute_delay(config: RequestRetryConfig, attempt_index: int, retry_after: float | None) -> float:
    delay = config.wait_initial * (config.backoff_multiplier**attempt_index)
    if config.max_wait is not None:
        delay = min(delay, config.max_wait)
    if retry_after is not None and config.respect_retry_after:
        delay = max(delay, retry_after)
    if config.jitter is RetryJitter.FULL and delay > 0:
        delay = random.uniform(0, delay)
    return max(delay, 0.0)
