import socket

import jsonschema_rs
import pytest

from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY, is_valid, make_validator, make_validator_with_seed


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


def test_bundles_with_the_same_definition_names_stay_apart():
    string = {"$ref": f"#/{BUNDLE_STORAGE_KEY}/s", BUNDLE_STORAGE_KEY: {"s": {"type": "string"}}}
    integer = {"$ref": f"#/{BUNDLE_STORAGE_KEY}/s", BUNDLE_STORAGE_KEY: {"s": {"type": "integer"}}}
    validators = [make_validator(schema, jsonschema_rs.Draft7Validator) for schema in (string, integer)]
    assert [(validator.is_valid("a"), validator.is_valid(1)) for validator in validators] == [
        (True, False),
        (False, True),
    ]


def test_property_named_ref_is_not_a_reference():
    schema = {
        "properties": {"$ref": {"$ref": f"#/{BUNDLE_STORAGE_KEY}/s"}},
        BUNDLE_STORAGE_KEY: {"s": {"type": "string"}},
    }
    validator = make_validator(schema, jsonschema_rs.Draft7Validator)
    assert (validator.is_valid({"$ref": "a"}), validator.is_valid({"$ref": 1})) == (True, False)


def test_seeded_validator_is_cached_per_seed():
    string = make_validator_with_seed(lambda: {"type": "string"}, jsonschema_rs.Draft7Validator, ("seeded", "string"))
    integer = make_validator_with_seed(lambda: {"type": "integer"}, jsonschema_rs.Draft7Validator, ("seeded", "int"))
    again = make_validator_with_seed(lambda: {"type": "integer"}, jsonschema_rs.Draft7Validator, ("seeded", "string"))
    assert again is string
    assert [string.is_valid("a"), integer.is_valid(1), integer.is_valid("a")] == [True, True, False]


def test_value_passes_when_the_schema_cannot_be_compiled():
    assert is_valid(1, {"type": "string", "pattern": "("})
