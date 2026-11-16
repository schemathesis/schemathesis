import sys
from contextlib import ExitStack
from unittest.mock import ANY

import graphql
import pytest
from fastapi import FastAPI
from flask import jsonify, request
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st
from hypothesis.errors import NoSuchExample

import schemathesis
from schemathesis.core.failures import FailureGroup
from schemathesis.core.transport import USER_AGENT
from schemathesis.engine import Status
from schemathesis.generation.hypothesis import examples
from schemathesis.hooks import (
    HookContext,
    HookDispatcher,
    HookDispatcherMark,
    HookScope,
    dispatch_before_add_examples,
    dispatch_before_call,
)
from schemathesis.pytest.plugin import SchemaHandleMark
from schemathesis.specs.graphql.scalars import UnknownScalar
from schemathesis.transport.prepare import get_default_headers
from test.apps.catalog.graphql import bookstore
from test.apps.catalog.openapi import basic
from test.python._constants.fixtures import buggy_app, graphql_string_pool
from test.utils import assert_requests_call, find_first, run_given


def integer_id(query):
    value = query["id"]
    return value.isdigit() and value.isascii()


@pytest.fixture(params=["default-direct", "default-named", "generate-direct", "generate-named"])
def global_hook(request):
    if request.param == "default-direct":

        @schemathesis.hook
        def filter_query(context, query):
            return integer_id(query)

    if request.param == "default-named":

        @schemathesis.hook("filter_query")
        def hook(context, query):
            return integer_id(query)

    if request.param == "generate-direct":

        @schemathesis.hook
        def before_generate_query(context, strategy):
            return strategy.filter(integer_id)

    if request.param == "generate-named":

        @schemathesis.hook("before_generate_query")
        def hook(context, strategy):
            return strategy.filter(integer_id)


@pytest.fixture
def dispatcher():
    return HookDispatcher(scope=HookScope.SCHEMA)


def test_hook_dispatch_accepts_multiple_dispatchers(dispatcher):
    calls = []
    context = HookContext()
    examples = []
    another_dispatcher = HookDispatcher(scope=HookScope.SCHEMA)

    @dispatcher.hook
    def before_add_examples(context, examples):
        calls.append(("first", context, examples))

    @another_dispatcher.hook
    def before_add_examples(context, examples):  # noqa: F811
        calls.append(("second", context, examples))

    dispatch_before_add_examples(dispatcher, another_dispatcher, context=context, examples=examples)

    assert calls == [("first", context, examples), ("second", context, examples)]


def test_hook_dispatch_skips_filtered_hooks(ctx, dispatcher):
    operation = ctx.openapi.load_operation("GET /users", {"responses": {"200": {"description": "OK"}}})
    calls = []

    @dispatcher.hook.apply_to(method="POST")
    def before_add_examples(context, examples):
        calls.append("ran")

    dispatch_before_add_examples(dispatcher, context=HookContext(operation=operation), examples=[])

    assert calls == []


def test_before_call_dispatch_preserves_kwargs_styles():
    dispatcher = HookDispatcher(scope=HookScope.GLOBAL)
    context = HookContext()
    case = object()
    calls = []

    @dispatcher.hook
    def before_call(context, case, kwargs):
        calls.append(("legacy", context, case, kwargs))

    @dispatcher.hook("before_call")
    def hook(context, case, **kwargs):
        calls.append(("kwargs", context, case, kwargs))

    dispatch_before_call(dispatcher, context=context, case=case, kwargs={"timeout": 1})

    assert calls == [
        ("legacy", context, case, {"timeout": 1}),
        ("kwargs", context, case, {"timeout": 1}),
    ]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(pytest.skip, pytest.skip.Exception), (pytest.fail, pytest.fail.Exception)],
    ids=["skip", "fail"],
)
def test_before_call_propagates_pytest_outcomes(outcome, expected):
    dispatcher = HookDispatcher(scope=HookScope.GLOBAL)

    @dispatcher.hook
    def before_call(context, case, kwargs):
        outcome("from hook")

    with pytest.raises(expected, match="from hook"):
        dispatch_before_call(dispatcher, context=HookContext(), case=object(), kwargs={})


@pytest.mark.hypothesis_nested
@pytest.mark.usefixtures("global_hook")
def test_global_query_hook(ctx):
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()


@pytest.mark.hypothesis_nested
def test_case_hook(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    dispatcher = HookDispatcher(scope=HookScope.TEST)

    @dispatcher.hook
    def map_case(context, case):
        case.body["extra"] = 42
        return case

    @schemathesis.hook
    def map_case(context, case):  # noqa: F811
        case.body["first_name"] = case.body["last_name"]
        return case

    strategy = schema["/users/"]["POST"].as_strategy(hooks=dispatcher)

    @given(case=strategy)
    @settings(max_examples=10, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.body["first_name"] == case.body["last_name"]
        assert case.body["extra"] == 42

    test()


@pytest.mark.hypothesis_nested
def test_schema_query_hook(ctx):
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)

    @schema.hook
    def filter_query(context, query):
        return query["id"].isdigit() and query["id"].isascii()

    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()


@pytest.mark.hypothesis_nested
@pytest.mark.usefixtures("global_hook")
def test_hooks_combination(ctx):
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)

    @schema.hook("filter_query")
    def extra(context, query):
        assert context.operation == schema["/api/custom_format"]["GET"]
        return int(query["id"]) % 2 == 0

    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    # Both filters together admit ~0.15% of the unconstrained strings; a random draw sequence sometimes finds none.
    @run_given(case=strategy, max_examples=3, derandomize=True)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0


def test_register_invalid_hook_name(dispatcher):
    with pytest.raises(TypeError, match="There is no hook with name 'hook'"):

        @dispatcher.hook
        def hook():
            pass


def test_register_invalid_hook_spec(dispatcher):
    with pytest.raises(TypeError, match="Hook 'filter_query' takes 2 arguments but 3 is defined"):

        @dispatcher.hook
        def filter_query(a, b, c):
            pass


def test_save_test_function(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    assert schema.test_function is None

    @schema.parametrize()
    def test(case):
        pass

    assert SchemaHandleMark.get(test).test_function is test


@pytest.mark.parametrize("apply_first", [True, False])
def test_local_dispatcher(ctx, apply_first):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    assert schema.hooks.scope == HookScope.SCHEMA

    @schema.hook("map_query")
    def schema_hook(context, query):
        return query

    def local_hook(context, cookies):
        return cookies

    # Decorator order must not matter.
    apply = schema.hooks.apply(local_hook, name="map_cookies")
    parametrize = schema.parametrize()
    if apply_first:

        def wrap(x):
            return parametrize(apply(x))

    else:

        def wrap(x):
            return apply(parametrize(x))

    @wrap
    def test(case):
        pass

    hook_dispatcher = HookDispatcherMark.get(test)
    assert isinstance(hook_dispatcher, HookDispatcher)
    assert hook_dispatcher.scope == HookScope.TEST
    assert hook_dispatcher.get_all_by_name("map_cookies") == [local_hook]
    assert hook_dispatcher.get_all_by_name("map_query") == []
    handle = SchemaHandleMark.get(test)
    assert handle is not None
    assert handle.hooks.get_all_by_name("map_query") == [schema_hook]
    assert handle.hooks.get_all_by_name("map_cookies") == []


# Non-numeric values crash the second hook, so the test passes only when hooks run in registration order.
@pytest.mark.hypothesis_nested
def test_multiple_hooks_per_spec(ctx):
    schemathesis.openapi.format("digits", st.integers().map(str) | st.just("x"))
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)

    @schema.hook("filter_query")
    def first_hook(context, query):
        return query["id"].isdigit() and query["id"].isascii()

    @schema.hook("filter_query")
    def second_hook(context, query):
        return int(query["id"]) % 2 == 0

    assert schema.hooks.get_all_by_name("filter_query") == [first_hook, second_hook]

    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    @run_given(case=strategy, max_examples=10)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0


@pytest.mark.hypothesis_nested
def test_flatmap(ctx):
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)

    @schema.hook
    def filter_query(context, query):
        return query["id"].isdigit() and query["id"].isascii()

    @schema.hook
    def flatmap_query(context, query):
        value = query["id"]
        return st.fixed_dictionaries({"id": st.just(value), "square": st.just(int(value) ** 2)})

    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        value = case.query["id"]
        assert value.isdigit()
        assert case.query["square"] == int(value) ** 2


@pytest.mark.hypothesis_nested
def test_case_hooks(ctx):
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)

    @schema.hook
    def filter_case(context, case):
        return case.query["id"].isdigit() and case.query["id"].isascii()

    @schema.hook
    def map_case(context, case):
        case.query["id"] += "42"
        case.query["square"] = int(case.query["id"]) ** 2
        return case

    @schema.hook
    def flatmap_case(context, case):
        return st.just(case)

    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        value = case.query["id"]
        assert value.isdigit()
        assert case.query["square"] == int(value) ** 2


@pytest.mark.hypothesis_nested
def test_before_process_path_hook(ctx):
    api = ctx.openapi.apps.custom_format()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)

    @schema.hook
    def before_process_path(context, path, methods):
        methods["get"]["parameters"][0]["name"] = "foo"
        methods["get"]["parameters"][0]["schema"] = {"type": "string", "enum": ["bar"]}

    strategy = schema["/api/custom_format"]["GET"].as_strategy()

    @run_given(case=strategy, max_examples=3)
    def test(case):
        assert case.query == {"foo": "bar"}


def test_register_wrong_scope(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    with pytest.raises(
        ValueError,
        match=r"Cannot register hook 'before_load_schema' on SCHEMA scope dispatcher. "
        r"Use a dispatcher with GLOBAL scope\(s\) instead",
    ):

        @schema.hook
        def before_load_schema(ctx, raw_schema):
            pass


@pytest.mark.hypothesis_nested
def test_after_validate_hook(ctx):
    api = ctx.openapi.apps.success_and_failure()
    api_schema = schemathesis.openapi.from_url(api.schema_url)
    results = []

    @schemathesis.hook
    def after_validate(context, case, response, check_results):
        results.extend(check_results)

    @given(case=api_schema["/api/success"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None, suppress_health_check=list(HealthCheck))
    def test_success(case):
        case.call_and_validate(checks=[schemathesis.checks.not_a_server_error])

    test_success()

    @given(case=api_schema["/api/failure"]["GET"].as_strategy())
    @settings(max_examples=1, deadline=None, suppress_health_check=list(HealthCheck))
    def test_failure(case):
        with pytest.raises(FailureGroup):
            case.call_and_validate(checks=[schemathesis.checks.not_a_server_error])

    test_failure()

    assert [(r.name, r.status) for r in results] == [
        ("not_a_server_error", Status.SUCCESS),
        ("not_a_server_error", Status.FAILURE),
    ]


def test_graphql_body(ctx):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)

    @schema.hook
    def map_body(context, body):
        return graphql.parse("mutation { addedViaHook }")

    strategy = schema["Mutation"]["addBook"].as_strategy()

    @run_given(case=strategy, max_examples=3, phases=[Phase.generate])
    def test(case):
        # The body need not be valid GraphQL; this shape makes the hook easy to check.
        assert case.body == "mutation {\n  addedViaHook\n}"


def test_flatmap_query_hook_applies_to_openapi_and_graphql(ctx):
    openapi_schema = schemathesis.openapi.from_url(ctx.openapi.apps.success().schema_url)
    graphql_schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    seen = []

    for schema in (openapi_schema, graphql_schema):

        @schema.hook
        def flatmap_query(context, query):
            return st.just({"q": "1"})

    for operation in (openapi_schema["/api/success"]["GET"], graphql_schema["Query"]["getBooks"]):

        @given(case=operation.as_strategy())
        @settings(max_examples=1, phases=[Phase.generate], suppress_health_check=list(HealthCheck), deadline=None)
        def test(case):
            seen.append(case.query)

        test()

    assert seen == [{"q": "1"}, {"q": "1"}]


def test_graphql_query(ctx):
    api = ctx.graphql.apps.books()
    schema = schemathesis.graphql.from_url(api.schema_url)
    query = {"q": 1}
    path_parameters = {"p": 2}
    headers = {"h": "3"}
    cookies = {"c": "4"}

    @schema.hook
    def map_query(_, __):
        nonlocal query

        return query

    @schema.hook
    def map_path_parameters(_, __):
        nonlocal path_parameters

        return path_parameters

    @schema.hook
    def map_headers(_, __):
        nonlocal headers

        return headers

    @schema.hook
    def map_cookies(_, __):
        nonlocal cookies

        return cookies

    strategy = schema["Query"]["getBooks"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, phases=[Phase.generate], suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.query == query
        assert case.path_parameters == path_parameters
        assert case.headers == headers
        assert case.cookies == cookies
        assert case.as_transport_kwargs() == {
            "cookies": {"c": "4"},
            "headers": {
                **get_default_headers(),
                "User-Agent": USER_AGENT,
                "X-Schemathesis-TestCaseId": ANY,
                "Content-Type": "application/json",
                "h": "3",
            },
            "json": {"query": ANY},
            "method": "POST",
            "params": {"q": 1},
            "url": f"http://127.0.0.1:{api.port}/graphql",
        }
        assert_requests_call(case)


@pytest.mark.hypothesis_nested
def test_after_call_fires_for_schema_level_hook(ctx):
    api = ctx.openapi.apps.success()
    api_schema = schemathesis.openapi.from_url(api.schema_url)
    calls = []

    @api_schema.hooks.hook
    def after_call(context, case, response):
        calls.append(case.id)

    @run_given(case=api_schema["/api/success"]["GET"].as_strategy(), max_examples=1)
    def test(case):
        case.call()

    assert len(calls) == 1


@pytest.mark.hypothesis_nested
def test_after_validate_fires_for_schema_level_hook(ctx):
    api = ctx.openapi.apps.success_and_failure()
    api_schema = schemathesis.openapi.from_url(api.schema_url)
    results = []

    @api_schema.hooks.hook
    def after_validate(context, case, response, check_results):
        results.extend(check_results)

    @run_given(case=api_schema["/api/success"]["GET"].as_strategy(), max_examples=1)
    def test_success(case):
        case.call_and_validate(checks=[schemathesis.checks.not_a_server_error])

    @run_given(case=api_schema["/api/failure"]["GET"].as_strategy(), max_examples=1)
    def test_failure(case):
        with pytest.raises(FailureGroup):
            case.call_and_validate(checks=[schemathesis.checks.not_a_server_error])

    assert [(r.name, r.status) for r in results] == [
        ("not_a_server_error", Status.SUCCESS),
        ("not_a_server_error", Status.FAILURE),
    ]


def test_after_validate_reports_success_when_filter_failure_drops_every_failure(ctx, response_factory):
    schema = ctx.openapi.load_schema({"/foo": {"get": {"responses": {"200": {"description": "OK"}}}}})
    results = []

    @schema.hooks.hook
    def filter_failure(context, failure, case, response):
        return False

    @schema.hooks.hook
    def after_validate(context, case, response, check_results):
        results.extend((r.name, r.status) for r in check_results)

    case = schema["/foo"]["GET"].Case()
    response = response_factory.requests(status_code=500)
    assert case.validate_response(response, checks=[schemathesis.checks.not_a_server_error]) is None
    assert results == [("not_a_server_error", Status.SUCCESS)]


def register_recording_component_hooks(dispatcher, container, key, calls):
    def before_generate(context, strategy):
        calls.append(f"before_generate_{container}")
        return strategy

    def filter_hook(context, value):
        calls.append(f"filter_{container}")
        return True

    def map_hook(context, value):
        calls.append(f"map_{container}")
        return {**(value or {}), key: 42}

    def flatmap_hook(context, value):
        calls.append(f"flatmap_{container}")
        return st.just(value)

    dispatcher.hook(f"before_generate_{container}")(before_generate)
    dispatcher.hook(f"filter_{container}")(filter_hook)
    dispatcher.hook(f"map_{container}")(map_hook)
    dispatcher.hook(f"flatmap_{container}")(flatmap_hook)


def test_component_hooks_leave_examples_untouched(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items/{id}": {
                "post": {
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}, "example": 7},
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}, "example": "e"},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}},
                                "example": {"name": "example"},
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    calls = []
    test_hooks = HookDispatcher(scope=HookScope.TEST)
    for dispatcher in (schema.hooks, test_hooks):
        for container, key in (("path_parameters", "id"), ("query", "q"), ("body", "name")):
            register_recording_component_hooks(dispatcher, container, key, calls)

    cases = [
        examples.generate_one(strategy)
        for strategy in schema["/items/{id}"]["POST"].get_strategies_from_examples(hooks=test_hooks)
    ]

    assert [(case.path_parameters, case.query, case.body) for case in cases] == [
        ({"id": 7}, {"q": "e"}, {"name": "example"})
    ]
    # Hooks see only the generated remainder of each location; a body taken from an example is never hooked.
    assert set(calls) == {
        f"{hook}_{container}"
        for hook in ("before_generate", "filter", "map", "flatmap")
        for container in ("path_parameters", "query")
    }


def test_component_hooks_shape_generated_parts_in_examples_phase(ctx):
    schema = ctx.openapi.load_schema(
        {
            "/items": {
                "get": {
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}, "example": "e"},
                        {"name": "limit", "in": "query", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    )
    filtered = []

    @schema.hook
    def filter_query(context, query):
        filtered.append(query)
        return True

    @schema.hook
    def map_headers(context, headers):
        return {**(headers or {}), "X-Token": "secret"}

    cases = [examples.generate_one(strategy) for strategy in schema["/items"]["GET"].get_strategies_from_examples()]

    assert [(case.query["q"], case.headers) for case in cases] == [("e", {"X-Token": "secret"})]
    assert filtered
    assert all(set(query) == {"limit"} for query in filtered), filtered


def _fastapi_app():
    app = FastAPI()

    @app.get("/items")
    def items():
        return []

    return app


@pytest.mark.parametrize(
    ("loader", "path", "make_app"),
    [
        (schemathesis.openapi.from_asgi, "/openapi.json", _fastapi_app),
        (schemathesis.openapi.from_wsgi, "/openapi.json", lambda: basic.success().server),
        (schemathesis.graphql.from_asgi, "/graphql", lambda: bookstore.books(framework="fastapi").server),
        (schemathesis.graphql.from_wsgi, "/graphql", lambda: bookstore.books().server),
    ],
    ids=["openapi-asgi", "openapi-wsgi", "graphql-asgi", "graphql-wsgi"],
)
def test_after_load_schema_receives_the_app(loader, path, make_app):
    app = make_app()
    seen = []

    @schemathesis.hook
    def after_load_schema(context, schema):
        seen.append(schema.app)

    loader(path, app)
    assert seen == [app]


def _asgi_tap(app, seen):
    async def tapped(scope, receive, send):
        if scope["type"] == "http":
            seen.append(dict(scope["headers"])[b"x-schemathesis-testcaseid"].decode())
        await app(scope, receive, send)

    return tapped


def _wsgi_tap(app, seen):
    def tapped(environ, start_response):
        seen.append(environ["HTTP_X_SCHEMATHESIS_TESTCASEID"])
        return app(environ, start_response)

    return tapped


@pytest.mark.parametrize(
    ("loader", "make_app", "tap"),
    [
        (schemathesis.openapi.from_asgi, _fastapi_app, _asgi_tap),
        (schemathesis.openapi.from_wsgi, lambda: basic.success().server, _wsgi_tap),
    ],
    ids=["asgi", "wsgi"],
)
def test_app_wrapped_in_after_load_schema_sees_test_case_ids(loader, make_app, tap):
    seen = []

    @schemathesis.hook
    def after_load_schema(context, schema):
        schema.app = tap(schema.app, seen)

    schema = loader("/openapi.json", make_app())
    case = next(schema.get_all_operations()).ok().Case()
    case.call()
    assert seen == [case.id]


def register_hook():
    @schemathesis.hook
    def map_headers(context, headers):
        return {**(headers or {}), "X-Scoped": "hook"}


def register_auth():
    @schemathesis.auth()
    class ScopedAuth:
        def get(self, case, context):
            return "scoped"

        def set(self, case, data, context):
            case.headers = {**(case.headers or {}), "Authorization": data}


def register_check():
    @schemathesis.check
    def scoped_check(ctx, response, case):
        raise AssertionError("Scoped check failed")


def register_deserializer():
    @schemathesis.deserializer("application/x-scoped")
    def deserialize(ctx, response):
        return "not an object"


def register_serializer():
    @schemathesis.serializer("text/x-scoped")
    def serialize(ctx, value):
        return b"scoped"


def register_format():
    schemathesis.openapi.format("scoped", st.just("scoped"))


def register_media_type():
    schemathesis.openapi.media_type("text/x-scoped", st.just(b"scoped"))


def register_scalar():
    schemathesis.graphql.scalar("Scoped", st.just(graphql.StringValueNode(value="scoped")))


def register_constants():
    @schemathesis.python.constants
    def source():
        return graphql_string_pool


def register_extension(label):
    @schemathesis.extension("scoped")
    class Scoped:
        def __init__(self, settings):
            pass

        def map_headers(self, context, headers):
            return {**(headers or {}), "X-Scoped": label}


def generated_headers(ctx, response_factory):
    operation = ctx.openapi.load_operation("GET /items", {"responses": {"200": {"description": "OK"}}})
    return examples.generate_one(operation.as_strategy()).headers


def validation_failures(ctx, response_factory):
    content = {"application/x-scoped": {"schema": {"type": "object"}}}
    operation = ctx.openapi.load_operation(
        "GET /items", {"responses": {"200": {"description": "OK", "content": content}}}
    )
    response = response_factory.requests(content=b"{}", content_type="application/x-scoped", method="GET")
    try:
        operation.Case().validate_response(response)
    except FailureGroup as exc:
        return sorted(failure.title for failure in exc.exceptions)
    return []


def text_body_operation(ctx):
    return ctx.openapi.load_operation(
        "POST /items",
        {
            "requestBody": {"required": True, "content": {"text/x-scoped": {"schema": {"type": "string"}}}},
            "responses": {"200": {"description": "OK"}},
        },
    )


def serialized_body(ctx, response_factory):
    case = text_body_operation(ctx).Case(body="value", media_type="text/x-scoped")
    return case.as_transport_kwargs()["data"]


def generated_body(ctx, response_factory):
    return examples.generate_one(text_body_operation(ctx).as_strategy()).body


def generated_query(ctx, response_factory):
    parameter = {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "format": "scoped"}}
    operation = ctx.openapi.load_operation(
        "GET /items", {"parameters": [parameter], "responses": {"200": {"description": "OK"}}}
    )
    return examples.generate_one(operation.as_strategy()).query


def generated_graphql_query(ctx, response_factory):
    schema = schemathesis.graphql.from_file("scalar Scoped\ntype Query { item(id: Scoped!): Int }")
    try:
        return examples.generate_one(schema["Query"]["item"].as_strategy()).body
    except UnknownScalar:
        return None


def draws_constant(ctx, response_factory):
    operation = schemathesis.openapi.from_dict(buggy_app.SCHEMA)["/unlock"]["POST"]
    # Registered constants are drawn among other values, so finding one takes many examples
    try:
        find_first(
            operation.as_strategy(),
            lambda case: isinstance(case.body, dict) and case.body.get("code") == graphql_string_pool.TOKEN,
            max_examples=200,
        )
    except NoSuchExample:
        return False
    return True


@pytest.mark.parametrize(
    ("register", "observe", "outside", "inside"),
    [
        (register_hook, generated_headers, {}, {"X-Scoped": "hook"}),
        (register_auth, generated_headers, {}, {"Authorization": "scoped"}),
        (register_check, validation_failures, [], ["Custom check failed: `scoped_check`"]),
        (register_deserializer, validation_failures, [], ["Response violates schema"]),
        (register_serializer, serialized_body, b"value", b"scoped"),
        (register_format, generated_query, {"q": ""}, {"q": "scoped"}),
        (register_media_type, generated_body, "", b"scoped"),
        (register_scalar, generated_graphql_query, None, '{\n  item(id: "scoped")\n}'),
        (register_constants, draws_constant, False, True),
    ],
    ids=["hook", "auth", "check", "deserializer", "serializer", "format", "media-type", "graphql-scalar", "constants"],
)
def test_scoped_extensions_remove_registrations_made_inside(ctx, response_factory, register, observe, outside, inside):
    assert observe(ctx, response_factory) == outside
    with schemathesis.scoped_extensions():
        register()
        assert observe(ctx, response_factory) == inside
    assert observe(ctx, response_factory) == outside


BEFORE_HOOKS = """
import schemathesis


@schemathesis.hook
def map_headers(context, headers):
    return {**(headers or {}), "X-Before": "1"}
"""


def register_before_with_decorator(tmp_path, monkeypatch):
    @schemathesis.hook
    def map_headers(context, headers):
        return {**(headers or {}), "X-Before": "1"}


def register_before_with_environment(tmp_path, monkeypatch):
    path = tmp_path / "before_hooks.py"
    path.write_text(BEFORE_HOOKS)
    monkeypatch.setenv("SCHEMATHESIS_HOOKS", str(path))
    schemathesis.Config.from_dict({})


def register_before_with_config(tmp_path, monkeypatch):
    path = tmp_path / "before_hooks.py"
    path.write_text(BEFORE_HOOKS)
    schemathesis.Config.from_dict({"hooks": [str(path)]})


@pytest.mark.parametrize(
    "register_before",
    [register_before_with_decorator, register_before_with_environment, register_before_with_config],
    ids=["decorator", "environment", "config"],
)
def test_scoped_extensions_keep_registrations_made_before(
    ctx, response_factory, tmp_path, monkeypatch, register_before
):
    register_before(tmp_path, monkeypatch)
    with schemathesis.scoped_extensions():
        register_hook()
        assert generated_headers(ctx, response_factory) == {"X-Before": "1", "X-Scoped": "hook"}
    assert generated_headers(ctx, response_factory) == {"X-Before": "1"}


def test_scoped_extensions_keep_constants_registered_before(ctx, response_factory):
    register_constants()
    with schemathesis.scoped_extensions():

        @schemathesis.python.constants
        def scoped():
            return buggy_app

        assert draws_constant(ctx, response_factory)
    assert draws_constant(ctx, response_factory)


def test_scoped_extensions_keep_hooks_module_from_environment(ctx, response_factory, tmp_path, monkeypatch):
    # Python imports the module once, so it must not load for the first time inside the scope
    (tmp_path / "scoped_environment_hooks.py").write_text(BEFORE_HOOKS)
    monkeypatch.syspath_prepend(tmp_path)
    monkeypatch.setenv("SCHEMATHESIS_HOOKS", "scoped_environment_hooks")
    try:
        with schemathesis.scoped_extensions():
            assert generated_headers(ctx, response_factory) == {"X-Before": "1"}
        assert generated_headers(ctx, response_factory) == {"X-Before": "1"}
    finally:
        sys.modules.pop("scoped_environment_hooks", None)


def test_scoped_extensions_load_hooks_file_from_config_in_every_scope(ctx, response_factory, tmp_path):
    path = tmp_path / "suite_hooks.py"
    path.write_text(BEFORE_HOOKS)
    for _ in range(2):
        with schemathesis.scoped_extensions():
            schemathesis.Config.from_dict({"hooks": [str(path)]})
            assert generated_headers(ctx, response_factory) == {"X-Before": "1"}
    assert generated_headers(ctx, response_factory) == {}


def test_scoped_extensions_nest(ctx, response_factory):
    with schemathesis.scoped_extensions():
        register_hook()
        with schemathesis.scoped_extensions():
            register_auth()
            assert generated_headers(ctx, response_factory) == {"X-Scoped": "hook", "Authorization": "scoped"}
        assert generated_headers(ctx, response_factory) == {"X-Scoped": "hook"}
    assert generated_headers(ctx, response_factory) == {}


def test_scoped_extensions_restore_after_exception(ctx, response_factory):
    with pytest.raises(RuntimeError, match="Suite failed"), schemathesis.scoped_extensions():
        register_hook()
        raise RuntimeError("Suite failed")
    assert generated_headers(ctx, response_factory) == {}


def test_scoped_extensions_free_extension_names(ctx, response_factory):
    for label in ("first", "second"):
        with schemathesis.scoped_extensions():
            register_extension(label)
            assert generated_headers(ctx, response_factory) == {"X-Scoped": label}
    assert generated_headers(ctx, response_factory) == {}


def test_scoped_extensions_do_not_reach_schemas_tested_after_the_scope(ctx):
    app, _ = ctx.openapi.make_flask_app({"/user": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/user")
    def user():
        return jsonify({name: request.headers.get(name) for name in ("Authorization", "X-Scoped")})

    def start_suite(extensions):
        extensions.enter_context(schemathesis.scoped_extensions())
        register_hook()
        register_auth()

    def run_suite():
        schema = schemathesis.openapi.from_wsgi("/openapi.json", app)
        return examples.generate_one(schema["/user"]["GET"].as_strategy()).call().json()

    extensions = ExitStack()
    start_suite(extensions)
    inside = run_suite()
    extensions.close()

    assert [inside, run_suite()] == [
        {"Authorization": "scoped", "X-Scoped": "hook"},
        {"Authorization": None, "X-Scoped": None},
    ]
