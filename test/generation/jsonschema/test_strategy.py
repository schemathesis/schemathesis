import jsonschema_rs
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from schemathesis.generation.jsonschema import Alphabet, FormatRegistry, StrategyContext
from schemathesis.generation.jsonschema.strategy import from_schema


def test_custom_format_strategy_is_used():
    context = StrategyContext(formats=FormatRegistry({"ssn": st.just("123-45-6789")}))
    strategy = from_schema(jsonschema_rs.canonicalize({"type": "string", "format": "ssn"}), context)

    @given(strategy)
    @settings(max_examples=10)
    def check(value):
        assert value == "123-45-6789"

    check()


def test_custom_format_overrides_builtin():
    context = StrategyContext(formats=FormatRegistry({"email": st.just("a@b.test")}))
    strategy = from_schema(jsonschema_rs.canonicalize({"type": "string", "format": "email"}), context)

    @given(strategy)
    @settings(max_examples=10)
    def check(value):
        assert value == "a@b.test"

    check()


@pytest.mark.parametrize("fmt", sorted(FormatRegistry()._formats))
def test_builtin_format_matches_jsonschema_rs(fmt):
    # Every generated format value must pass jsonschema_rs's own format check, or we'd flag healthy servers.
    schema = {"type": "string", "format": fmt}
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema, validate_formats=True)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def check(value):
        assert validator.is_valid(value), f"format {fmt!r} produced invalid {value!r}"

    check()


@pytest.mark.parametrize("schema", [
    {"type": "string", "pattern": "^[a-z]+$", "minLength": 5, "maxLength": 10},
    {"type": "string", "format": "email", "maxLength": 40},
    {"type": "string", "format": "email", "pattern": "a"},
], ids=str)
def test_string_pattern_format_with_length(schema):
    strategy = from_schema(jsonschema_rs.canonicalize(schema, inline_budget=0), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


def test_alphabet_excludes_null_byte():
    context = StrategyContext(alphabet=Alphabet(allow_x00=False))
    strategy = from_schema(jsonschema_rs.canonicalize({"type": "string"}), context)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def check(value):
        assert "\x00" not in value

    check()


def test_alphabet_codec_restricts_charset():
    context = StrategyContext(alphabet=Alphabet(codec="ascii"))
    strategy = from_schema(jsonschema_rs.canonicalize({"type": "string", "minLength": 1}), context)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def check(value):
        assert value.isascii(), f"non-ascii: {value!r}"

    check()

# Self-contained object-schema fuzzer, adapted from hypothesis-jsonschema's `gen_object`.
# Patterns are kept disjoint from property names (names are lowercase, patterns start with `x-`).
_NAMES = st.text("abcdefgh", min_size=1, max_size=4)
_LEAVES = st.sampled_from([
    {"type": "integer"},
    {"type": "string"},
    {"type": "boolean"},
    {"type": "number"},
    {"type": "string", "maxLength": 3},
    {"type": "array", "items": {"type": "integer"}, "maxItems": 2},
])


@st.composite
def object_schemas(draw: st.DrawFn) -> dict:
    schema: dict = {"type": "object"}
    properties = draw(st.dictionaries(_NAMES, _LEAVES, max_size=4))
    required = draw(st.lists(st.sampled_from(sorted(properties)), unique=True)) if properties else []
    patterns = draw(st.dictionaries(st.just("^x-"), _LEAVES, max_size=1))
    additional = draw(st.none() | st.booleans() | _LEAVES)
    min_size = draw(st.none() | st.integers(0, 4))
    max_size = draw(st.none() | st.integers(0, 6))
    if min_size is not None and max_size is not None and min_size > max_size:
        min_size, max_size = max_size, min_size
    if properties:
        schema["properties"] = properties
        names = st.sampled_from(sorted(properties))
        if required:
            schema["required"] = required
        if draw(st.booleans()):
            schema["dependentRequired"] = draw(st.dictionaries(names, st.lists(names, unique=True, max_size=2)))
        if draw(st.integers(0, 3)) == 0:
            trigger = draw(names)
            schema["dependentSchemas"] = {trigger: {"required": draw(st.lists(names, unique=True, min_size=1, max_size=2))}}
    if patterns:
        schema["patternProperties"] = patterns
    if additional is not None:
        schema["additionalProperties"] = additional
    if min_size is not None:
        schema["minProperties"] = min_size
    if max_size is not None:
        schema["maxProperties"] = max_size
    # Consistent with lowercase names from `_NAMES` (subset of a-h).
    if draw(st.booleans()):
        schema["propertyNames"] = {"type": "string", "pattern": "^[a-h]+$", "maxLength": 4}
    return schema


@given(data=st.data())
@settings(max_examples=800, suppress_health_check=list(HealthCheck), deadline=None)
def test_object_fuzz_round_trip(data):
    schema = data.draw(object_schemas())
    canonical = jsonschema_rs.canonicalize(schema, inline_budget=0)
    assume(canonical.is_satisfiable())
    value = data.draw(from_schema(canonical, StrategyContext()))
    assert jsonschema_rs.validator_for(schema).is_valid(value), f"{schema} produced invalid {value!r}"

ROUND_TRIP_SCHEMAS = [
    {},
    True,
    {"type": "null"},
    {"type": "boolean"},
    {"type": "integer", "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 0, "multipleOf": 5},
    {"type": "number", "minimum": -1.5, "maximum": 3.5},
    {"type": "string", "minLength": 2, "maxLength": 5},
    {"type": "string", "pattern": "^a[0-9]+$"},
    {"type": "string", "format": "email"},
    {"const": 42},
    {"enum": [1, "a", None, True]},
    {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3},
    {"type": "array", "items": {"type": "integer"}, "uniqueItems": True, "minItems": 2, "maxItems": 4},
    {"type": "array", "prefixItems": [{"type": "integer"}, {"type": "string"}], "minItems": 2},
    {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    },
    {"type": ["integer", "string"]},
    {"anyOf": [{"type": "integer", "minimum": 5}, {"type": "string", "minLength": 1}]},
    {"oneOf": [{"type": "integer"}, {"type": "boolean"}]},
    {"not": {"type": "integer"}},
    {"not": {"type": "number"}},
    {"not": {"enum": [1, 2, 3]}},
    {"not": {"type": "object", "required": ["x"]}},
    {"not": {"type": "string", "pattern": "^a"}},
]

NEGATABLE_SCHEMAS = [
    {"type": "integer"},
    {"type": "integer", "minimum": 5},
    {"type": "string"},
    {"type": "string", "pattern": "^a[0-9]+$"},
    {"enum": [1, 2, 3]},
    {"type": "object", "required": ["x"]},
    {"type": "array", "uniqueItems": True, "items": {"type": "integer"}},
]


@pytest.mark.parametrize("schema", ROUND_TRIP_SCHEMAS, ids=str)
def test_round_trip_only_yields_valid_values(schema):
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


def test_false_schema_generates_nothing():
    strategy = from_schema(jsonschema_rs.canonicalize(False), StrategyContext())
    assert strategy.is_empty


REFERENCE_SCHEMAS = [
    {"type": "object", "properties": {"next": {"$ref": "#"}}, "additionalProperties": False},
    {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#"}}}},
    {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/N"}, "b": {"$ref": "#/$defs/N"}},
        "$defs": {"N": {"type": "integer", "minimum": 3}},
    },
    {
        "$ref": "#/$defs/A",
        "$defs": {
            "A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}},
            "B": {"type": "object", "properties": {"a": {"$ref": "#/$defs/A"}}},
        },
    },
    {
        "$ref": "#/$defs/Node",
        "$defs": {
            "Node": {
                "oneOf": [
                    {"type": "object", "required": ["leaf"], "properties": {"leaf": {"type": "string"}},
                     "additionalProperties": False},
                    {"type": "object", "required": ["kids"],
                     "properties": {"kids": {"type": "array", "items": {"$ref": "#/$defs/Node"}}},
                     "additionalProperties": False},
                ]
            }
        },
    },
]


@pytest.mark.parametrize("schema", REFERENCE_SCHEMAS, ids=str)
def test_reference_round_trip(schema):
    # `inline_budget=0` keeps refs symbolic so the generator must defer them.
    strategy = from_schema(jsonschema_rs.canonicalize(schema, inline_budget=0), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


def test_dangling_reference_generates_nothing():
    strategy = from_schema(jsonschema_rs.canonicalize({"$ref": "#/$defs/missing"}, inline_budget=0), StrategyContext())
    assert strategy.is_empty


# Schemas with no IR lifter (still canonicalize to `RawView`): `unevaluated*` with an
# applicator and `$dynamicRef`. They must still generate only valid values.
RAW_FALLBACK_SCHEMAS = [
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "allOf": [{"properties": {"a": {"type": "integer"}}}],
        "unevaluatedProperties": False,
    },
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"n": {"$dynamicAnchor": "T", "type": "integer"}},
        "$dynamicRef": "#T",
    },
]


@pytest.mark.parametrize("schema", RAW_FALLBACK_SCHEMAS, ids=str)
def test_raw_fallback_round_trip(schema):
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


OBJECT_WITNESS_SCHEMAS = [
    {"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": {"type": "string"}, "minProperties": 3},
    {"type": "object", "properties": {"a": {}, "b": {}, "c": {}}, "maxProperties": 1},
    {"type": "object", "patternProperties": {"^x-": {"type": "integer"}}, "additionalProperties": False, "minProperties": 1},
    {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}, "dependentRequired": {"a": ["b"]}},
    {"type": "object", "properties": {"a": {"type": "integer"}}, "dependentSchemas": {"a": {"required": ["b"], "properties": {"b": {"type": "string"}}}}},
    {"type": "object", "additionalProperties": {"type": "integer"}, "propertyNames": {"pattern": "^[a-z]+$"}, "minProperties": 2},
    {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}, "c": {"type": "integer"}}, "additionalProperties": False, "minProperties": 3},
    {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}, "c": {"type": "integer"}}, "additionalProperties": False, "minProperties": 2},
]


@pytest.mark.parametrize("schema", OBJECT_WITNESS_SCHEMAS, ids=str)
def test_object_witness_round_trip(schema):
    strategy = from_schema(jsonschema_rs.canonicalize(schema, inline_budget=0), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


def test_array_min_items_above_hypothesis_list_cap():
    # `minItems` larger than Hypothesis's list-size cap must still build and stay valid.
    schema = {"type": "array", "items": {"type": "integer"}, "minItems": 8193}
    strategy = from_schema(jsonschema_rs.canonicalize(schema, inline_budget=0), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=2, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"produced invalid array of length {len(value)}"

    check()


@pytest.mark.parametrize("schema", [
    {"not": {"multipleOf": 2}},
    {"not": {"type": "null"}},
    {"allOf": [{}, {"not": {"multipleOf": 3}}]},
], ids=str)
def test_typed_group_only_yields_in_type_values(schema):
    # `not multipleOf` negates to a `type`-guarded group; values must stay within the guard's type.
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=30, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"produced invalid {value!r}"

    check()


def test_all_of_with_negated_object_branch_generates_required_property():
    # `not {kind: const}` requires the object to carry a non-matching `kind`; the positive `value`
    # branch alone never produces it, so the negation must merge into the generated object.
    schema = {
        "allOf": [
            {"not": {"properties": {"kind": {"const": "number"}}}},
            {"properties": {"value": {"type": "string"}}, "required": ["value"]},
        ]
    }
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)
    seen = []

    @given(strategy)
    @settings(max_examples=30, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"produced invalid {value!r}"
        seen.append(value)

    check()
    assert seen, "strategy generated nothing"


def test_pattern_properties_with_ecma_only_pattern():
    # `\p{L}` is valid ECMA but uncompilable in Python `re`; generation must degrade, not crash.
    schema = {"patternProperties": {r"^[-._\p{L}\p{N}]+$": {"type": "string"}}}
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=10, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"produced invalid {value!r}"

    check()


def test_items_false_with_prefix_items():
    # `items: false` forbids elements past the prefix; the array must stay exactly the prefix length.
    schema = {"type": "array", "items": False, "prefixItems": [{"type": "string"}, {"type": "string"}]}
    strategy = from_schema(jsonschema_rs.canonicalize(schema), StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=10, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"produced invalid {value!r}"

    check()


@pytest.mark.parametrize("schema", [
    {"exclusiveMinimum": 2.248882142084603e16},
    {"minimum": 1.518654047127806e308},
    {"type": "number", "maximum": -1.5e308},
    {"minProperties": 55426920},
    {"minLength": 33589},
    {"type": "string", "minLength": 9867, "maxLength": 20000},
    {"exclusiveMaximum": -1.7976931348623157e308},
    {"type": "number", "exclusiveMinimum": 1.7976931348623157e308},
    {"minimum": 4.241863741862099e16, "multipleOf": 4.241863741862099e16},
    {"minimum": -1.721080944749303e253, "multipleOf": 5e-324},
], ids=str)
def test_extreme_values_generate_without_crash(schema):
    # Extreme numeric bounds / property counts must not crash strategy construction.
    strategy = from_schema(jsonschema_rs.canonicalize(schema, inline_budget=0), StrategyContext())
    if strategy.is_empty:
        # No representable witness (e.g. a number above max float); empty is sound.
        return
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=20, suppress_health_check=list(HealthCheck), deadline=None)
    def check(value):
        assert validator.is_valid(value), f"{schema} produced invalid {value!r}"

    check()


def test_max_recursion_depth_bounds_generated_nesting():
    schema = {"type": "object", "properties": {"next": {"$ref": "#"}}, "additionalProperties": False}
    strategy = from_schema(
        jsonschema_rs.canonicalize(schema, inline_budget=0),
        StrategyContext(max_recursion_depth=3),
    )

    @given(strategy)
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
    def check(value):
        depth = 0
        while isinstance(value, dict) and "next" in value:
            depth += 1
            value = value["next"]
        assert depth <= 3, f"chain depth {depth} exceeds max_recursion_depth=3"

    check()


@pytest.mark.parametrize("schema", NEGATABLE_SCHEMAS, ids=str)
def test_negation_only_yields_values_outside_the_schema(schema):
    negated = jsonschema_rs.canonicalize(schema).negate()
    strategy = from_schema(negated, StrategyContext())
    validator = jsonschema_rs.validator_for(schema)

    @given(strategy)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def check(value):
        assert not validator.is_valid(value), f"negation of {schema} produced valid {value!r}"

    check()
