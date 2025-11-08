import pytest

from schemathesis.config import ConfigError
from schemathesis.config._operations import OperationConfig
from schemathesis.config._projects import ProjectConfig
from schemathesis.config._retry import RequestRetryConfig, RetryExceptionKind


def test_request_retry_defaults_disabled():
    project = ProjectConfig.from_dict({"title": "default", "request-retry": {}})
    retry = project.request_retry
    assert not retry.is_enabled
    assert retry.max_attempts == 3
    assert retry.wait_initial == 0.5
    assert retry.backoff_multiplier == 2.0
    assert retry.methods == ("GET", "HEAD", "OPTIONS")
    assert set(retry.retry_on_exceptions) == {
        RetryExceptionKind.CONNECTION,
        RetryExceptionKind.TIMEOUT,
    }


def test_request_retry_invalid_method():
    with pytest.raises(ConfigError):
        ProjectConfig.from_dict({"title": "default", "request-retry": {"methods": ["INVALID"]}})


def test_request_retry_operation_override():
    project = ProjectConfig(request_retry=RequestRetryConfig(enabled=True, max_attempts=2))
    override = OperationConfig(request_retry=RequestRetryConfig(enabled=True, max_attempts=5, methods=("GET",)))

    project.operations.operations = [override]

    assert project.request_retry_for(operation=None) is project.request_retry

    # Force override by ensuring get_for_operation returns our config
    class OverrideOperations:
        def get_for_operation(self, operation):
            return override

    project.operations = OverrideOperations()  # type: ignore[assignment]
    config = project.request_retry_for(operation=object())
    assert config is override.request_retry
