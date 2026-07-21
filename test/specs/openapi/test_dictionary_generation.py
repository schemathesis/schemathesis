from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation, ObservationKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.dictionaries import (
    DictionaryDraw,
    build_dictionary_overlay_strategy,
    resolve_parameter_bindings,
)
from schemathesis.generation.value import GeneratedValue
from schemathesis.resources import SemanticDraw


def _load_schema_with_dictionaries(ctx, config: dict, paths: dict, *, version: str = "3.0.2"):
    schema = ctx.openapi.load_schema(paths, version=version)
    parent_config = SchemathesisConfig.from_dict(config)
    schema.config._parent = parent_config
    schema.config.generation = parent_config.projects.default.generation
    schema.config.parameters = parent_config.projects.default.parameters
    schema.config.operations = parent_config.projects.default.operations
    return schema


_PATHS_ONE_STRING = {
    "/items": {
        "get": {
            "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
            "responses": {"200": {"description": "OK"}},
        }
    }
}


@pytest.mark.hypothesis_nested
def test_probability_one_always_substitutes(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"edge": {"values": ["admin", "root", "' OR 1=1--"]}},
            "generation": {"dictionaries": {"string": {"dictionary": "edge", "probability": 1.0}}},
        },
        _PATHS_ONE_STRING,
    )
    operation = schema["/items"]["GET"]
    seen_substituted = []

    @given(case=operation.as_strategy())
    @settings(max_examples=15, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        q = case.query.get("q")
        if q is None:
            return
        seen_substituted.append(q)
        assert q in {"admin", "root", "' OR 1=1--"}

    collect()
    assert seen_substituted, "no q values generated to verify"


@pytest.mark.hypothesis_nested
def test_dictionary_draw_provenance_attached(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"edge": {"values": ["payload"]}},
            "generation": {"dictionaries": {"string": {"dictionary": "edge", "probability": 1.0}}},
        },
        _PATHS_ONE_STRING,
    )
    operation = schema["/items"]["GET"]
    draws_seen = []

    @given(case=operation.as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        if case._meta is not None and case._meta.dictionary_draws:
            draws_seen.extend(case._meta.dictionary_draws)

    collect()
    assert draws_seen and set(draws_seen) == {
        DictionaryDraw(
            dictionary="edge",
            source_kind="values",
            source_path=None,
            entry_index=0,
            operation_label="GET /items",
            parameter_location="query",
            parameter_name="q",
            value="payload",
            matches_schema=True,
        )
    }


@pytest.mark.hypothesis_nested
def test_parameter_binding_wins_over_type_wide(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {
                "broad": {"values": ["BROAD"]},
                "specific": {"values": ["SPECIFIC"]},
            },
            "generation": {"dictionaries": {"string": {"dictionary": "broad", "probability": 1.0}}},
            "parameters": {"query.q": {"dictionary": "specific"}},
        },
        _PATHS_ONE_STRING,
    )
    operation = schema["/items"]["GET"]
    values = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=15, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        q = case.query.get("q")
        if q is not None:
            values.add(q)

    collect()
    assert values == {"SPECIFIC"}


@pytest.mark.hypothesis_nested
def test_integer_eligibility_filters_string_values(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"mixed": {"values": ["1", "abc", "42"]}},
            "generation": {"dictionaries": {"integer": {"dictionary": "mixed", "probability": 1.0}}},
        },
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "n", "in": "query", "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["GET"]
    dictionary_values = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=20, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        if case._meta is None:
            return
        for draw in case._meta.dictionary_draws:
            dictionary_values.add(draw.value)

    collect()
    assert dictionary_values, "no dictionary draws recorded"
    assert dictionary_values == {1, 42}


@pytest.mark.hypothesis_nested
def test_positive_path_parameter_binding_does_not_break_serialization(ctx):
    # Path parameters under positive mode pass through `_quote_all_safe` and
    # `jsonify_python_specific_types` directly; the dictionary overlay wraps
    # substituted values in `GeneratedValue`, which those helpers cannot iterate.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"ids": {"values": ["abc123"]}},
            "parameters": {"path.id": {"dictionary": "ids"}},
        },
        {
            "/items/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items/{id}"]["GET"]

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        assert case.path_parameters["id"] == "abc123"

    collect()


@pytest.mark.hypothesis_nested
def test_parameter_binding_forces_optional_parameter_present(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"edge": {"values": ["SENTINEL"]}},
            "parameters": {"query.q": {"dictionary": "edge"}},
        },
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["GET"]
    omissions = 0
    substitutions = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=20, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal omissions, substitutions
        if "q" not in case.query:
            omissions += 1
        elif case.query["q"] == "SENTINEL":
            substitutions += 1

    collect()
    assert omissions == 0
    assert substitutions > 0


@pytest.mark.hypothesis_nested
def test_negative_mode_applies_dictionary_to_unmutated_parameters(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"short": {"values": ["ab"]}},
            "parameters": {"query.r": {"dictionary": "short"}},
        },
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string", "minLength": 5}},
                        {"name": "r", "in": "query", "schema": {"type": "string", "minLength": 5}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["GET"]
    short_value_in_negative_case = False

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=30, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal short_value_in_negative_case
        if case._meta is None:
            return
        query_component = case._meta.components.get(ParameterLocation.QUERY)
        if query_component is None or query_component.mode != GenerationMode.NEGATIVE:
            return
        if case.query.get("r") == "ab":
            short_value_in_negative_case = True

    collect()
    assert short_value_in_negative_case, (
        "expected schema-violating dictionary entry to fire on un-mutated parameter `r` "
        "while `q` carried the negative mutation"
    )


@pytest.mark.hypothesis_nested
def test_negative_mode_keeps_case_negative_when_dictionary_overrides_only_mutation(ctx):
    # Dictionary overwrites the only mutated parameter; case must stay negative.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"short": {"values": ["ab"]}},
            "parameters": {"query.q": {"dictionary": "short"}},
        },
        {
            "/items": {
                "get": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "minLength": 5},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["GET"]
    invalid_draw_cases = 0

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=30, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal invalid_draw_cases
        if case._meta is None:
            return
        if any(not d.matches_schema for d in case._meta.dictionary_draws):
            invalid_draw_cases += 1
            assert case._meta.generation.mode is GenerationMode.NEGATIVE, (
                f"schema-invalid dictionary draw downgraded case to positive: query={case.query!r}"
            )

    collect()
    assert invalid_draw_cases > 0, "scenario never produced a schema-invalid dictionary draw"


@pytest.mark.hypothesis_nested
def test_positive_mode_filters_out_schema_violating_entries(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"mixed": {"values": ["valid-string-long-enough", "ab"]}},
            "parameters": {"query.q": {"dictionary": "mixed"}},
        },
        {
            "/items": {
                "get": {
                    "parameters": [{"name": "q", "in": "query", "schema": {"type": "string", "minLength": 5}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["GET"]
    seen: set = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=15, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        q = case.query.get("q")
        if q is not None:
            seen.add(q)

    collect()
    assert seen == {"valid-string-long-enough"}


@pytest.mark.hypothesis_nested
def test_overlay_does_not_admit_schema_invalid_entries_when_schema_view_is_missing(ctx):
    # Missing parameter-schema view must keep dict entries out of positive-mode cases.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"strs": {"values": ["c001"]}},
            "parameters": {"path.id": {"dictionary": "strs"}},
        },
        {
            "/items/{id}": {
                "get": {
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items/{id}"]["GET"]
    bindings = resolve_parameter_bindings(
        operation=operation,
        location=ParameterLocation.PATH,
        properties=operation.path_parameters.schema.get("properties", {}),
        generation_config=schema.config.generation,
    )
    overlay = build_dictionary_overlay_strategy(
        st.fixed_dictionaries({"id": st.just(42)}),
        bindings=bindings,
        operation_label=operation.label,
        parameter_location=ParameterLocation.PATH,
        schema_properties={},
        validator_cls=operation.schema.adapter.jsonschema_validator_cls,
        generation_mode=GenerationMode.POSITIVE,
    )
    leaked: list[DictionaryDraw] = []

    @given(value=overlay)
    @settings(max_examples=15, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def check(value):
        if isinstance(value, GeneratedValue):
            leaked.extend(value.dictionary_draws)

    check()
    assert not leaked, f"schema-invalid entries leaked into POSITIVE mode: {leaked}"


@pytest.mark.hypothesis_nested
def test_unexpected_property_feedback_does_not_leak_invalid_dict_entries_into_positive(ctx):
    # Production trigger for the schema-view-missing case: error-feedback's
    # UNEXPECTED_PROPERTY adjustment pops a parameter from the strategy schema,
    # while `resolve_parameter_bindings` keeps the binding (it reads the full
    # parameter-set schema). The overlay then has a binding without its schema.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"strs": {"values": ["c001"]}},
            "parameters": {"path.constraintId": {"dictionary": "strs"}},
        },
        {
            "/items/{constraintId}": {
                "get": {
                    "parameters": [
                        {
                            "name": "constraintId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items/{constraintId}"]["GET"]
    store = ErrorFeedbackStore()
    store.record(
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            parameter_path=("constraintId",),
            kind=ObservationKind.UNEXPECTED_PROPERTY,
            raw_message="constraintId is not allowed",
        )
    )
    leaked: list[DictionaryDraw] = []

    @given(case=operation.as_strategy(error_feedback=store))
    @settings(max_examples=20, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        if case._meta is None:
            return
        for d in case._meta.dictionary_draws:
            if d.parameter_name == "constraintId" and d.matches_schema:
                leaked.append(d)

    collect()
    assert not leaked, f"schema-invalid entries leaked into POSITIVE mode: {leaked}"


def test_resolve_parameter_bindings_respects_caller_provided_properties(ctx):
    # Bindings only cover parameters present in the caller-supplied `properties` view.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"strs": {"values": ["c001"]}},
            "parameters": {"path.constraintId": {"dictionary": "strs"}},
        },
        {
            "/items/{constraintId}": {
                "get": {
                    "parameters": [
                        {
                            "name": "constraintId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer"},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items/{constraintId}"]["GET"]
    assert (
        resolve_parameter_bindings(
            operation=operation,
            location=ParameterLocation.PATH,
            properties={},
            generation_config=schema.config.generation,
        )
        == {}
    )


@pytest.mark.hypothesis_nested
def test_probability_below_one_takes_both_substitute_and_skip_branches(ctx):
    # With `probability=0.5`, both branches of the per-binding coin must fire across runs.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"edge": {"values": ["X"]}},
            "parameters": {"query.q": {"dictionary": "edge", "probability": 0.5}},
        },
        _PATHS_ONE_STRING,
    )
    operation = schema["/items"]["GET"]
    substituted = 0
    skipped = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=60, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal substituted, skipped
        if case._meta is None:
            return
        if case._meta.dictionary_draws:
            substituted += 1
        else:
            skipped += 1

    collect()
    assert substituted > 0 and skipped > 0, f"coin never took both paths: substituted={substituted}, skipped={skipped}"


@pytest.mark.hypothesis_nested
def test_overlay_shares_slot_with_prior_semantic_substitution(ctx):
    # When the inner strategy already substituted the same parameter via the semantic
    # pool, the dict overlay splits the slot 50/50 instead of always overriding.
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"edge": {"values": ["DICT"]}},
            "parameters": {"query.q": {"dictionary": "edge"}},
        },
        _PATHS_ONE_STRING,
    )
    operation = schema["/items"]["GET"]
    parameter_set = operation.query
    properties = parameter_set.schema.get("properties", {})
    bindings = resolve_parameter_bindings(
        operation=operation,
        location=ParameterLocation.QUERY,
        properties=properties,
        generation_config=schema.config.generation,
    )
    inner = st.just(
        GeneratedValue(
            value={"q": "SEMANTIC"},
            meta=None,
            pool_draws=(),
            semantic_draws=(
                SemanticDraw(
                    path=("q",),
                    type_token=None,
                    format_token=None,
                    pattern_hash=None,
                    normalized_name=None,
                    value="SEMANTIC",
                    source_operation=None,
                ),
            ),
            dictionary_draws=(),
        )
    )
    overlay = build_dictionary_overlay_strategy(
        inner,
        bindings=bindings,
        operation_label=operation.label,
        parameter_location=ParameterLocation.QUERY,
        schema_properties=properties,
        validator_cls=operation.schema.adapter.jsonschema_validator_cls,
        generation_mode=GenerationMode.POSITIVE,
    )
    kept_semantic = 0
    dict_won = 0

    @given(value=overlay)
    @settings(max_examples=40, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(value):
        nonlocal kept_semantic, dict_won
        assert isinstance(value, GeneratedValue)
        q = value.value.get("q")
        if q == "SEMANTIC":
            kept_semantic += 1
        elif q == "DICT":
            dict_won += 1

    collect()
    assert kept_semantic > 0 and dict_won > 0, (
        f"semantic/dict 50/50 split never took both paths: semantic={kept_semantic}, dict={dict_won}"
    )


def _path_with_body(body_schema: dict, *, required: bool = True) -> dict:
    body = {"content": {"application/json": {"schema": body_schema}}}
    if required:
        body["required"] = True
    return {
        "/items": {
            "post": {
                "requestBody": body,
                "responses": {"200": {"description": "OK"}},
            }
        }
    }


@pytest.mark.hypothesis_nested
def test_body_binding_top_level_field(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"cc": {"values": ["1234-5678-9012-3456"]}},
            "parameters": {"body.ccNumber": {"dictionary": "cc"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {"ccNumber": {"type": "string"}, "name": {"type": "string"}},
                "required": ["ccNumber"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    seen_values: set[str] = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=15, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen_values.add(case.body.get("ccNumber"))

    collect()
    assert seen_values == {"1234-5678-9012-3456"}


@pytest.mark.hypothesis_nested
@pytest.mark.parametrize("combinator", ["oneOf", "anyOf", "allOf"])
def test_body_binding_field_under_combinator(ctx, combinator):
    object_schema = {
        "type": "object",
        "properties": {"region": {"type": "string"}},
        "required": ["region"],
    }
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"region": {"values": ["DE", "GB", "US"]}},
            "parameters": {"body.region": {"dictionary": "region"}},
        },
        _path_with_body({combinator: [object_schema]}),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen.add(case.body.get("region") if isinstance(case.body, dict) else None)

    collect()
    assert seen == {"DE", "GB", "US"}


@pytest.mark.hypothesis_nested
def test_body_binding_field_under_conditional(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"region": {"values": ["DE", "GB", "US"]}},
            "parameters": {"body.region": {"dictionary": "region"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {"kind": {"const": "x"}},
                "required": ["kind"],
                "if": {"properties": {"kind": {"const": "x"}}},
                "then": {"properties": {"region": {"type": "string"}}, "required": ["region"]},
            }
        ),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen.add(case.body.get("region") if isinstance(case.body, dict) else None)

    collect()
    assert seen == {"DE", "GB", "US"}


@pytest.mark.hypothesis_nested
def test_body_binding_nested_field(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"emails": {"values": ["x@y.com"]}},
            "parameters": {"body.user.email": {"dictionary": "emails"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                        "required": ["email"],
                    }
                },
                "required": ["user"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    seen: set[str] = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen.add(case.body.get("user", {}).get("email"))

    collect()
    assert seen == {"x@y.com"}


@pytest.mark.hypothesis_nested
def test_body_binding_array_wildcard(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"names": {"values": ["widget"]}},
            "parameters": {"body.items[*].name": {"dictionary": "names"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                            "required": ["name"],
                        },
                    }
                },
                "required": ["items"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    elements_with_widget = 0
    total_elements = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal elements_with_widget, total_elements
        for item in case.body.get("items", []):
            total_elements += 1
            if item.get("name") == "widget":
                elements_with_widget += 1

    collect()
    assert total_elements > 0 and elements_with_widget == total_elements


@pytest.mark.hypothesis_nested
def test_body_binding_top_level_array(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"tags": {"values": ["alpha"]}},
            "parameters": {"body.[*]": {"dictionary": "tags"}},
        },
        _path_with_body(
            {"type": "array", "minItems": 1, "items": {"type": "string"}},
        ),
    )
    operation = schema["/items"]["POST"]
    elements_seen = 0
    elements_with_alpha = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal elements_seen, elements_with_alpha
        for item in case.body or []:
            elements_seen += 1
            if item == "alpha":
                elements_with_alpha += 1

    collect()
    assert elements_seen > 0 and elements_with_alpha == elements_seen


@pytest.mark.hypothesis_nested
def test_body_binding_skipped_when_path_does_not_resolve(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"missing": {"values": ["X"]}},
            "parameters": {"body.notInSchema": {"dictionary": "missing"}},
        },
        _path_with_body({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    )
    operation = schema["/items"]["POST"]
    leaks = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal leaks
        if "notInSchema" in (case.body or {}):
            leaks += 1

    collect()
    assert leaks == 0


def test_body_binding_invalid_syntax_rejected_at_config_load():
    with pytest.raises(ConfigError, match="Only `\\[\\*\\]` is supported"):
        SchemathesisConfig.from_dict(
            {
                "dictionaries": {"x": {"values": ["X"]}},
                "parameters": {"body.items[3]": {"dictionary": "x"}},
            }
        )


@pytest.mark.hypothesis_nested
def test_body_binding_draws_recorded_on_meta(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"cc": {"values": ["1234-5678-9012-3456"]}},
            "parameters": {"body.ccNumber": {"dictionary": "cc"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {"ccNumber": {"type": "string"}},
                "required": ["ccNumber"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    body_draws: list[DictionaryDraw] = []

    @given(case=operation.as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        if case._meta is None:
            return
        body_draws.extend(d for d in case._meta.dictionary_draws if d.body_path is not None)

    collect()
    assert body_draws, "expected body_path-bearing draws on case._meta.dictionary_draws"
    assert all(d.body_path == "/ccNumber" and d.value == "1234-5678-9012-3456" for d in body_draws)


@pytest.mark.hypothesis_nested
def test_body_binding_true_subschema_accepts_entries(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"toks": {"values": ["sk-abc"]}},
            "parameters": {"body.token": {"dictionary": "toks"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {"token": True},
                "required": ["token"],
            }
        ),
        version="3.1.0",
    )
    operation = schema["/items"]["POST"]
    seen: list[DictionaryDraw] = []

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        if case._meta is None:
            return
        for draw_ in case._meta.dictionary_draws:
            if draw_.body_path == "/token":
                seen.append(draw_)

    collect()
    assert seen, "no entries substituted into body.token (true subschema misclassified)"
    assert all(d.matches_schema for d in seen), "true subschema must classify entries as matches_schema=True"


@pytest.mark.hypothesis_nested
def test_body_binding_resolves_through_boolean_items(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["seed"]}},
            "parameters": {"body.[*]": {"dictionary": "vals"}},
        },
        _path_with_body({"type": "array", "minItems": 1, "items": True}),
        version="3.1.0",
    )
    operation = schema["/items"]["POST"]
    elements_seen = 0
    elements_with_seed = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal elements_seen, elements_with_seed
        for item in case.body or []:
            elements_seen += 1
            if item == "seed":
                elements_with_seed += 1

    collect()
    assert elements_seen > 0 and elements_with_seed == elements_seen


@pytest.mark.hypothesis_nested
def test_body_binding_drops_descendant_mutations_under_overwrite(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"users": {"values": ["override"]}},
            "parameters": {"body.user": {"dictionary": "users"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {"email": {"type": "string", "minLength": 5}},
                        "required": ["email"],
                    },
                    "sibling": {"type": "string", "minLength": 5},
                },
                "required": ["user", "sibling"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    overwrites_seen = 0
    saw_sibling_mutation = False

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=40, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal overwrites_seen, saw_sibling_mutation
        if case._meta is None:
            return
        overrides = [d for d in case._meta.dictionary_draws if d.body_path == "/user"]
        if not overrides:
            return
        overwrites_seen += 1
        for mutation in case._meta.phase.data.mutations:
            assert not (mutation.path and mutation.path[:1] == ("user",)), (
                f"mutation descendant of overwritten /user not dropped: path={mutation.path!r}"
            )
            if mutation.path and mutation.path[:1] == ("sibling",):
                saw_sibling_mutation = True

    collect()
    assert overwrites_seen > 0, "scenario never produced a /user overwrite"
    assert saw_sibling_mutation, (
        "no sibling mutation observed in any overwrite case; absence-of-user-mutation could be vacuous"
    )


@pytest.mark.hypothesis_nested
def test_body_binding_probability_mixes_substituted_and_native(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"tokens": {"values": ["SENTINEL"]}},
            "parameters": {"body.token": {"dictionary": "tokens", "probability": 0.3}},
        },
        _path_with_body({"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}),
    )
    operation = schema["/items"]["POST"]
    substituted = 0
    native = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=40, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal substituted, native
        token = (case.body or {}).get("token")
        if token == "SENTINEL":
            substituted += 1
        elif token is not None:
            native += 1

    collect()
    assert substituted > 0 and native > 0, f"probability 0.3 did not mix outcomes: subst={substituted}, native={native}"


@pytest.mark.hypothesis_nested
def test_operation_scoped_body_binding_applies(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"tokens": {"values": ["OPERATION-SCOPED"]}},
            "operations": [
                {
                    "include-name": "POST /items",
                    "parameters": {"body.token": {"dictionary": "tokens"}},
                }
            ],
        },
        _path_with_body({"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}),
    )
    operation = schema["/items"]["POST"]
    seen_values: set[str] = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen_values.add((case.body or {}).get("token"))

    collect()
    assert "OPERATION-SCOPED" in seen_values


@pytest.mark.hypothesis_nested
def test_operation_scoped_body_binding_wins_over_global(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {
                "global": {"values": ["GLOBAL"]},
                "op": {"values": ["OPERATION"]},
            },
            "parameters": {"body.token": {"dictionary": "global"}},
            "operations": [
                {
                    "include-name": "POST /items",
                    "parameters": {"body.token": {"dictionary": "op"}},
                }
            ],
        },
        _path_with_body({"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}),
    )
    operation = schema["/items"]["POST"]
    seen_values: set[str] = set()

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        seen_values.add((case.body or {}).get("token"))

    collect()
    assert seen_values == {"OPERATION"}


@pytest.mark.hypothesis_nested
def test_body_binding_drops_indexed_mutation_under_wildcard_overwrite(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"replacements": {"values": ["ab"]}},
            "parameters": {"body.items[*]": {"dictionary": "replacements"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 5},
                    },
                    "sibling": {"type": "string", "minLength": 5},
                },
                "required": ["items", "sibling"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    overwrites_seen = 0
    saw_sibling_mutation = False

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=40, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal overwrites_seen, saw_sibling_mutation
        if case._meta is None:
            return
        overrides = [d for d in case._meta.dictionary_draws if d.body_path == "/items/*"]
        if not overrides:
            return
        overwrites_seen += 1
        for mutation in case._meta.phase.data.mutations:
            assert not (
                len(mutation.path) >= 2 and mutation.path[0] == "items" and isinstance(mutation.path[1], int)
            ), f"indexed mutation under /items/* overwrite not dropped: path={mutation.path!r}"
            if mutation.path and mutation.path[:1] == ("sibling",):
                saw_sibling_mutation = True

    collect()
    assert overwrites_seen > 0, "scenario never produced a /items/* overwrite"
    assert saw_sibling_mutation, (
        "no sibling mutation observed in any overwrite case; absence-of-items-mutation could be vacuous"
    )


@pytest.mark.hypothesis_nested
def test_body_binding_literal_and_non_body_keys_are_skipped(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["DICT"]}},
            "parameters": {
                "query.q": {"dictionary": "vals"},
                "body.literal": "LITERAL",
                "body.token": {"dictionary": "vals"},
            },
        },
        {
            "/items": {
                "post": {
                    "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "token": {"type": "string"},
                                        "literal": {"type": "string"},
                                    },
                                    "required": ["token"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    )
    operation = schema["/items"]["POST"]
    literal_applied_cases = 0
    token_dict_draws = 0
    bodies_seen = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal literal_applied_cases, token_dict_draws, bodies_seen
        if isinstance(case.body, dict):
            bodies_seen += 1
            if case.body.get("literal") == "LITERAL":
                literal_applied_cases += 1
        if case._meta is None:
            return
        token_dict_draws += sum(1 for d in case._meta.dictionary_draws if d.body_path == "/token")

    collect()
    assert bodies_seen > 0
    assert literal_applied_cases == bodies_seen, "literal body override must apply to every case"
    assert token_dict_draws > 0, "dictionary binding on sibling body key did not fire"


@pytest.mark.hypothesis_nested
def test_body_binding_skipped_when_descending_through_non_object_schema(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["X"]}},
            "parameters": {"body.token.deeper": {"dictionary": "vals"}},
        },
        _path_with_body({"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]}),
    )
    operation = schema["/items"]["POST"]
    leaks = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal leaks
        token = (case.body or {}).get("token")
        if isinstance(token, dict) and "deeper" in token:
            leaks += 1

    collect()
    assert leaks == 0


@pytest.mark.hypothesis_nested
def test_body_binding_skipped_when_wildcard_target_lacks_items(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["SENTINEL_UNIQUE_VALUE_12345"]}},
            "parameters": {"body.tags[*]": {"dictionary": "vals"}},
        },
        _path_with_body({"type": "object", "properties": {"tags": {"type": "array"}}, "required": ["tags"]}),
    )
    operation = schema["/items"]["POST"]
    leaks = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal leaks
        for item in (case.body or {}).get("tags", []):
            if item == "SENTINEL_UNIQUE_VALUE_12345":
                leaks += 1

    collect()
    assert leaks == 0


@pytest.mark.hypothesis_nested
def test_body_binding_returns_inner_when_all_entries_filtered(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"too_short": {"values": ["ab"]}},
            "parameters": {"body.token": {"dictionary": "too_short"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {"token": {"type": "string", "minLength": 5}},
                "required": ["token"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    body_draws = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=10, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal body_draws
        if case._meta is None:
            return
        body_draws += sum(1 for d in case._meta.dictionary_draws if d.body_path is not None)

    collect()
    assert body_draws == 0


@pytest.mark.hypothesis_nested
def test_body_binding_keeps_mutation_when_path_does_not_match(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"users": {"values": ["ab"]}},
            "parameters": {"body.user": {"dictionary": "users"}},
        },
        _path_with_body(
            {
                "type": "object",
                "properties": {
                    "user": {"type": "string", "minLength": 5},
                    "tag": {"type": "string", "minLength": 5},
                },
                "required": ["user", "tag"],
            }
        ),
    )
    operation = schema["/items"]["POST"]
    saw_kept_mutation = False

    @given(case=operation.as_strategy(generation_mode=GenerationMode.NEGATIVE))
    @settings(max_examples=40, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal saw_kept_mutation
        if case._meta is None:
            return
        body_overrides = [d for d in case._meta.dictionary_draws if d.body_path == "/user"]
        if not body_overrides:
            return
        for mutation in case._meta.phase.data.mutations:
            if mutation.path and mutation.path[0] == "tag":
                saw_kept_mutation = True

    collect()
    assert saw_kept_mutation, "expected mutation on sibling field `tag` to be preserved when /user is overwritten"


@pytest.mark.hypothesis_nested
def test_body_binding_skipped_when_descending_past_boolean_leaf(ctx):
    schema = _load_schema_with_dictionaries(
        ctx,
        {
            "dictionaries": {"vals": {"values": ["X"]}},
            "parameters": {"body.token.field": {"dictionary": "vals"}},
        },
        _path_with_body(
            {"type": "object", "properties": {"token": True}, "required": ["token"]},
        ),
        version="3.1.0",
    )
    operation = schema["/items"]["POST"]
    leaks = 0

    @given(case=operation.as_strategy())
    @settings(max_examples=5, derandomize=True, database=None, suppress_health_check=list(HealthCheck))
    def collect(case):
        nonlocal leaks
        token = (case.body or {}).get("token")
        if isinstance(token, dict) and "field" in token:
            leaks += 1

    collect()
    assert leaks == 0
