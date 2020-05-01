import pytest
from hypothesis import given, settings

import schemathesis


def hook(strategy, context):
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
    def extra(st, context):
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


SIMPLE_SCHEMA = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
    "paths": {
        "/query": {
            "get": {
                "parameters": [
                    {"name": "id", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1}},
                    {"name": "value", "in": "header", "required": True, "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


def test_per_test_hooks(testdir):
    testdir.make_test(
        """
from hypothesis import strategies as st

def replacement(strategy, context):
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

def another_replacement(strategy, context):
    return st.just({"id": "foobaz"})

def third_replacement(strategy, context):
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
        schema=SIMPLE_SCHEMA,
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=4)


def test_invalid_hook(schema):
    def foo(strategy, context):
        pass

    with pytest.raises(KeyError, match="wrong"):

        @schema.with_hook("wrong", foo)
        def test(case):
            pass


def test_hooks_via_parametrize(testdir):
    testdir.make_test(
        """
def extra(st, context):
    return st.filter(lambda x: x["id"].isdigit() and int(x["id"]) % 2 == 0)

schema.register_hook("query", extra)

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert case.endpoint.schema.get_hook("query") is extra
    assert int(case.query["id"]) % 2 == 0
    """,
        schema=SIMPLE_SCHEMA,
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
        str(recwarn.list[0].message) == "Hook functions that do not accept `context` argument are deprecated and "
        "support will be removed in Schemathesis 2.0."
    )

    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()
