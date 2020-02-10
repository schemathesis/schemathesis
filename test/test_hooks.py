import pytest
from hypothesis import given, settings

import schemathesis


def hook(strategy):
    return strategy.filter(lambda x: x["id"].isdigit())


@pytest.fixture
def query_hook():
    schemathesis.hooks.register("query", hook)
    yield
    schemathesis.hooks.unregister_all()


@pytest.fixture
def schema(flask_app):
    return schemathesis.from_wsgi("/swagger.yaml", flask_app)


@pytest.mark.endpoints("custom_format")
@pytest.mark.usefixtures("query_hook")
def test_global_query_hook(schema, schema_url):
    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.endpoints("custom_format")
def test_schema_query_hook(schema, schema_url):
    schema.register_hook("query", hook)
    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()

    test()


@pytest.mark.usefixtures("query_hook")
@pytest.mark.endpoints("custom_format")
def test_hooks_combination(schema, schema_url):
    def extra(st):
        return st.filter(lambda x: int(x["id"]) % 2 == 0)

    schema.register_hook("query", extra)
    strategy = schema.endpoints["/api/custom_format"]["GET"].as_strategy()

    @given(case=strategy)
    @settings(max_examples=3)
    def test(case):
        assert case.query["id"].isdigit()
        assert int(case.query["id"]) % 2 == 0

    test()


def test_hooks_via_parametrize(testdir):
    testdir.make_test(
        """
def extra(st):
    return st.filter(lambda x: x["id"].isdigit() and int(x["id"]) % 2 == 0)

schema.register_hook("query", extra)

@schema.parametrize()
@settings(max_examples=1)
def test(case):
    assert case.endpoint.schema.get_hook("query") is extra
    assert int(case.query["id"]) % 2 == 0
    """,
        schema={
            "openapi": "3.0.2",
            "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
            "paths": {
                "/query": {
                    "get": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string", "minLength": 1},
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
    )
    result = testdir.runpytest()
    result.assert_outcomes(passed=1)
