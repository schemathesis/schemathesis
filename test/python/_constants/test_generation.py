import json
import random

import graphql
import jsonschema_rs
import pytest
from hypothesis import HealthCheck, Phase, find, given, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample

import schemathesis
from schemathesis.config import GenerationConfig, SchemathesisConfig
from schemathesis.generation.body_overrides import build_body_override_overlay_strategy
from schemathesis.generation.meta import CaseMetadata, CoverageScenario, GenerationInfo, PhaseInfo
from schemathesis.generation.modes import GenerationMode
from schemathesis.generation.value import GeneratedValue
from schemathesis.python._constants.adapters import default_adapters
from schemathesis.python._constants.orchestrator import extract_all, extract_registered
from schemathesis.python._constants.pool import ConstantDraw, ConstantEntry, ConstantsPool, Origin
from schemathesis.python._constants.registry import SourceRegistry, default_registry
from schemathesis.specs.graphql.substitution import substitute_constants
from schemathesis.specs.openapi._hypothesis import _build_form_strategy_with_encoding
from schemathesis.specs.openapi.adapter.parameters import (
    _prune_overwritten_body_constants,
    build_constants_overlay_strategy,
)
from schemathesis.specs.openapi.negative import (
    wrap_flatmap_hook_for_generated_value,
    wrap_map_hook_for_generated_value,
)
from schemathesis.transport.serialization import Binary
from test.python._constants.fixtures import (
    buggy_app,
    buggy_asgi_app,
    buggy_path_app,
    buggy_query_app,
    graphql_app,
    graphql_string_pool,
)

# Constant substitution is probabilistic; `find` drives generation until it fires (or proves it can't).
_FIND = settings(max_examples=200, database=None)


def _app_constants(source, *, name="provider"):
    """Harvest constants from an app/module the way a registered source would, without the engine."""

    def provider():
        return source

    provider.__name__ = name
    registry = SourceRegistry()
    registry.register(provider)
    return extract_all(registry=registry, adapters=default_adapters())


def _harvested(source, type_):
    return [entry.value for entry in _app_constants(source).entries_for(type_)]


@pytest.fixture
def _clean_registry():
    default_registry().clear()
    yield
    default_registry().clear()


def test_constant_from_wsgi_app_unlocks_bug():
    # The high-entropy unlock code exists only in the app source; reaching it requires harvesting it.
    assert buggy_app.UNLOCK_CODE in _harvested(buggy_app.app, "string")
    operation = schemathesis.openapi.from_dict(buggy_app.SCHEMA)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(
            generation_mode=GenerationMode.POSITIVE, constants_value_source=_source("string", buggy_app.UNLOCK_CODE)
        ),
        lambda case: isinstance(case.body, dict) and case.body.get("code") == buggy_app.UNLOCK_CODE,
        settings=_FIND,
    )
    assert case.body["code"] == buggy_app.UNLOCK_CODE


def test_constant_from_fastapi_app_unlocks_bug():
    assert buggy_asgi_app.UNLOCK_CODE in _harvested(buggy_asgi_app.app, "string")
    operation = schemathesis.openapi.from_asgi("/openapi.json", app=buggy_asgi_app.app)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(
            generation_mode=GenerationMode.POSITIVE,
            constants_value_source=_source("string", buggy_asgi_app.UNLOCK_CODE),
        ),
        lambda case: isinstance(case.body, dict) and case.body.get("code") == buggy_asgi_app.UNLOCK_CODE,
        settings=_FIND,
    )
    assert case.body["code"] == buggy_asgi_app.UNLOCK_CODE


@pytest.mark.usefixtures("_clean_registry")
def test_registered_constants_apply_to_direct_operation_strategy():
    @schemathesis.python.constants
    def source():
        return graphql_string_pool

    operation = schemathesis.openapi.from_dict(buggy_app.SCHEMA)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(),
        lambda case: (
            case._meta is not None
            and any(draw.value == graphql_string_pool.TOKEN for draw in case._meta.constants_draws)
        ),
        settings=settings(max_examples=200, database=None),
    )

    assert case.body["code"] == graphql_string_pool.TOKEN


@pytest.mark.usefixtures("_clean_registry")
def test_registered_constants_apply_to_direct_state_machine():
    @schemathesis.python.constants
    def source():
        return graphql_string_pool

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])

    class Workflow(schema.as_state_machine()):
        constant_was_used = False

        # Only the generated cases matter here; skip checks so an unrelated response never aborts the run.
        def validate_response(self, *args, **kwargs):
            pass

        def before_call(self, case):
            if case._meta is not None and any(
                draw.value == graphql_string_pool.TOKEN for draw in case._meta.constants_draws
            ):
                type(self).constant_was_used = True

    Workflow.run(
        settings=settings(
            max_examples=50,
            database=None,
            deadline=None,
            phases=[Phase.generate],
            stateful_step_count=1,
            suppress_health_check=list(HealthCheck),
        )
    )

    assert Workflow.constant_was_used


def test_constant_applied_to_body_in_fuzz_mode():
    pool = _app_constants(buggy_app.app)
    operation = schemathesis.openapi.from_dict(buggy_app.SCHEMA)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE, constants_value_source=pool),
        lambda case: case._meta is not None and any(d.parameter_name == "code" for d in case._meta.constants_draws),
        settings=_FIND,
    )
    assert any(d.parameter_name == "code" for d in case._meta.constants_draws)


def test_constant_applied_to_query_parameter():
    pool = _app_constants(buggy_query_app.app)
    operation = schemathesis.openapi.from_dict(buggy_query_app.SCHEMA)["/unlock"]["GET"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE, constants_value_source=pool),
        lambda case: case._meta is not None and any(d.parameter_name == "code" for d in case._meta.constants_draws),
        settings=_FIND,
    )
    draw = next(d for d in case._meta.constants_draws if d.parameter_name == "code")
    assert case.query["code"] == draw.value


def test_constants_respect_allow_x00(ctx):
    # A NUL harvested from the app's source must not reach data when the user disabled `\x00`.
    schema = ctx.openapi.load_schema(
        {
            "/unlock": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"code": {"type": "string"}},
                                    "required": ["code"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    schema.config.generation.update(allow_x00=False)
    operation = schema["/unlock"]["POST"]
    with pytest.raises(NoSuchExample):
        find(
            operation.as_strategy(
                generation_mode=GenerationMode.POSITIVE, constants_value_source=_source("string", "pre\x00post")
            ),
            lambda case: isinstance(case.body, dict) and "\x00" in str(case.body.get("code", "")),
            settings=_FIND,
        )


def test_constants_not_substituted_into_security_parameters(ctx):
    # A harvested key makes "generated" auth valid, so `ignored_auth` reports the API accepting it.
    schema = ctx.openapi.load_schema(
        {"/data": {"get": {"security": [{"ApiKeyQuery": []}], "responses": {"200": {"description": "OK"}}}}},
        components={"securitySchemes": {"ApiKeyQuery": {"type": "apiKey", "in": "query", "name": "api_key"}}},
    )
    operation = schema["/data"]["GET"]
    with pytest.raises(NoSuchExample):
        find(
            operation.as_strategy(
                generation_mode=GenerationMode.POSITIVE, constants_value_source=_source("string", "42")
            ),
            lambda case: (
                case._meta is not None and any(draw.parameter_name == "api_key" for draw in case._meta.constants_draws)
            ),
            settings=_FIND,
        )


def test_constant_applied_to_integer_path_parameter():
    pool = _app_constants(buggy_path_app.app)
    operation = schemathesis.openapi.from_dict(buggy_path_app.SCHEMA)["/item/{item_id}"]["GET"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE, constants_value_source=pool),
        lambda case: case._meta is not None and any(d.parameter_name == "item_id" for d in case._meta.constants_draws),
        settings=_FIND,
    )
    draw = next(d for d in case._meta.constants_draws if d.parameter_name == "item_id")
    assert draw.value == buggy_path_app.MAGIC_ID
    # The positive-integer path bias must not rewrite a substituted (negative) literal.
    assert case.path_parameters["item_id"] == draw.value


def test_bug_stays_unfindable_without_the_feature():
    # No source: the overlay is off, so no constant provenance is ever recorded.
    operation = schemathesis.openapi.from_dict(buggy_app.SCHEMA)["/unlock"]["POST"]
    with pytest.raises(NoSuchExample):
        find(
            operation.as_strategy(generation_mode=GenerationMode.POSITIVE, constants_value_source=None),
            lambda case: case._meta is not None and bool(case._meta.constants_draws),
            settings=_FIND,
        )


def test_constant_usage_recorded_in_case_metadata():
    pool = _app_constants(buggy_app.app)
    operation = schemathesis.openapi.from_dict(buggy_app.SCHEMA)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE, constants_value_source=pool),
        lambda case: case._meta is not None and any(d.parameter_name == "code" for d in case._meta.constants_draws),
        settings=_FIND,
    )
    draws = [draw for draw in case._meta.constants_draws if draw.parameter_name == "code"]
    assert draws, "constant substitutions into `code` should be recorded on the case metadata"
    draw = draws[0]
    assert draw.location == "body"
    assert isinstance(draw.value, str)
    assert draw.origin.source == "provider"
    assert draw.origin.module == "test.python._constants.fixtures.buggy_app"


@pytest.mark.usefixtures("_clean_registry")
def test_constants_auto_extracted_from_wsgi_app():
    # No registered source and no explicit pool: loading a WSGI app introspects its modules by default.
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE),
        lambda case: case._meta is not None and bool(case._meta.constants_draws),
        settings=_FIND,
    )
    assert any(draw.origin.source == "application" for draw in case._meta.constants_draws)


@pytest.mark.usefixtures("_clean_registry")
def test_app_constants_are_cached_across_strategy_builds():
    calls = 0

    @schemathesis.python.constants
    def source():
        nonlocal calls
        calls += 1
        return graphql_string_pool

    operation = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app)["/unlock"]["POST"]

    operation.as_strategy()
    operation.as_strategy()

    assert calls == 1


@pytest.mark.usefixtures("_clean_registry")
def test_app_constants_cache_survives_schema_clone():
    # `@schema.parametrize()` clones the schema per test function; each clone must not re-import the app.
    calls = 0

    @schemathesis.python.constants
    def source():
        nonlocal calls
        calls += 1
        return graphql_string_pool

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app)

    schema["/unlock"]["POST"].as_strategy()
    schema.clone()["/unlock"]["POST"].as_strategy()

    assert calls == 1


@pytest.mark.usefixtures("_clean_registry")
def test_app_constants_cache_is_invalidated_when_registry_changes():
    calls = []

    @schemathesis.python.constants
    def first_source():
        calls.append("first")
        return graphql_string_pool

    operation = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app)["/unlock"]["POST"]
    operation.as_strategy()

    @schemathesis.python.constants
    def second_source():
        calls.append("second")
        return graphql_string_pool

    operation.as_strategy()

    assert calls == ["first", "first", "second"]


@pytest.mark.usefixtures("_clean_registry")
def test_constants_auto_extracted_from_asgi_app():
    operation = schemathesis.openapi.from_asgi("/openapi.json", app=buggy_asgi_app.app)["/unlock"]["POST"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE),
        lambda case: case._meta is not None and bool(case._meta.constants_draws),
        settings=_FIND,
    )
    assert any(draw.origin.source == "application" for draw in case._meta.constants_draws)


@pytest.mark.usefixtures("_clean_registry")
def test_auto_extraction_disabled_by_config():
    config = SchemathesisConfig.from_str("[analysis.constants]\nenabled = false\n")
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app, config=config)["/unlock"]["POST"]
    with pytest.raises(NoSuchExample):
        find(
            operation.as_strategy(generation_mode=GenerationMode.POSITIVE),
            lambda case: case._meta is not None and bool(case._meta.constants_draws),
            settings=_FIND,
        )


def _graphql_operations():
    schema = schemathesis.graphql.from_wsgi("/graphql", app=graphql_app.app)
    return {result.ok().label: result.ok() for result in schema.get_all_operations() if result.ok() is not None}


def _graphql_case(operation, pool, predicate):
    return find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE, constants_value_source=pool),
        predicate,
        settings=_FIND,
    )


def _has_any_draw(case):
    return case._meta is not None and bool(case._meta.constants_draws)


def test_constant_applied_to_graphql_argument():
    assert graphql_app.SECRET_CODE in _harvested(graphql_app, "string")
    operation = _graphql_operations()["Query.lookup"]
    case = _graphql_case(
        operation,
        _source("string", graphql_app.SECRET_CODE),
        lambda case: (
            case._meta is not None and any(d.value == graphql_app.SECRET_CODE for d in case._meta.constants_draws)
        ),
    )
    assert any(d.value == graphql_app.SECRET_CODE for d in case._meta.constants_draws)


@pytest.mark.usefixtures("_clean_registry")
def test_constants_auto_extracted_from_graphql_wsgi_app():
    # The strawberry request handler is a library view, but Flask records the app's own module,
    # so its resolver literals are reached without manual registration.
    operation = _graphql_operations()["Query.lookup"]
    case = find(
        operation.as_strategy(generation_mode=GenerationMode.POSITIVE),
        lambda case: case._meta is not None and bool(case._meta.constants_draws),
        settings=_FIND,
    )
    assert any(draw.origin.source == "application" for draw in case._meta.constants_draws)


def test_graphql_constant_usage_recorded_in_case_metadata():
    pool = _app_constants(graphql_app)
    operation = _graphql_operations()["Query.lookup"]
    case = _graphql_case(
        operation,
        pool,
        lambda case: case._meta is not None and any(d.body_path == "/lookup/code" for d in case._meta.constants_draws),
    )
    draw = next(d for d in case._meta.constants_draws if d.body_path == "/lookup/code")
    assert draw.location == "body"
    assert draw.parameter_name == "code"
    assert draw.origin.source == "provider"
    assert draw.origin.module == "test.python._constants.fixtures.graphql_app"


def test_graphql_constants_cover_scalar_shapes():
    pool = _app_constants(graphql_app)
    operations = _graphql_operations()

    def has_path(path):
        return lambda case: case._meta is not None and any(d.body_path == path for d in case._meta.constants_draws)

    def has_value(path, value):
        return lambda case: (
            case._meta is not None and any(d.body_path == path and d.value == value for d in case._meta.constants_draws)
        )

    # Integer, float, ID, list-element, input-object-field, and nested-object-field arguments each substitute.
    number = _graphql_case(operations["Query.byNumber"], pool, has_path("/byNumber/n"))
    assert any(d.value == graphql_app.SECRET_NUMBER for d in number._meta.constants_draws)
    # `Float` accepts integer literals too, so pin the value we assert on the wire.
    ratio = _graphql_case(operations["Query.byRatio"], pool, has_value("/byRatio/r", graphql_app.SECRET_RATIO))
    _graphql_case(operations["Query.byId"], pool, has_path("/byId/id"))
    _graphql_case(operations["Query.byTags"], pool, has_path("/byTags/tags"))
    _graphql_case(operations["Query.byFilter"], pool, has_path("/byFilter/filter/code"))
    _graphql_case(operations["Query.byFilter"], pool, has_path("/byFilter/filter/size"))
    _graphql_case(operations["Query.container"], pool, has_path("/container/item/code"))
    # Non-string scalars render as bare literals, not quoted strings.
    assert f"n: {graphql_app.SECRET_NUMBER}" in number.body
    assert f"r: {graphql_app.SECRET_RATIO}" in ratio.body
    # Boolean scalars and enum arguments have no pool type mapping and are never substituted.
    with pytest.raises(NoSuchExample):
        _graphql_case(operations["Query.byFlag"], pool, _has_any_draw)
    with pytest.raises(NoSuchExample):
        _graphql_case(operations["Query.byColor"], pool, _has_any_draw)


def test_graphql_numeric_argument_untouched_without_numeric_constants():
    pool = _app_constants(graphql_string_pool)
    operations = _graphql_operations()
    _graphql_case(
        operations["Query.lookup"],
        pool,
        lambda case: (
            case._meta is not None and any(d.value == graphql_string_pool.TOKEN for d in case._meta.constants_draws)
        ),
    )
    # No integer or float constants in the pool, so numeric arguments get nothing.
    with pytest.raises(NoSuchExample):
        _graphql_case(operations["Query.byNumber"], pool, _has_any_draw)
    with pytest.raises(NoSuchExample):
        _graphql_case(operations["Query.byRatio"], pool, _has_any_draw)


def _source(type_, *values):
    pool = ConstantsPool()
    for value in values:
        pool.add(ConstantEntry(value=value, type=type_, origins=(Origin(source="s", module="m", adapter=None),)))
    return pool


def _constant_draw(name, value, *, body_path=None):
    return ConstantDraw(
        location="body" if body_path else "query",
        parameter_name=name,
        value=value,
        origin=Origin(source="s", module="m", adapter=None),
        body_path=body_path,
    )


def _parse_graphql(query):
    operation = graphql.parse(query).definitions[0]
    assert isinstance(operation, graphql.OperationDefinitionNode)
    return operation


_GRAPHQL_SCALAR_SCHEMA = graphql.build_schema(
    "type Query { lookup(code: String): Boolean byNumber(n: Int): Boolean byRatio(r: Float): Boolean }"
)


@pytest.mark.parametrize(
    ("value", "substituted"),
    [
        (2**31 - 1, True),
        (-(2**31), True),
        (2**31, False),
        (2**40, False),
        (-(2**31) - 1, False),
    ],
)
def test_graphql_int_scalar_rejects_out_of_range_constants(value, substituted):
    draws = substitute_constants(
        operation_node=_parse_graphql("{ byNumber(n: 0) }"),
        client_schema=_GRAPHQL_SCALAR_SCHEMA,
        pool=_source("integer", value),
        random=random.Random(0),
        probability=1.0,
    )
    assert bool(draws) is substituted


def test_graphql_meta_field_does_not_abort_substitution():
    draws = substitute_constants(
        operation_node=_parse_graphql('{ lookup(code: "x") __typename }'),
        client_schema=_GRAPHQL_SCALAR_SCHEMA,
        pool=_source("string", "REPLACED"),
        random=random.Random(0),
        probability=1.0,
    )
    assert [draw.parameter_name for draw in draws] == ["code"]


def test_graphql_float_scalar_rejects_overflowing_integer_constant():
    # `float(10**400)` overflows; such an integer can never be a finite `Float` literal, so it must be
    # filtered out rather than crash the draw with `OverflowError`.
    draws = substitute_constants(
        operation_node=_parse_graphql("{ byRatio(r: 1.0) }"),
        client_schema=_GRAPHQL_SCALAR_SCHEMA,
        pool=_source("integer", 10**400),
        random=random.Random(0),
        probability=1.0,
    )
    assert draws == []


def test_stateful_prune_keeps_non_body_constant_draws():
    query_draw = _constant_draw("q", "QVAL")
    kept_body = _constant_draw("a", "AVAL", body_path="/a")
    overwritten_body = _constant_draw("b", "BVAL", body_path="/b")
    body = {"a": "AVAL", "b": "CHANGED"}
    assert _prune_overwritten_body_constants((query_draw, kept_body, overwritten_body), body) == (query_draw, kept_body)


@pytest.mark.usefixtures("_clean_registry")
def test_registered_constants_extracted_once_until_registry_changes():
    calls = []

    @schemathesis.python.constants
    def source():
        calls.append(1)
        return graphql_string_pool

    first = extract_registered()
    assert extract_registered() is first
    assert calls == [1]

    @schemathesis.python.constants
    def another():
        calls.append(1)
        return graphql_string_pool

    assert extract_registered() is not first


def test_map_hook_prunes_only_modified_constant_draw():
    changed = _constant_draw("code", "CONSTANT")
    unchanged = _constant_draw("name", "KEPT")
    generated = GeneratedValue(
        value={"code": "CONSTANT", "name": "KEPT"},
        meta=None,
        constants_draws=(changed, unchanged),
    )

    def hook(value):
        value["code"] = "changed"
        return value

    produced = wrap_map_hook_for_generated_value(hook)(generated)

    assert produced.constants_draws == (unchanged,)


def test_flatmap_hook_prunes_only_modified_constant_draw():
    changed = _constant_draw("code", "CONSTANT", body_path="/payload/code")
    unchanged = _constant_draw("name", "KEPT", body_path="/payload/name")
    generated = GeneratedValue(
        value={"payload": {"code": "CONSTANT", "name": "KEPT"}},
        meta=None,
        constants_draws=(changed, unchanged),
    )
    strategy = wrap_flatmap_hook_for_generated_value(
        lambda value: st.just({"payload": {**value["payload"], "code": "changed"}})
    )(generated)

    produced = find(strategy, lambda value: True)

    assert produced.constants_draws == (unchanged,)


def test_noop_map_and_flatmap_hooks_keep_constant_draws():
    draw = _constant_draw("code", "CONSTANT")
    generated = GeneratedValue(value={"code": "CONSTANT"}, meta=None, constants_draws=(draw,))

    mapped = wrap_map_hook_for_generated_value(lambda value: value)(generated)
    flatmapped = find(
        wrap_flatmap_hook_for_generated_value(lambda value: st.just(value))(generated),
        lambda value: True,
    )

    assert mapped.constants_draws == (draw,)
    assert flatmapped.constants_draws == (draw,)


def _draw(data, source, *, schema_properties, container_schema=None):
    strategy = build_constants_overlay_strategy(
        st.just({"blob": "placeholder"}),
        source=source,
        schema_properties=schema_properties,
        validator_cls=jsonschema_rs.Draft4Validator,
        location="body",
        container_schema=container_schema,
        generation_config=GenerationConfig(),
        probability=1.0,
    )
    produced = data.draw(strategy)
    value = produced.value if isinstance(produced, GeneratedValue) else produced
    return value["blob"]


@given(data=st.data())
@settings(max_examples=5)
def test_oversized_binary_constant_is_not_substituted(data):
    source = _source("bytes", b"way_too_long_binary_blob")
    schema = {"type": "string", "format": "binary", "maxLength": 4}
    assert _draw(data, source, schema_properties={"blob": schema}) == "placeholder"


@given(data=st.data())
@settings(max_examples=5)
def test_valid_binary_constant_is_substituted(data):
    schema = {"type": "string", "format": "binary", "maxLength": 4}
    blob = _draw(data, _source("bytes", b"OK"), schema_properties={"blob": schema})
    assert isinstance(blob, Binary)
    assert blob.data == b"OK"


@pytest.mark.parametrize(
    "constraint",
    [{"enum": [""]}, {"const": ""}, {"pattern": "^allowed$"}],
    ids=["enum", "const", "pattern"],
)
@given(data=st.data())
@settings(max_examples=5)
def test_constrained_binary_constant_is_not_substituted(data, constraint):
    schema = {"type": "string", "format": "binary", **constraint}
    assert _draw(data, _source("bytes", b"OK"), schema_properties={"blob": schema}) == "placeholder"


@given(data=st.data())
@settings(max_examples=5)
def test_undersized_binary_constant_is_not_substituted(data):
    schema = {"type": "string", "format": "binary", "minLength": 4}
    assert _draw(data, _source("bytes", b"x"), schema_properties={"blob": schema}) == "placeholder"


@given(data=st.data())
@settings(max_examples=5)
def test_typeless_object_property_is_not_scalar_substituted(data):
    # A property that is object-shaped (has `properties`) but omits an explicit `type` must not be
    # replaced by a scalar constant -- that would corrupt the body structure.
    source = _source("string", "SCALAR_CONST")
    blob = _draw(data, source, schema_properties={"blob": {"properties": {"code": {"type": "string"}}}})
    assert blob == "placeholder"


@given(data=st.data())
@settings(max_examples=5)
def test_format_constrained_string_constant_is_not_substituted(data):
    # A string leaf with a semantic `format` is generated by a dedicated format-aware strategy.
    # `format` is annotation-only for formats the validator does not recognize (here `phone`), so the
    # leaf validator accepts a type-valid literal that violates the format; injecting it produces
    # positive data the SUT rejects (the real django-modern-rest `phone` failure). Skip it.
    source = _source("string", "utf8")
    schema = {"type": "string", "format": "phone"}
    assert _draw(data, source, schema_properties={"blob": schema}) == "placeholder"


@given(data=st.data())
@settings(max_examples=5)
def test_validator_enforced_format_string_constant_is_substituted(data):
    # `email` is enforced by the leaf validator (`validate_formats=True`), so a valid harvested
    # email is a safe substitution -- the validator filters any format-invalid literal itself.
    source = _source("string", "user@example.com")
    schema = {"type": "string", "format": "email"}
    assert _draw(data, source, schema_properties={"blob": schema}) == "user@example.com"


@given(data=st.data())
@settings(max_examples=5)
def test_unvalidated_numeric_format_constant_is_not_substituted(data):
    # `int32` is annotation-only: `jsonschema_rs` does not enforce it, so an out-of-range integer
    # slips past leaf validation and clobbers the format-aware value with data the SUT rejects.
    source = _source("integer", 5_000_000_000)
    schema = {"type": "integer", "format": "int32"}
    assert _draw(data, source, schema_properties={"blob": schema}) == "placeholder"


@given(data=st.data())
@settings(max_examples=5)
def test_integer_constant_is_substituted(data):
    blob = _draw(data, _source("integer", 4096), schema_properties={"blob": {"type": "integer"}})
    assert blob == 4096


@pytest.mark.parametrize(
    "extra",
    [{"weird": "not-a-schema"}, {"ghost": {"type": "string"}}],
    ids=["non-dict-property-schema", "candidate-absent-from-value"],
)
@given(data=st.data())
@settings(max_examples=5)
def test_extra_property_does_not_break_substitution(data, extra):
    blob = _draw(data, _source("string", "SUBSTITUTE_ME"), schema_properties={"blob": {"type": "string"}, **extra})
    assert blob == "SUBSTITUTE_ME"


@given(data=st.data())
@settings(max_examples=5)
def test_overlay_substitutes_into_generated_value_keeping_provenance(data):
    strategy = build_constants_overlay_strategy(
        st.just(GeneratedValue(value={"blob": "placeholder"}, meta=None)),
        source=_source("string", "SUBSTITUTE_ME"),
        schema_properties={"blob": {"type": "string"}},
        validator_cls=jsonschema_rs.Draft4Validator,
        location="body",
        generation_config=GenerationConfig(),
        probability=1.0,
    )
    produced = data.draw(strategy)
    assert isinstance(produced, GeneratedValue)
    assert produced.value["blob"] == "SUBSTITUTE_ME"
    assert any(draw.parameter_name == "blob" for draw in produced.constants_draws)


def test_nested_non_body_parameter_is_not_substituted():
    schema_properties = {
        "filter": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
        }
    }
    strategy = build_constants_overlay_strategy(
        st.just({"filter": {"code": "ORIGINAL"}}),
        source=_source("string", "SUBSTITUTE_ME"),
        schema_properties=schema_properties,
        validator_cls=jsonschema_rs.Draft4Validator,
        location="query",
        container_schema={"type": "object", "properties": schema_properties},
        generation_config=GenerationConfig(),
        probability=1.0,
    )

    produced = find(strategy, lambda value: True)

    assert produced == {"filter": {"code": "ORIGINAL"}}


def test_binary_constant_not_substituted_into_non_body_parameter():
    # `bytes`/`binary` values belong only in a request body; a query/header/path location must never
    # receive a `Binary` object, whose serialization path expects a scalar.
    schema_properties = {"blob": {"type": "string", "format": "binary"}}
    strategy = build_constants_overlay_strategy(
        st.just({"blob": "ORIGINAL"}),
        source=_source("bytes", b"OK"),
        schema_properties=schema_properties,
        validator_cls=jsonschema_rs.Draft4Validator,
        location="query",
        container_schema={"type": "object", "properties": schema_properties},
        generation_config=GenerationConfig(),
        probability=1.0,
    )

    produced = find(strategy, lambda value: True)

    assert produced == {"blob": "ORIGINAL"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("TOKEN123", "TOKEN123"), ("tok\U0001f600en", "ORIGINAL")],
    ids=["header-safe", "non-latin1"],
)
def test_header_string_constant_filtered_by_header_validity(value, expected):
    # A header/cookie value that `is_valid_header` rejects would make the location filter discard the
    # whole generated case; screen such string constants out at substitution time instead.
    schema_properties = {"X-Token": {"type": "string"}}
    strategy = build_constants_overlay_strategy(
        st.just({"X-Token": "ORIGINAL"}),
        source=_source("string", value),
        schema_properties=schema_properties,
        validator_cls=jsonschema_rs.Draft4Validator,
        location="header",
        container_schema={"type": "object", "properties": schema_properties},
        generation_config=GenerationConfig(),
        probability=1.0,
    )

    produced = find(strategy, lambda v: True)

    result = produced.value if isinstance(produced, GeneratedValue) else produced
    assert result["X-Token"] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("safe-token", "safe-token"), ("abc;def", "ORIGINAL")],
    ids=["allowed", "excluded-character"],
)
def test_header_string_constant_filtered_by_excluded_characters(value, expected):
    # `exclude-header-characters` bounds generated headers; a constant must not smuggle the char back in.
    schema_properties = {"X-Token": {"type": "string"}}
    strategy = build_constants_overlay_strategy(
        st.just({"X-Token": "ORIGINAL"}),
        source=_source("string", value),
        schema_properties=schema_properties,
        validator_cls=jsonschema_rs.Draft4Validator,
        location="header",
        container_schema={"type": "object", "properties": schema_properties},
        generation_config=GenerationConfig(exclude_header_characters=";"),
        probability=1.0,
    )

    produced = find(strategy, lambda v: True)

    result = produced.value if isinstance(produced, GeneratedValue) else produced
    assert result["X-Token"] == expected


_FORM_SCHEMA = {
    "openapi": "3.0.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/upload": {
            "post": {
                "requestBody": {
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "blob": {"type": "string", "format": "binary"},
                                    "label": {"type": "string"},
                                },
                                "required": ["blob", "label"],
                            },
                            "encoding": {"blob": {"contentType": "application/octet-stream, application/pdf"}},
                        }
                    }
                },
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def test_form_body_records_constant_provenance(ctx):
    schema = ctx.openapi.from_full_schema(_FORM_SCHEMA)
    operation = schema["/upload"]["POST"]
    config = schema.config.generation_for(operation=operation, phase="fuzzing")
    strategy = _build_form_strategy_with_encoding(
        operation.body[0], operation, config, GenerationMode.POSITIVE, _source("string", "MAGIC_LABEL")
    )
    generated = find(
        strategy,
        lambda result: (
            isinstance(result, GeneratedValue)
            and any(draw.parameter_name == "label" for draw in result.constants_draws)
        ),
    )
    draw = next(draw for draw in generated.constants_draws if draw.parameter_name == "label")
    assert draw.value == "MAGIC_LABEL"
    assert draw.location == "body"


@given(data=st.data())
@settings(max_examples=5)
def test_constant_substituted_into_bundled_ref_property(data):
    container = {
        "type": "object",
        "properties": {"blob": {"$ref": "#/x-bundled/Blob"}},
        "x-bundled": {"Blob": {"type": "string", "maxLength": 4}},
    }
    blob = _draw(
        data,
        _source("string", "way_too_long_string_value", "OK"),
        schema_properties=container["properties"],
        container_schema=container,
    )
    assert blob == "OK"


@given(data=st.data())
@settings(max_examples=5)
def test_substitution_reverted_when_it_breaks_container_validation(data):
    # `TOOLONG` is valid against the leaf schema (no length limit) but violates the container's
    # `maxLength`, so the substitution must be reverted rather than emitted.
    container = {"type": "object", "properties": {"blob": {"type": "string", "maxLength": 3}}}
    blob = _draw(
        data,
        _source("string", "TOOLONG"),
        schema_properties={"blob": {"type": "string"}},
        container_schema=container,
    )
    assert blob == "placeholder"


def _nested_body_schema(depth):
    schema = {
        "type": "object",
        "properties": {"code": {"type": "string", "minLength": 16, "maxLength": 16}},
        "required": ["code"],
    }
    path = ["code"]
    for index in reversed(range(depth)):
        name = f"level{index}"
        schema = {"type": "object", "properties": {name: schema}, "required": [name]}
        path.insert(0, name)
    return schema, "/" + "/".join(path)


@pytest.mark.parametrize(
    ("body_schema", "body_path"),
    [
        (
            {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"code": {"type": "string", "minLength": 16}},
                        "required": ["code"],
                    },
                    {"type": "object", "properties": {"code": {"maxLength": 16}}},
                ]
            },
            "/code",
        ),
        (
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"code": {"type": "string", "minLength": 16, "maxLength": 16}},
                        "required": ["code"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                        "additionalProperties": False,
                    },
                ]
            },
            "/code",
        ),
        (
            {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"code": {"type": "string", "minLength": 16, "maxLength": 16}},
                        "required": ["code"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                        "additionalProperties": False,
                    },
                ]
            },
            "/code",
        ),
        _nested_body_schema(1),
    ],
    ids=["allOf", "oneOf", "anyOf", "nested"],
)
def test_constant_substituted_into_composed_or_nested_body(ctx, body_schema, body_path):
    raw_schema = {
        "openapi": "3.0.0",
        "info": {"title": "test", "version": "1.0"},
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": body_schema}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    operation = ctx.openapi.from_full_schema(raw_schema)["/items"]["POST"]
    value = "a3f9c1e7b5d24680"

    case = find(
        operation.as_strategy(
            generation_mode=GenerationMode.POSITIVE,
            constants_value_source=_source("string", value),
        ),
        lambda case: (
            case._meta is not None
            and any(draw.body_path == body_path and draw.value == value for draw in case._meta.constants_draws)
        ),
    )

    actual = case.body
    for segment in body_path[1:].split("/"):
        actual = actual[segment]
    assert actual == value


def test_body_override_removes_overridden_constant_provenance():
    origin = Origin(source="s", module="m", adapter=None)
    overridden = ConstantDraw(
        location="body",
        parameter_name="code",
        value="CONSTANT",
        origin=origin,
        body_path="/payload/code",
    )
    sibling = ConstantDraw(
        location="body",
        parameter_name="name",
        value="KEPT",
        origin=origin,
        body_path="/payload/name",
    )
    strategy = build_body_override_overlay_strategy(
        st.just(
            GeneratedValue(
                value={"payload": {"code": "CONSTANT", "name": "KEPT"}},
                meta=None,
                constants_draws=(overridden, sibling),
            )
        ),
        overrides={"/payload/code": "OVERRIDE"},
    )

    produced = find(strategy, lambda value: True)

    assert produced.value == {"payload": {"code": "OVERRIDE", "name": "KEPT"}}
    assert produced.constants_draws == (sibling,)


def _meta_with_constant(value):
    return CaseMetadata(
        generation=GenerationInfo(time=0.0, mode=GenerationMode.POSITIVE),
        components={},
        phase=PhaseInfo.coverage(CoverageScenario.VALID_STRING, "desc"),
        constants_draws=(
            ConstantDraw(
                location="body",
                parameter_name="blob",
                value=value,
                origin=Origin(source="s", module="m", adapter=None),
                body_path="/payload/blob",
            ),
        ),
    )


@pytest.mark.parametrize("value", [b"tok_secret", "ACTIVE", 42, 3.14], ids=["bytes", "string", "integer", "float"])
def test_constant_draw_survives_metadata_round_trip(value):
    meta = _meta_with_constant(value)
    restored = CaseMetadata.from_dict(json.loads(json.dumps(meta.to_dict())))
    assert restored.constants_draws[0].value == value
    assert restored.constants_draws[0].body_path == "/payload/blob"


@pytest.mark.usefixtures("_clean_registry")
def test_stateful_body_merge_prunes_overwritten_constant_provenance():
    @schemathesis.python.constants
    def source():
        return graphql_string_pool

    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=buggy_app.app)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])

    class Workflow(schema.as_state_machine()):
        merged_case_seen = False
        stale_provenance_seen = False

        # The link's merge is driven by the recorded response, not checks; skip checks so an
        # unrelated response never aborts the run.
        def validate_response(self, *args, **kwargs):
            pass

        def before_call(self, case):
            if isinstance(case.body, dict) and case.body.get("code") == buggy_app.LINKED_CODE:
                type(self).merged_case_seen = True
                if case._meta is not None and any(draw.parameter_name == "code" for draw in case._meta.constants_draws):
                    type(self).stale_provenance_seen = True

    Workflow.run(
        settings=settings(
            max_examples=100,
            database=None,
            deadline=None,
            phases=[Phase.generate],
            stateful_step_count=2,
            suppress_health_check=list(HealthCheck),
        )
    )

    assert Workflow.merged_case_seen
    assert not Workflow.stale_provenance_seen
