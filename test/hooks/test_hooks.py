import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.hooks import HookDispatcher


@pytest.fixture(params=["direct", "named"])
def global_hook(request):
    if request.param == "direct":

        @schemathesis.hooks.register
        def before_generate_query(context, strategy):
            return strategy.filter(lambda x: x["id"].isdigit())

    if request.param == "named":

        @schemathesis.hooks.register("before_generate_query")
        def hook(context, strategy):
            return strategy.filter(lambda x: x["id"].isdigit())

    yield
    schemathesis.hooks.unregister_all()


@pytest.fixture
def schema(flask_app):
    return schemathesis.from_wsgi("/swagger.yaml", flask_app)


@pytest.fixture()
def dispatcher():
    return HookDispatcher()


@pytest.mark.hypothesis_nested
@pytest.mark.endpoints("custom_format")
@pytest.mark.usefixtures("global_hook")
def test_global_query_hook(schema, schema_url):
    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.endpoints("custom_format")
def test_schema_query_hook(schema, schema_url):
    @schema.hooks.register
    def before_generate_query(context, strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.usefixtures("global_hook")
@pytest.mark.endpoints("custom_format")
def test_hooks_combination(schema, schema_url):
    @schema.hooks.register("before_generate_query")
    def extra(context, st):
        assert context.endpoint == schema.endpoints["/api/custom_format"]["GET"]
        return st.filter(lambda x: int(x["id"]) % 2 == 0)

    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


def test_per_test_hooks(testdir, simple_openapi):
    testdir.make_test(
        """
from hypothesis import strategies as st

def replacement(context, strategy):
    return st.just({"id": "foobar"})

@schema.hooks.apply("before_generate_query", replacement)
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    assert case.query["id"] == "foobar"

@schema.parametrize()
@schema.hooks.apply("before_generate_query", replacement)
@settings(max_examples=1)
def test_b(case):
    assert case.query["id"] == "foobar"

def another_replacement(context, strategy):
    return st.just({"id": "foobaz"})

def third_replacement(context, strategy):
    return st.just({"value": "spam"})

@schema.parametrize()
@schema.hooks.apply("before_generate_query", another_replacement)  # Higher priority
@schema.hooks.apply("before_generate_query", replacement)
@schema.hooks.apply("before_generate_headers", third_replacement)
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
@schema.hooks.register("before_generate_query")
def extra(context, st):
    return st.filter(lambda x: x["id"].isdigit() and int(x["id"]) % 2 == 0)

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert case.endpoint.schema.hooks.get_hooks("before_generate_query")[0] is extra
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
    with pytest.raises(TypeError, match="Hook 'before_generate_query' takes 2 arguments but 3 is defined"):

        @dispatcher.register
        def before_generate_query(a, b, c):
            pass


def test_save_test_function(schema):
    assert schema.test_function is None

    @schema.parametrize()
    def test(case):
        pass

    assert test._schemathesis_test.test_function is test


@pytest.mark.parametrize("apply_first", (True, False))
def test_local_dispatcher(schema, apply_first):
    # When there are schema-level hooks
    @schema.hooks.register("before_generate_query")
    def schema_hook(context, strategy):
        return strategy

    # And per-test hooks are applied
    def local_hook(context, strategy):
        return strategy

    # And order of decorators is any
    apply = schema.hooks.apply("before_generate_cookies", local_hook)
    parametrize = schema.parametrize()
    if apply_first:
        wrap = lambda x: parametrize(apply(x))
    else:
        wrap = lambda x: apply(parametrize(x))

    @wrap
    def test(case):
        pass

    # Then a hook dispatcher instance is attached to the test function
    assert isinstance(test._schemathesis_hooks, HookDispatcher)
    # And this dispatcher contains only local hooks
    assert test._schemathesis_hooks.get_hooks("before_generate_cookies") == [local_hook]
    assert test._schemathesis_hooks.get_hooks("before_generate_query") == []
    # And the schema-level dispatcher still contains only schema-level hooks
    assert test._schemathesis_test.hooks.get_hooks("before_generate_query") == [schema_hook]
    assert test._schemathesis_test.hooks.get_hooks("before_generate_cookies") == []


@pytest.mark.hypothesis_nested
@pytest.mark.endpoints("custom_format")
def test_multiple_hooks_per_spec(schema):
    @schema.hooks.register("before_generate_query")
    def first_hook(context, strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    @schema.hooks.register("before_generate_query")
    def second_hook(context, strategy):
        return strategy.filter(lambda x: int(x["id"]) % 2 == 0)

    assert schema.hooks.get_hooks("before_generate_query") == [first_hook, second_hook]

    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.endpoints("custom_format")
def test_before_process_path_hook(schema):
    @schema.hooks.register
    def before_process_path(context, path, methods):
        methods["get"]["parameters"][0]["name"] = "foo"
        methods["get"]["parameters"][0]["const"] = "bar"

    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query == {"foo": "bar"}

    test()
