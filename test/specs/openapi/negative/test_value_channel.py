import jsonschema_rs
import pytest
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample

from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.transforms import deepclone
from schemathesis.generation import GenerationMode
from schemathesis.generation.hypothesis.examples import generate_many
from schemathesis.generation.value import GeneratedValue
from schemathesis.specs.openapi._hypothesis import get_default_format_strategies
from schemathesis.specs.openapi.negative import _strip_binary, negative_schema
from schemathesis.specs.openapi.negative.mutations import MutationChannel
from schemathesis.specs.openapi.negative.value_channel import (
    apply_value_channel,
    collect_value_targets,
    violate_max_length,
)
from schemathesis.transport.serialization import Binary

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
    assert collect_value_targets({"declared": "x", "extra": "y"}, schema) == [
        (("declared",), "/properties/declared", "x", "pattern", {"type": "string", "pattern": "^a$"}),
        (("extra",), "/additionalProperties", "y", "pattern", {"type": "string", "pattern": "^b$"}),
    ]


STRING_LEAF = {"type": "string", "pattern": "^a", "minLength": 1, "maxLength": 3, "enum": ["ab"], "format": "uuid"}
NUMBER_LEAF = {"type": "number", "minimum": 0, "maximum": 9, "multipleOf": 0.5, "enum": [1.5]}
BUNDLED_LEAF = {"type": "string", "format": "email"}
BUNDLED_ROOT = {
    "type": "object",
    "properties": {"list": {"type": "array", "items": {"$ref": "#/x-bundled/Email"}}},
    "additionalProperties": {"$ref": "#/x-bundled/Email"},
}
NESTED_OBJECT = {
    "type": "object",
    "properties": {"inner": {"type": "string", "format": "date"}, "absent": {"type": "string", "minLength": 1}},
    "required": ["inner"],
}


@pytest.mark.parametrize(
    ("body", "schema", "expected"),
    [
        (
            "ab",
            STRING_LEAF,
            [
                ((), "", "ab", "pattern", STRING_LEAF),
                ((), "", "ab", "minLength", STRING_LEAF),
                ((), "", "ab", "maxLength", STRING_LEAF),
                ((), "", "ab", "enum", STRING_LEAF),
                ((), "", "ab", "format:uuid", STRING_LEAF),
            ],
        ),
        (
            1.5,
            NUMBER_LEAF,
            [
                ((), "", 1.5, "minimum", NUMBER_LEAF),
                ((), "", 1.5, "maximum", NUMBER_LEAF),
                ((), "", 1.5, "multipleOf", NUMBER_LEAF),
                ((), "", 1.5, "enum", NUMBER_LEAF),
            ],
        ),
        (True, {"enum": [True], "minimum": 0}, []),
        ("x", {"type": "string", "format": "ipv4"}, []),
        (
            {"node": {"inner": "2024-01-01"}, "extra": 3, "skipped": "x"},
            {
                "type": "object",
                "properties": {"node": NESTED_OBJECT, "skipped": True},
                "additionalProperties": {"type": "integer", "maximum": 5},
                "required": [],
            },
            [
                (
                    ("node", "inner"),
                    "/properties/node/properties/inner",
                    "2024-01-01",
                    "format:date",
                    NESTED_OBJECT["properties"]["inner"],
                ),
                (("node",), "/properties/node", {"inner": "2024-01-01"}, "required", NESTED_OBJECT),
                (("extra",), "/additionalProperties", 3, "maximum", {"type": "integer", "maximum": 5}),
            ],
        ),
        (
            ["a", "b"],
            {"type": "array", "items": {"type": "string", "format": "date-time"}},
            [
                ((0,), "/items", "a", "format:date-time", {"type": "string", "format": "date-time"}),
                ((1,), "/items", "b", "format:date-time", {"type": "string", "format": "date-time"}),
            ],
        ),
        (
            {"a": "u@example.com"},
            {
                "type": "object",
                "properties": {"a": {"$ref": "#/x-bundled/Email"}},
                BUNDLE_STORAGE_KEY: {"Email": BUNDLED_LEAF},
            },
            [(("a",), "/properties/a/$ref/Email", "u@example.com", "format:email", BUNDLED_LEAF)],
        ),
        (
            {"list": ["u@example.com"], "extra": "v@example.com"},
            {"$ref": "#/x-bundled/Root", BUNDLE_STORAGE_KEY: {"Root": BUNDLED_ROOT, "Email": BUNDLED_LEAF}},
            [
                (
                    ("list", 0),
                    "/$ref/Root/properties/list/items/$ref/Email",
                    "u@example.com",
                    "format:email",
                    BUNDLED_LEAF,
                ),
                (
                    ("extra",),
                    "/$ref/Root/additionalProperties/$ref/Email",
                    "v@example.com",
                    "format:email",
                    BUNDLED_LEAF,
                ),
            ],
        ),
        (
            "x",
            {"$ref": "#/components/schemas/Email", "format": "email"},
            [((), "", "x", "format:email", {"$ref": "#/components/schemas/Email", "format": "email"})],
        ),
    ],
    ids=[
        "string-leaf",
        "number-leaf",
        "boolean-is-not-a-number",
        "unchecked-format",
        "object",
        "array",
        "bundled-ref",
        "nested-bundled-refs",
        "external-ref",
    ],
)
def test_collect_value_targets(body, schema, expected):
    assert collect_value_targets(body, schema) == expected


def test_strip_binary_replaces_nested_binary_values():
    assert _strip_binary({"a": [Binary(b"x"), 1], "b": Binary(b"y"), "c": "z"}) == {"a": ["", 1], "b": "", "c": "z"}


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
        ("u@example.com", "format:email", {"type": "string", "format": "email"}),
        ("not-a-uuid", "format:uuid", {"type": "string", "format": "uuid"}),
        ("abc", "pattern", {"type": "string", "pattern": "^[a-z]+$"}),
        ("abcd", "minLength", {"type": "string", "minLength": 3}),
        ("abc", "maxLength", {"type": "string", "maxLength": 3}),
        (5, "minimum", {"type": "integer", "minimum": 3}),
        (5, "maximum", {"type": "integer", "maximum": 7}),
        ("a", "enum", {"enum": ["a"]}),
        (4, "multipleOf", {"type": "integer", "multipleOf": 2}),
    ],
    ids=[
        "format-date",
        "format-date-time",
        "format-email",
        "format-uuid",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "enum",
        "multipleOf",
    ],
)
def test_apply_value_channel_dispatches_keyword(value, keyword, schema):
    new_body, original, new_value = apply_value_channel(value, (), keyword, schema)
    assert (new_body, original) == (new_value, value)
    assert not jsonschema_rs.Draft202012Validator(schema, validate_formats=True).is_valid(new_value)


def test_apply_value_channel_replaces_nested_leaf():
    body = {"items": [{"name": "ab"}]}
    assert apply_value_channel(body, ("items", 0, "name"), "maxLength", {"type": "string", "maxLength": 2}) == (
        {"items": [{"name": "ab*"}]},
        "ab",
        "ab*",
    )


def test_apply_value_channel_required_key_already_absent():
    assert apply_value_channel({"b": 2}, (), "required", {"type": "object", "required": ["a"]}) == (
        {"b": 2},
        None,
        {"b": 2},
    )


def test_apply_value_channel_drops_first_required_key_of_nested_object():
    body = {"outer": {"a": 1, "b": 2}}
    assert apply_value_channel(body, ("outer",), "required", {"type": "object", "required": ["a", "b"]}) == (
        {"outer": {"b": 2}},
        1,
        {"b": 2},
    )


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
# The violating draw is rare, so fewer examples would let a regression pass.
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
# The violating draw is rare, so fewer examples would let a regression pass.
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


def _restore(body, path, original):
    if not path:
        return original
    restored = deepclone(body)
    target = restored
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = original
    return restored


def test_value_channel_base_body_is_valid():
    # Undoing the reported mutation must leave a valid body — otherwise the case is invalid
    # for a reason the report never mentions.
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer", "maximum": 100}, "b": {"type": "integer"}},
        "dependentRequired": {"a": ["b"]},
        "additionalProperties": False,
    }
    validator = jsonschema_rs.Draft202012Validator(schema, validate_formats=True)

    def is_invalid_before_mutation(result: GeneratedValue) -> bool:
        if isinstance(result.value, bytes) or result.meta is None:
            return False
        return any(
            not validator.is_valid(_restore(result.value, mutation.path, mutation.original_value))
            for mutation in result.meta.mutations
            if mutation.channel == MutationChannel.VALUE
        )

    with pytest.raises(NoSuchExample):
        find(
            negative_schema(
                schema,
                operation_name="POST /widgets/",
                location=ParameterLocation.BODY,
                media_type="application/json",
                custom_formats=get_default_format_strategies(),
                generation_config=GenerationConfig(),
                validator_cls=jsonschema_rs.Draft202012Validator,
            ),
            is_invalid_before_mutation,
            # The excluded value is rare, so a small budget would pass without looking.
            settings=settings(max_examples=1000, database=None, deadline=None, suppress_health_check=_SUPPRESSED),
        )


def test_value_channel_keeps_earlier_bodies_intact(ctx):
    # A value-channel break must not alter enum members shared by later draws or earlier cases.
    operation = ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["a"],
                            "properties": {"a": {"type": "string", "minLength": 2}},
                            "enum": [{"a": "xx"}],
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )
    # The value channel fires on 15% of draws, so a smaller budget can miss it.
    drawn = [
        (case.body, deepclone(case.body))
        for case in generate_many(operation.as_strategy(generation_mode=GenerationMode.NEGATIVE), 100)
    ]
    assert [snapshot for body, snapshot in drawn if body != snapshot] == []
