from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from schemathesis.config import SchemathesisConfig
from schemathesis.core.error_feedback import ErrorFeedbackStore, Observation, ObservationKind
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation import GenerationMode
from schemathesis.generation.dictionaries import (
    DictionaryDraw,
    build_dictionary_overlay_strategy,
    resolve_parameter_bindings,
)
from schemathesis.resources import SemanticDraw
from schemathesis.specs.openapi.negative import GeneratedValue


def _load_schema_with_dictionaries(ctx, config: dict, paths: dict):
    schema = ctx.openapi.load_schema(paths)
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
