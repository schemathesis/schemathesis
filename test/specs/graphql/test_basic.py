import pytest
from hypothesis import given, settings

import schemathesis
from schemathesis.constants import USER_AGENT


@pytest.fixture()
def graphql_schema(graphql_endpoint):
    return schemathesis.graphql.from_url(graphql_endpoint)


@pytest.fixture
def graphql_strategy(graphql_schema):
    return graphql_schema["/graphql"]["POST"].as_strategy()


def test_raw_schema(graphql_schema):
    assert graphql_schema.verbose_name == "GraphQL"
    assert graphql_schema.raw_schema["__schema"]["types"][1] == {
        "kind": "OBJECT",
        "name": "Patron",
        "fields": [
            {
                "name": "id",
                "args": [],
                "type": {"kind": "SCALAR", "name": "ID", "ofType": None},
                "isDeprecated": False,
                "deprecationReason": None,
            },
            {
                "name": "name",
                "args": [],
                "type": {"kind": "SCALAR", "name": "String", "ofType": None},
                "isDeprecated": False,
                "deprecationReason": None,
            },
            {
                "name": "age",
                "args": [],
                "type": {"kind": "SCALAR", "name": "Int", "ofType": None},
                "isDeprecated": False,
                "deprecationReason": None,
            },
        ],
        "inputFields": None,
        "interfaces": [],
        "enumValues": None,
        "possibleTypes": None,
    }


@pytest.mark.hypothesis_nested
def test_query_strategy(graphql_strategy):
    @given(case=graphql_strategy)
    @settings(max_examples=10)
    def test(case):
        response = case.call()
        assert response.status_code < 500

    test()


@pytest.mark.filterwarnings("ignore:.*method is good for exploring strategies.*")
def test_get_code_to_reproduce(graphql_endpoint, graphql_strategy):
    case = graphql_strategy.example()
    assert (
        case.get_code_to_reproduce() == f"requests.post('{graphql_endpoint}', "
        f"json={{'query': {repr(case.body)}}}, "
        f"headers={{'User-Agent': '{USER_AGENT}'}})"
    )


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
def test_custom_base_url(graphql_endpoint, kwargs, base_path, expected):
    # When a custom Base URL is specified
    schema = schemathesis.graphql.from_url(graphql_endpoint, **kwargs)
    # Then the base path is changed, in this case it is the only available endpoint
    assert schema.base_path == base_path
    strategy = schema[base_path]["POST"].as_strategy()
    case = strategy.example()
    # And all requests should go to the specified URL
    assert case.as_requests_kwargs()["url"] == expected
