"""These tests ensure backward compatibility with the old hooks system."""
import pytest
from hypothesis import given, settings

import schemathesis


def hook(context, strategy):
    return strategy.filter(lambda x: x["id"].isdigit())


@pytest.fixture
def query_hook():
    schemathesis.hooks.register("query", hook)
    yield
    schemathesis.hooks.unregister_all()


@pytest.fixture
def schema(flask_app):
    return schemathesis.from_wsgi("/swagger.yaml", flask_app)


@pytest.mark.hypothesis_nested
@pytest.mark.endpoints("custom_format")
@pytest.mark.usefixtures("query_hook")
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
    schema.register_hook("query", hook)
    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.hypothesis_nested
@pytest.mark.usefixtures("query_hook")
@pytest.mark.endpoints("custom_format")
def test_hooks_combination(schema, schema_url):
    def extra(context, st):
        assert context.endpoint == schema.endpoints["/api/custom_format"]["GET"]
        return st.filter(lambda x: int(x["id"]) % 2 == 0)

    schema.register_hook("query", extra)
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

@schema.with_hook("query", replacement)
@schema.parametrize()
@settings(max_examples=1)
def test_a(case):
    assert case.query["id"] == "foobar"

@schema.parametrize()
@schema.with_hook("query", replacement)
@settings(max_examples=1)
def test_b(case):
    assert case.query["id"] == "foobar"

def another_replacement(context, strategy):
    return st.just({"id": "foobaz"})

def third_replacement(context, strategy):
    return st.just({"value": "spam"})

@schema.parametrize()
@schema.with_hook("query", another_replacement)  # Higher priority
@schema.with_hook("query", replacement)
@schema.with_hook("headers", third_replacement)
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


def test_invalid_global_hook():
    with pytest.raises(KeyError, match="wrong"):
        schemathesis.hooks.register("wrong", lambda x: x)


def test_invalid_schema_hook(schema):
    with pytest.raises(KeyError, match="wrong"):
        schema.register_hook("wrong", lambda x: x)


def test_invalid_local_hook(schema):
    def foo(context, strategy):
        pass

    with pytest.raises(KeyError, match="wrong"):

        @schema.with_hook("wrong", foo)
        def test(case):
            pass


def test_hooks_via_parametrize(testdir, simple_openapi):
    testdir.make_test(
        """
def extra(context, st):
    return st.filter(lambda x: x["id"].isdigit() and int(x["id"]) % 2 == 0)

schema.register_hook("query", extra)

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


@pytest.mark.hypothesis_nested
@pytest.mark.endpoints("custom_format")
def test_deprecated_hook(recwarn, schema):
    def deprecated_hook(strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    schema.register_hook("query", deprecated_hook)
    assert (
        str(recwarn.list[1].message) == "Hook functions that do not accept `context` argument are deprecated and "
        "support will be removed in Schemathesis 2.0."
    )

    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


def test_register_wrong_number_of_argument():
    with pytest.raises(TypeError, match="Invalid number of arguments. Please, use `register` as a decorator."):
        schemathesis.hooks.register("a", "b", "c")
