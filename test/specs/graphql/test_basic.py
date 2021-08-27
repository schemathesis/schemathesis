from test.utils import assert_requests_call

import pytest
import requests
from hypothesis import given, settings

import schemathesis
from schemathesis.constants import USER_AGENT
from schemathesis.specs.graphql.loaders import INTROSPECTION_QUERY
from schemathesis.specs.graphql.schemas import GraphQLCase


def test_raw_schema(graphql_schema):
    assert graphql_schema.verbose_name == "GraphQL"


@pytest.mark.hypothesis_nested
def test_query_strategy(graphql_strategy):
    @given(case=graphql_strategy)
    @settings(max_examples=10)
    def test(case):
        response = case.call()
        assert response.status_code < 500

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_as_werkzeug_kwargs(graphql_strategy):
    case = graphql_strategy.example()
    assert case.as_werkzeug_kwargs() == {
        "method": "POST",
        "path": "/graphql",
        "query_string": None,
        "json": {"query": case.body},
        "headers": {"User-Agent": USER_AGENT},
    }


@pytest.mark.parametrize(
    "kwargs, base_path, expected",
    (
        ({"base_url": "http://0.0.0.0:1234/something"}, "/something", "http://0.0.0.0:1234/something"),
        ({"port": 8888}, "/graphql", "http://127.0.0.1:8888/graphql"),
    ),
)
@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_custom_base_url(graphql_url, kwargs, base_path, expected):
    # When a custom Base URL is specified
    schema = schemathesis.graphql.from_url(graphql_url, **kwargs)
    # Then the base path is changed, in this case it is the only available path
    assert schema.base_path == base_path
    strategy = schema[base_path]["POST"].as_strategy()
    case = strategy.example()
    # And all requests should go to the specified URL
    assert case.as_requests_kwargs()["url"] == expected


@pytest.mark.parametrize("kwargs", ({"body": "SomeQuery"}, {"body": b'{"query": "SomeQuery"}'}))
def test_make_case(graphql_schema, kwargs):
    case = graphql_schema["/graphql"]["POST"].make_case(**kwargs)
    assert isinstance(case, GraphQLCase)
    assert_requests_call(case)


def test_no_query(graphql_url):
    # When GraphQL schema does not contain the `Query` type
    response = requests.post(graphql_url, json={"query": INTROSPECTION_QUERY})
    decoded = response.json()
    raw_schema = decoded["data"]
    raw_schema["__schema"]["queryType"] = None
    schema = schemathesis.graphql.from_dict(raw_schema)
    # Then no operations should be collected
    assert list(schema.get_all_operations()) == []
