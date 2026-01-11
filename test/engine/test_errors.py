import requests

from schemathesis.core.errors import SerializationNotPossible
from schemathesis.engine.errors import deduplicate_errors, is_unrecoverable_network_error


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


def test_deduplicate_errors_with_serialization_not_possible():
    errors = [
        SerializationNotPossible.from_media_types("text/csv", "application/xml"),
        SerializationNotPossible.from_media_types("text/tsv"),
        SerializationNotPossible.from_media_types("application/json"),
    ]
    deduplicated = list(deduplicate_errors(errors))
    assert len(deduplicated) == 1
    assert isinstance(deduplicated[0], SerializationNotPossible)
    assert set(deduplicated[0].media_types) == {"text/csv", "application/xml", "text/tsv", "application/json"}


def test_is_unrecoverable_network_error_chunked_encoding():
    import requests.exceptions

    error = requests.exceptions.ChunkedEncodingError("Connection broken")
    assert is_unrecoverable_network_error(error) is True


def test_is_unrecoverable_network_error_timeout():
    error = requests.Timeout("Read timed out")
    assert is_unrecoverable_network_error(error) is True
