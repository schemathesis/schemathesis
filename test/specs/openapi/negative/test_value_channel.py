import jsonschema_rs
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.value import GeneratedValue
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies
from schemathesis.specs.openapi.negative import _strip_binary, negative_schema
from schemathesis.specs.openapi.negative.mutations import MutationChannel
from schemathesis.specs.openapi.negative.value_channel import (
    apply_value_channel,
    collect_value_targets,
    violate_max_length,
)

_SUPPRESSED = [HealthCheck.too_slow, HealthCheck.filter_too_much, HealthCheck.data_too_large]


def test_violate_max_length_skips_huge_max_length():
    # INT32_MAX `maxLength` would expand to ~2 GB; skip instead.
    original = "abc"
    assert violate_max_length(original, 2_147_483_647) == original


def test_collect_value_targets_handles_boolean_property_schema():
    # OpenAPI 3.1 / JSON Schema allow boolean property schemas (`{"x": true}`);
    # recursing into them and calling `.get` on `True` raises AttributeError.
    schema = {"type": "object", "properties": {"x": True, "y": False}}
    body = {"x": "hello", "y": 42}

    assert collect_value_targets(body, schema) == []


def test_collect_value_targets_returns_empty_for_missing_bundled_ref():
    # Schemas adjusted by error-feedback can leave `$ref` pointing into a stripped
    # bundle; a missing target yields no constraint-bearing leaves rather than crashing.
    schema = {"$ref": "#/x-bundled/missing", BUNDLE_STORAGE_KEY: {}}
    assert collect_value_targets({"x": "hello"}, schema) == []


def test_collect_value_targets_walks_additional_properties_only_for_uncovered_keys():
    # Each body key picks a target schema from `properties` first, falling back
    # to `additionalProperties` only for keys not declared upfront.
    schema = {
        "type": "object",
        "properties": {"declared": {"type": "string", "pattern": "^a$"}},
        "additionalProperties": {"type": "string", "pattern": "^b$"},
    }
    targets = collect_value_targets({"declared": "x", "extra": "y"}, schema)
    assert {path: keyword for path, _, _, keyword, _ in targets} == {("declared",): "pattern", ("extra",): "pattern"}


def test_apply_value_channel_required_with_empty_list_is_noop():
    # An object whose `required` is present but empty has nothing to drop —
    # the call returns the body unchanged so the dispatcher's revalidation
    # falls back to schema-channel.
    schema = {"type": "object", "required": [], "properties": {"x": {"type": "string"}}}
    body = {"x": "hello"}
    assert apply_value_channel(body, (), "required", schema) == (body, body, body)


@pytest.mark.parametrize(
    ("value", "keyword", "schema"),
    [
        ("2024-01-01", "format:date", {"type": "string", "format": "date"}),
        ("2024-01-01T00:00:00Z", "format:date-time", {"type": "string", "format": "date-time"}),
        ("abc", "maxLength", {"type": "string", "maxLength": 3}),
    ],
    ids=["format-date", "format-date-time", "maxLength"],
)
def test_apply_value_channel_dispatches_keyword(value, keyword, schema):
    new_body, original, new_value = apply_value_channel(value, (), keyword, schema)
    assert original == value
    assert new_value != original
    assert new_body == new_value


@given(data=st.data())
@settings(deadline=None, suppress_health_check=_SUPPRESSED, max_examples=10)
def test_unsatisfiable_body_schema_does_not_abort_generation(data):
    # `from_schema` raises `InvalidArgument` when drawn for unsatisfiable schemas;
    # the schema-channel mutator can still produce invalid cases, so the value
    # channel must not crash the whole strategy.
    schema = {"type": "string", "minLength": 5, "maxLength": 3}
    result = data.draw(
        negative_schema(
            schema,
            operation_name="POST /widgets/",
            location=ParameterLocation.BODY,
            media_type="application/json",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
            validator_cls=jsonschema_rs.Draft4Validator,
        )
    )
    assert isinstance(result, GeneratedValue)


@given(data=st.data())
@settings(deadline=None, suppress_health_check=_SUPPRESSED, max_examples=100)
def test_value_channel_does_not_emit_still_valid_bodies(data):
    # A permissive sibling (`minLength: 0`) next to a format field must not let a still-valid body slip through.
    schema = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "code": {"type": "string", "minLength": 0},
        },
        "required": ["email", "code"],
        "additionalProperties": False,
    }
    real_validator = jsonschema_rs.Draft4Validator(schema, validate_formats=True)
    result = data.draw(
        negative_schema(
            schema,
            operation_name="POST /widgets/",
            location=ParameterLocation.BODY,
            media_type="application/json",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
            validator_cls=jsonschema_rs.Draft4Validator,
        )
    )
    assert isinstance(result, GeneratedValue)
    if (
        not isinstance(result.value, bytes)
        and result.meta is not None
        and any(m.channel == MutationChannel.VALUE for m in result.meta.mutations)
    ):
        assert not real_validator.is_valid(result.value), f"still-valid body emitted as negative: {result.value!r}"


@given(data=st.data())
@settings(deadline=None, suppress_health_check=_SUPPRESSED, max_examples=100)
def test_value_channel_falls_back_when_violator_is_a_noop_with_binary_sibling(data):
    # `contains_binary` must not skip no-op revalidation: a binary+permissive-sibling body
    # would otherwise emit as still-valid negative data.
    schema = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "format": "binary"},
            "code": {"type": "string", "minLength": 0},
        },
        "required": ["file", "code"],
        "additionalProperties": False,
    }
    real_validator = jsonschema_rs.Draft4Validator(schema, validate_formats=True)
    result = data.draw(
        negative_schema(
            schema,
            operation_name="POST /upload/",
            location=ParameterLocation.BODY,
            media_type="multipart/form-data",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
            validator_cls=jsonschema_rs.Draft4Validator,
        )
    )
    assert isinstance(result, GeneratedValue)
    if (
        not isinstance(result.value, bytes)
        and result.meta is not None
        and any(m.channel == MutationChannel.VALUE for m in result.meta.mutations)
    ):
        stripped = _strip_binary(result.value)
        assert not real_validator.is_valid(stripped), f"still-valid body emitted as negative: {result.value!r}"


@given(data=st.data())
@settings(deadline=None, suppress_health_check=_SUPPRESSED, max_examples=30)
def test_value_channel_handles_binary_bodies(data):
    # `jsonschema_rs` raises `ValueError: Unsupported type: 'Binary'` on Schemathesis
    # `Binary` wrappers; the value-channel revalidation must short-circuit on
    # `contains_binary` before calling the validator.
    schema = {
        "type": "object",
        "properties": {
            "file": {"type": "string", "format": "binary"},
            "caption": {"type": "string", "minLength": 0},
        },
        "required": ["file", "caption"],
    }
    result = data.draw(
        negative_schema(
            schema,
            operation_name="POST /upload/",
            location=ParameterLocation.BODY,
            media_type="multipart/form-data",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
            validator_cls=jsonschema_rs.Draft4Validator,
        )
    )
    assert isinstance(result, GeneratedValue)


@given(data=st.data())
@settings(deadline=None, suppress_health_check=_SUPPRESSED, max_examples=30)
def test_value_channel_falls_back_when_violator_is_a_noop(data):
    # `violate_multiple_of` returns `original + 1`, which is still a multiple
    # of 1; without revalidation the dispatcher would emit that as a negative
    # case and a server returning 200 would surface as `negative_data_rejection`.
    schema = {"type": "integer", "multipleOf": 1}
    validator = jsonschema_rs.Draft4Validator(schema)
    result = data.draw(
        negative_schema(
            schema,
            operation_name="POST /widgets/",
            location=ParameterLocation.BODY,
            media_type="application/json",
            custom_formats=get_default_format_strategies(),
            generation_config=GenerationConfig(),
            validator_cls=jsonschema_rs.Draft4Validator,
        )
    )
    assert isinstance(result, GeneratedValue)
    if not isinstance(result.value, bytes):
        assert not validator.is_valid(result.value)
