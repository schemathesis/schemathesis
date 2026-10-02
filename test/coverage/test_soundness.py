from __future__ import annotations

import json

import hypothesis.strategies as st
import jsonschema_rs
import pytest
from hypothesis import HealthCheck, assume, given, reject, settings

from schemathesis.config import GenerationConfig
from schemathesis.core.jsonschema import BUNDLE_STORAGE_KEY
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.specs.openapi._hypothesis import _canonical_strategy
from schemathesis.specs.openapi.coverage._schema import MAX_DRAWN_ARRAY_ITEMS, CoverageContext, cover_schema_iter
from schemathesis.specs.openapi.formats import get_default_format_strategies
from schemathesis.specs.openapi.patterns import update_quantifier
from tools.coverage.caches import clear_internal_caches

DRAFT4 = jsonschema_rs.Draft4Validator
DRAFT2020 = jsonschema_rs.Draft202012Validator
METASCHEMAS = [
    (DRAFT4, {"$ref": "http://json-schema.org/draft-04/schema#"}),
    (DRAFT2020, {"$ref": "https://json-schema.org/draft/2020-12/schema"}),
]
FORMATS = ["email", "uuid", "date", "date-time", "ipv4", "hostname", "uri"]
PATTERNS = ["^[a-z]+$", "^\\d{3}$", "[0-9]+", "^(foo|bar)$", "^[A-Z][a-z]*$"]
NAMES = ["a", "b", "c"]
SCALARS = st.none() | st.booleans() | st.integers(-5, 20) | st.text(max_size=4)
# Floats past 2**53 test bound handling: a unit step no longer moves them, and their shortest repr is inexact.
BOUNDS = st.integers(-10, 10) | st.sampled_from([-5.151020255852562e16, 9996036847180748.0, 1e17])
REF = {"$ref": f"#/{BUNDLE_STORAGE_KEY}/A"}
HINTS = st.fixed_dictionaries({}, optional={"example": SCALARS, "default": SCALARS})


def _hinted(schema: st.SearchStrategy[dict]) -> st.SearchStrategy[dict]:
    return st.builds(lambda base, hints: {**base, **hints}, schema, HINTS)


def _string(draft4: bool) -> st.SearchStrategy[dict]:
    # Draft 4 has no `uuid`, so the oracle cannot judge the format negatives coverage emits for it on purpose.
    formats = [f for f in FORMATS if not (draft4 and f == "uuid")]
    return st.fixed_dictionaries(
        {"type": st.just("string")},
        optional={
            "minLength": st.integers(0, 6),
            "maxLength": st.integers(0, 12),
            "pattern": st.sampled_from(PATTERNS),
            "format": st.sampled_from(formats),
            "enum": st.lists(st.text(max_size=5), min_size=1, max_size=3, unique=True),
        },
    )


def _number(draft4: bool) -> st.SearchStrategy[dict]:
    optional = {
        "minimum": BOUNDS,
        "maximum": BOUNDS,
        "multipleOf": st.sampled_from([2, 3, 5, 0.5]),
        "enum": st.lists(st.integers(-10, 10), min_size=1, max_size=3, unique=True),
    }
    if not draft4:
        optional["exclusiveMinimum"] = BOUNDS
        optional["exclusiveMaximum"] = BOUNDS
    return st.fixed_dictionaries({"type": st.sampled_from(["integer", "number"])}, optional=optional)


def _leaf(draft4: bool) -> st.SearchStrategy[dict]:
    leaves = (
        st.just({"type": "null"})
        | st.just({"type": "boolean"})
        | _hinted(_string(draft4))
        | _hinted(_number(draft4))
        | st.fixed_dictionaries({"type": st.just(["string", "null"])}, optional={"minLength": st.integers(1, 3)})
        | st.just(REF)
    )
    if not draft4:
        leaves |= st.fixed_dictionaries({"const": SCALARS})
    return leaves


def _extend(children: st.SearchStrategy[dict]) -> st.SearchStrategy[dict]:
    array = st.fixed_dictionaries(
        {"type": st.just("array"), "items": children},
        optional={"minItems": st.integers(0, 3), "maxItems": st.integers(0, 5), "uniqueItems": st.booleans()},
    )

    @st.composite
    def obj(draw, additional_properties: bool = True, names: list[str] = NAMES) -> dict:
        properties = draw(st.dictionaries(st.sampled_from(names), children, min_size=1, max_size=3))
        schema: dict = {"type": "object", "properties": properties}
        if additional_properties and draw(st.booleans()):
            schema["additionalProperties"] = draw(st.booleans() | children)
        if draw(st.booleans()):
            schema["required"] = draw(st.lists(st.sampled_from([*NAMES, "extra"]), min_size=1, max_size=3, unique=True))
        if draw(st.booleans()):
            schema["minProperties"] = draw(st.integers(0, 3))
        if draw(st.booleans()):
            schema["maxProperties"] = draw(st.integers(0, 4))
        return schema

    branches = st.lists(children, min_size=1, max_size=3)
    sibling_type = {"type": st.sampled_from(["string", "integer", "object", "array"])}
    combinator = st.one_of(
        st.fixed_dictionaries({"anyOf": branches}, optional=sibling_type),
        st.fixed_dictionaries({"oneOf": branches}, optional=sibling_type),
        st.fixed_dictionaries({"allOf": st.lists(children, min_size=1, max_size=1)}, optional=sibling_type),
        # Open objects with disjoint names per branch reach the object-merging paths of `allOf`.
        st.fixed_dictionaries(
            {
                "allOf": st.tuples(
                    obj(additional_properties=False, names=NAMES[:1]),
                    obj(additional_properties=False, names=NAMES[1:]),
                ).map(list)
            }
        ),
        st.fixed_dictionaries({"not": children}, optional=sibling_type),
        # An `anyOf` spliced onto any node puts a combinator beside describing keywords or another combinator.
        st.builds(lambda base, branch: {**base, "anyOf": [branch]}, children, children),
    )
    return array | obj() | combinator


def schemas(draft4: bool) -> st.SearchStrategy[dict]:
    body = st.recursive(_leaf(draft4), _extend, max_leaves=6)
    return st.builds(lambda schema, bundled: {**schema, BUNDLE_STORAGE_KEY: {"A": bundled}}, body, _leaf(draft4))


def _mentions(value: object, key: str, expected: object) -> bool:
    if isinstance(value, dict):
        return value.get(key) == expected or any(_mentions(item, key, expected) for item in value.values())
    if isinstance(value, list):
        return any(_mentions(item, key, expected) for item in value)
    return False


# A large `minProperties` floor under constrained names takes minutes of unique-key search.
def _is_out_of_scope(value: object) -> bool:
    if isinstance(value, dict):
        floor = value.get("minProperties")
        constrained_names = (
            "propertyNames" in value or "patternProperties" in value or value.get("additionalProperties") is False
        )
        if isinstance(floor, int) and floor > MAX_DRAWN_ARRAY_ITEMS and constrained_names:
            return True
        return any(_is_out_of_scope(item) for item in value.values())
    if isinstance(value, list):
        return any(_is_out_of_scope(item) for item in value)
    return False


def _context(schema: dict, mode: GenerationMode, validator_cls: type[jsonschema_rs.Validator]) -> CoverageContext:
    return CoverageContext(
        root_schema=schema,
        location=ParameterLocation.BODY,
        media_type=("application", "json"),
        generation_modes=[mode],
        is_required=True,
        custom_formats=get_default_format_strategies(),
        validator_cls=validator_cls,
        update_pattern=update_quantifier,
    )


def assert_sound(schema: dict, mode: GenerationMode, validator_cls: type[jsonschema_rs.Validator]) -> None:
    validator = validator_cls(schema, validate_formats=True, offline=True)
    for generated in cover_schema_iter(_context(schema, mode, validator_cls), schema):
        assert generated.generation_mode == mode, generated
        assert validator.is_valid(generated.value) == (mode == GenerationMode.POSITIVE), (
            f"{mode.value} {generated.scenario.value} at {generated.location}: {generated.value!r} for {schema!r}"
        )


SOUNDNESS_SETTINGS = {
    "deadline": None,
    "suppress_health_check": [
        HealthCheck.too_slow,
        HealthCheck.data_too_large,
        HealthCheck.filter_too_much,
        HealthCheck.nested_given,
    ],
}


@pytest.mark.parametrize("validator_cls", [DRAFT4, DRAFT2020])
@given(data=st.data())
@settings(**SOUNDNESS_SETTINGS)
def test_generated_body_values_match_their_mode(validator_cls, data):
    # Per-example isolation: generation caches are process-global.
    clear_internal_caches()
    schema = data.draw(schemas(draft4=validator_cls is DRAFT4))
    mode = data.draw(st.sampled_from(list(GenerationMode)))
    assert_sound(schema, mode, validator_cls)


# The metaschema reaches keyword mixes the structured strategy never builds.
@pytest.mark.parametrize(("validator_cls", "metaschema"), METASCHEMAS, ids=["draft4", "draft2020"])
@given(data=st.data())
@settings(database=None, **SOUNDNESS_SETTINGS)
def test_generated_body_values_match_their_mode_from_metaschema(validator_cls, metaschema, data):
    schema = data.draw(_canonical_strategy(metaschema, GenerationConfig(), validator_cls))
    assume(isinstance(schema, dict) and len(json.dumps(schema, default=str)) <= 1200)
    assume(not _is_out_of_scope(schema))
    assume(not (validator_cls is DRAFT4 and _mentions(schema, "format", "uuid")))
    try:
        validator_cls(schema, validate_formats=True, offline=True)
    except jsonschema_rs.ValidationError:
        reject()
    clear_internal_caches()
    assert_sound(schema, data.draw(st.sampled_from(list(GenerationMode))), validator_cls)
