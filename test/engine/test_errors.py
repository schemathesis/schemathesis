import requests

from schemathesis.config import ConfigError
from schemathesis.core.errors import AuthenticationError
from schemathesis.engine.errors import EngineErrorInfo, deduplicate_errors


def test_config_error_has_no_useful_traceback():
    info = EngineErrorInfo(ConfigError("boom"))
    assert info.has_useful_traceback is False
    assert info.title == "Configuration Error"


def test_authentication_error_traceback_visibility():
    assert EngineErrorInfo(AuthenticationError("P", "get", "boom")).has_useful_traceback is False
    assert EngineErrorInfo(AuthenticationError("P", "get", "boom", show_traceback=True)).has_useful_traceback is True


def test_deduplicate_errors():
    errors = [
        requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=808): Max retries exceeded with url: /snapshots/uploads/%5Dw2y%C3%9D (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x795a23db4ce0>: Failed to establish a new connection: [Errno 111] Connection refused'))"
        ),
        requests.exceptions.ConnectionError(
            "HTTPConnectionPool(host='127.0.0.1', port=808): Max retries exceeded with url: /snapshots/uploads/%C3%8BEK (Caused by NewConnectionError('<urllib3.connection.HTTPConnection object at 0x795a23e2a6c0>: Failed to establish a new connection: [Errno 111] Connection refused'))"
        ),
    ]
    assert len(list(deduplicate_errors(errors))) == 1
