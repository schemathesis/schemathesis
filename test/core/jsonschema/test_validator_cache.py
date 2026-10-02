import socket

import jsonschema_rs
import pytest

from schemathesis.core.jsonschema import make_validator


def test_failed_build_is_cached_and_reraised():
    schema = {"type": "string", "pattern": "("}
    with pytest.raises(jsonschema_rs.ValidationError) as first:
        make_validator(schema, jsonschema_rs.Draft7Validator)
    with pytest.raises(jsonschema_rs.ValidationError) as second:
        make_validator(schema, jsonschema_rs.Draft7Validator)
    # Same instance => cached, not recompiled.
    assert first.value is second.value


def test_failure_cache_is_keyed_by_schema():
    with pytest.raises(jsonschema_rs.ValidationError):
        make_validator({"type": "string", "pattern": "("}, jsonschema_rs.Draft7Validator)
    assert make_validator({"type": "string", "pattern": "^a$"}, jsonschema_rs.Draft7Validator).is_valid("a")


@pytest.mark.parametrize("keyword", ["unevaluatedProperties", "unevaluatedItems"])
def test_unevaluated_beside_self_reference_builds(keyword):
    validator = make_validator({"$ref": "#", keyword: False}, jsonschema_rs.Draft202012Validator)
    assert validator.is_valid([] if keyword == "unevaluatedItems" else {})


def test_meta_schema_url_is_not_fetched():
    with socket.create_server(("127.0.0.1", 0)) as listener:
        listener.setblocking(False)
        schema = {"$schema": f"http://127.0.0.1:{listener.getsockname()[1]}/meta", "type": "object"}
        assert make_validator(schema, jsonschema_rs.Draft202012Validator).is_valid({})
        # A pending connection means the validator build tried to fetch the meta-schema.
        with pytest.raises(BlockingIOError):
            listener.accept()
