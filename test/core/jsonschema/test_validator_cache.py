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
