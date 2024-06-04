from unittest.mock import ANY

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

import schemathesis
from schemathesis.constants import USER_AGENT
from schemathesis.hooks import HookContext, HookDispatcher, HookScope
from schemathesis.utils import PARAMETRIZE_MARKER
from test.utils import assert_requests_call, flaky


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


@pytest.fixture()
def dispatcher():
    return HookDispatcher(scope=HookScope.SCHEMA)


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
@pytest.mark.usefixtures("global_hook")
def test_global_query_hook(wsgi_app_schema, schema_url):
    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("payload")
def test_global_body_hook(wsgi_app_schema):
    @schemathesis.hook
    def filter_body(context, body):
        return len(body["name"]) == 5

    strategy = wsgi_app_schema["/payload"]["POST"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert len(case.body["name"]) == 5

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("create_user")
def test_case_hook(wsgi_app_schema):
    dispatcher = HookDispatcher(scope=HookScope.TEST)

    @dispatcher.register
    def map_case(context, case):
        case.body["extra"] = 42
        return case

    @schemathesis.hook
    def map_case(context, case):  # noqa: F811
        case.body["first_name"] = case.body["last_name"]
        return case

    strategy = wsgi_app_schema["/users/"]["POST"].as_strategy(hooks=dispatcher)

    @given(case=strategy)
    @settings(max_examples=10, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.body["first_name"] == case.body["last_name"]
        assert case.body["extra"] == 42

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_schema_query_hook(wsgi_app_schema, schema_url):
    @wsgi_app_schema.hook
    def filter_query(context, query):
        return query["id"].isdigit() and query["id"].isascii()

    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.usefixtures("global_hook")
@pytest.mark.operations("custom_format")
def test_hooks_combination(wsgi_app_schema):
    @wsgi_app_schema.hook("filter_query")
    def extra(context, query):
        assert context.operation == wsgi_app_schema["/custom_format"]["GET"]
        return int(query["id"]) % 2 == 0

    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


def test_per_test_hooks(testdir, simple_openapi):
    testdir.make_test(
        """
from hypothesis import strategies as st

def replacement(context, query):
    return {"id": "foobar"}

@schema.hooks.apply(replacement, name="map_query")
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    assert case.query["id"] == "foobar"

@schema.parametrize()
@schema.hooks.apply(replacement, name="map_query")
@settings(max_examples=1)
def test_b(case):
    assert case.query["id"] == "foobar"

def another_replacement(context, query):
    return {"id": "foobaz"}

def map_headers(context, headers):
    return {"value": "spam"}

@schema.parametrize()
@schema.hooks.apply(another_replacement, name="map_query")  # Higher priority
@schema.hooks.apply(replacement, name="map_query")
@schema.hooks.apply(map_headers)
@settings(max_examples=1)
def test_c(case):
    assert case.query["id"] == "foobaz"
    assert case.headers["value"] == "spam"

@schema.parametrize()
@settings(max_examples=1)
def test_d(case):
    assert case.query["id"] != "foobar"
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=4)


def test_hooks_via_parametrize(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook("filter_query")
def extra(context, query):
    return query["id"].isdigit() and query["id"].isascii() and int(query["id"]) % 2 == 0

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert case.operation.schema.hooks.get_all_by_name("filter_query")[0] is extra
    assert int(case.query["id"]) % 2 == 0
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_register_invalid_hook_name(dispatcher):
    with pytest.raises(TypeError, match="There is no hook with name 'hook'"):

        @dispatcher.register
        def hook():
            pass


def test_register_invalid_hook_spec(dispatcher):
    with pytest.raises(TypeError, match="Hook 'filter_query' takes 2 arguments but 3 is defined"):

        @dispatcher.register
        def filter_query(a, b, c):
            pass


def test_save_test_function(wsgi_app_schema):
    assert wsgi_app_schema.test_function is None

    @wsgi_app_schema.parametrize()
    def test(case):
        pass

    assert getattr(test, PARAMETRIZE_MARKER).test_function is test


@pytest.mark.parametrize("apply_first", (True, False))
def test_local_dispatcher(wsgi_app_schema, apply_first):
    assert wsgi_app_schema.hooks.scope == HookScope.SCHEMA

    # When there are schema-level hooks
    @wsgi_app_schema.hook("map_query")
    def schema_hook(context, query):
        return query

    # And per-test hooks are applied
    def local_hook(context, cookies):
        return cookies

    # And order of decorators is any
    apply = wsgi_app_schema.hooks.apply(local_hook, name="map_cookies")
    parametrize = wsgi_app_schema.parametrize()
    if apply_first:

        def wrap(x):
            return parametrize(apply(x))

    else:

        def wrap(x):
            return apply(parametrize(x))

    @wrap
    def test(case):
        pass

    # Then a hook dispatcher instance is attached to the test function
    assert isinstance(test._schemathesis_hooks, HookDispatcher)
    assert test._schemathesis_hooks.scope == HookScope.TEST
    # And this dispatcher contains only local hooks
    assert test._schemathesis_hooks.get_all_by_name("map_cookies") == [local_hook]
    assert test._schemathesis_hooks.get_all_by_name("map_query") == []
    # And the schema-level dispatcher still contains only schema-level hooks
    assert getattr(test, PARAMETRIZE_MARKER).hooks.get_all_by_name("map_query") == [schema_hook]
    assert getattr(test, PARAMETRIZE_MARKER).hooks.get_all_by_name("map_cookies") == []


@flaky(max_runs=3, min_passes=1)
@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_multiple_hooks_per_spec(wsgi_app_schema):
    @wsgi_app_schema.hook("filter_query")
    def first_hook(context, query):
        return query["id"].isdigit() and query["id"].isascii()

    @wsgi_app_schema.hook("filter_query")
    def second_hook(context, query):
        return int(query["id"]) % 2 == 0

    assert wsgi_app_schema.hooks.get_all_by_name("filter_query") == [first_hook, second_hook]

    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_flatmap(wsgi_app_schema):
    @wsgi_app_schema.hook
    def filter_query(context, query):
        return query["id"].isdigit() and query["id"].isascii()

    @wsgi_app_schema.hook
    def flatmap_query(context, query):
        value = query["id"]
        return st.fixed_dictionaries({"id": st.just(value), "square": st.just(int(value) ** 2)})

    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        value = case.query["id"]
        assert value.isdigit()
        assert case.query["square"] == int(value) ** 2

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_case_hooks(wsgi_app_schema):
    @wsgi_app_schema.hook
    def filter_case(context, case):
        return case.query["id"].isdigit() and case.query["id"].isascii()

    @wsgi_app_schema.hook
    def map_case(context, case):
        case.query["id"] += "42"
        case.query["square"] = int(case.query["id"]) ** 2
        return case

    @wsgi_app_schema.hook
    def flatmap_case(context, case):
        return st.just(case)

    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        value = case.query["id"]
        assert value.isdigit()
        assert case.query["square"] == int(value) ** 2

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.operations("custom_format")
def test_before_process_path_hook(wsgi_app_schema):
    @wsgi_app_schema.hook
    def before_process_path(context, path, methods):
        methods["get"]["parameters"][0]["name"] = "foo"
        methods["get"]["parameters"][0]["enum"] = ["bar"]

    strategy = wsgi_app_schema["/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        assert case.query == {"foo": "bar"}

    test()


def test_register_wrong_scope(wsgi_app_schema):
    with pytest.raises(
        ValueError,
        match=r"Cannot register hook 'before_load_schema' on SCHEMA scope dispatcher. "
        r"Use a dispatcher with GLOBAL scope\(s\) instead",
    ):

        @wsgi_app_schema.hook
        def before_load_schema(ctx, raw_schema):
            pass


def test_before_add_examples(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_add_examples(context, examples):
    new = schemathesis.models.Case(
        operation=context.operation,
        generation_time=0.0,
        query={"foo": "bar"}
    )
    examples.append(new)

@schema.parametrize()
@settings(phases=[Phase.explicit])
def test_a(case):
    assert case.query == {"foo": "bar"}


def another_hook(context, examples):
    new = schemathesis.models.Case(
        operation=context.operation,
        generation_time=0.0,
        query={"spam": "baz"}
    )
    examples.append(new)

IDX = 0

@schema.parametrize()
@schema.hooks.apply(another_hook, name="before_add_examples")
@settings(phases=[Phase.explicit])
def test_b(case):
    global IDX
    if IDX == 0:
        assert case.query == {"spam": "baz"}
    if IDX == 1:
        assert case.query == {"foo": "bar"}
    IDX += 1
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=2)


def test_deprecated_attribute():
    context = HookContext(1)
    with pytest.warns(Warning) as records:
        assert context.endpoint == context.operation == 1
    assert str(records[0].message) == (
        "Property `endpoint` is deprecated and will be removed in Schemathesis 4.0. Use `operation` instead."
    )


def test_before_init_operation(testdir, simple_openapi):
    testdir.make_test(
        """
@schema.hook
def before_init_operation(context, operation):
    operation.query[0].definition["schema"] = {"enum": [42]}

@schema.parametrize()
def test_a(case):
    assert case.query == {"id": 42}
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_after_load_schema(testdir, simple_openapi):
    testdir.make_test(
        """
LINK_STATUS = "200"
# Totally not working link, but it is for testing only
KEY = "userId"
EXPRESSION = "$response.body#/id"
PARAMETERS = {KEY: EXPRESSION}

@schemathesis.hook
def after_load_schema(
    context: schemathesis.hooks.HookContext,
    schema: schemathesis.schemas.BaseSchema,
) -> None:
    schema.add_link(
        source=schema["/query"]["get"],
        target=schema["/query"]["get"],
        status_code=LINK_STATUS,
        parameters=PARAMETERS,
    )

schema = schemathesis.from_dict(raw_schema)

@schema.parametrize()
def test_a(case):
    link = schema.get_links(case.operation)[LINK_STATUS][case.operation.verbose_name]
    assert link.operation == case.operation
    assert link.parameters == [(None, KEY, EXPRESSION)]
    """,
        schema=simple_openapi,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)


def test_graphql_body(graphql_schema):
    @graphql_schema.hook
    def map_body(context, body):
        node = body.definitions[0].selection_set.selections[0]
        node.name.value = "addedViaHook"
        node.arguments = ()
        node.selection_set = ()
        return body

    strategy = graphql_schema["Mutation"]["addBook"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3, phases=[Phase.generate], suppress_health_check=list(HealthCheck), deadline=None)
    def test(case):
        # Not necessarily valid GraphQL, but it is simpler to check the hook this way
        assert case.body == "mutation {\n  addedViaHook\n}"

    test()


def test_graphql_query(graphql_schema, graphql_server_host):
    query = {"q": 1}
    path_parameters = {"p": 2}
    headers = {"h": "3"}
    cookies = {"c": "4"}

    @graphql_schema.hook
    def map_query(_, __):
        nonlocal query

        return query

    @graphql_schema.hook
    def map_path_parameters(_, __):
        nonlocal path_parameters

        return path_parameters

    @graphql_schema.hook
    def map_headers(_, __):
        nonlocal headers

        return headers

    @graphql_schema.hook
    def map_cookies(_, __):
        nonlocal cookies

        return cookies

    strategy = graphql_schema["Query"]["getBooks"].as_strategy()

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
                "User-Agent": USER_AGENT,
                "X-Schemathesis-TestCaseId": ANY,
                "Content-Type": "application/json",
                "h": "3",
            },
            "json": {"query": ANY},
            "method": "POST",
            "params": {"q": 1},
            "url": f"http://{graphql_server_host}/graphql",
        }
        assert_requests_call(case)

    test()
