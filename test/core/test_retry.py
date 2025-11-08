import pytest
import requests

from schemathesis.config._retry import RequestRetryConfig
from schemathesis.core.retry import execute_with_retry
from schemathesis.core.transport import Response


def make_response(status: int, retry_after: str | None = None) -> Response:
    request = requests.Request("GET", "http://example.com").prepare()
    headers: dict[str, list[str]] = {}
    if retry_after is not None:
        headers["retry-after"] = [retry_after]
    return Response(
        status_code=status,
        headers=headers,
        content=b"",
        request=request,
        elapsed=0.0,
        verify=True,
    )


def test_status_retry_succeeds():
    attempts = iter([503, 200])
    config = RequestRetryConfig.from_dict(
        {"enabled": True, "status-forcelist": [503], "retry-on": [], "wait-initial": 0.01, "max-wait": 0.02}
    )

    def call():
        return make_response(next(attempts))

    result = execute_with_retry(call, config=config, method="GET", allow_exception_retry=False)
    assert result.status_code == 200


def test_status_retry_returns_last_response_when_exhausted():
    config = RequestRetryConfig.from_dict(
        {"status-forcelist": [503], "max-attempts": 2, "retry-on": [], "wait-initial": 0.01, "max-wait": 0.02}
    )

    result = execute_with_retry(lambda: make_response(503), config=config, method="GET", allow_exception_retry=False)
    assert result.status_code == 503


def test_connection_error_retry():
    counter = {"value": 0}
    config = RequestRetryConfig.from_dict({"enabled": True, "max-attempts": 3, "wait-initial": 0.01, "max-wait": 0.02})

    def call():
        counter["value"] += 1
        if counter["value"] < 3:
            raise requests.exceptions.ConnectionError("boom")
        return make_response(200)

    result = execute_with_retry(call, config=config, method="GET", allow_exception_retry=True)
    assert counter["value"] == 3
    assert result.status_code == 200


def test_connection_error_not_retried_when_disabled():
    config = RequestRetryConfig.from_dict({"enabled": True, "max-attempts": 3, "wait-initial": 0.01, "max-wait": 0.02})

    def call():
        raise requests.exceptions.ConnectionError("boom")

    with pytest.raises(requests.exceptions.ConnectionError):
        execute_with_retry(call, config=config, method="GET", allow_exception_retry=False)


def test_retry_callback_invoked():
    calls: list[tuple[int, int]] = []
    config = RequestRetryConfig.from_dict({"enabled": True, "max-attempts": 2, "wait-initial": 0.01, "max-wait": 0.02})

    def call():
        raise requests.exceptions.ConnectionError("boom")

    with pytest.raises(requests.exceptions.ConnectionError):
        execute_with_retry(
            call,
            config=config,
            method="GET",
            allow_exception_retry=True,
            on_retry=lambda attempt, max_attempts, delay, exc: calls.append((attempt, max_attempts)),
        )

    assert calls == [(1, 2)]
