import platform
from test.utils import assert_requests_call
from unittest.mock import ANY

import pytest
import requests
from hypothesis import HealthCheck, given, settings

import schemathesis
from schemathesis.constants import SCHEMATHESIS_TEST_CASE_HEADER, USER_AGENT
from schemathesis.exceptions import SchemaError
from schemathesis.specs.graphql.loaders import get_introspection_query, extract_schema_from_response
from schemathesis.specs.graphql.schemas import GraphQLCase


def test_raw_schema(graphql_schema):
    assert graphql_schema.verbose_name == "GraphQL"


@pytest.mark.hypothesis_nested
def test_operation_strategy(graphql_strategy):
    @given(case=graphql_strategy)
    @settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
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
        "headers": {"User-Agent": USER_AGENT, SCHEMATHESIS_TEST_CASE_HEADER: ANY},
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
    if "port" in kwargs:
        with pytest.raises(SchemaError) as exc:
            schemathesis.graphql.from_url(graphql_url, **kwargs)
        if platform.system() == "Windows":
            detail = "[WinError 10061] No connection could be made because the target machine actively refused it"
        elif platform.system() == "Darwin":
            detail = "[Errno 61] Connection refused"
        else:
            detail = "[Errno 111] Connection refused"
        assert exc.value.extras == [f"Failed to establish a new connection: {detail}"]
    else:
        schema = schemathesis.graphql.from_url(graphql_url, **kwargs)
        # Then the base path is changed, in this case it is the only available path
        assert schema.base_path == base_path
        strategy = schema["Query"]["getBooks"].as_strategy()
        case = strategy.example()
        # And all requests should go to the specified URL
        assert case.as_requests_kwargs()["url"] == expected


@pytest.mark.parametrize("kwargs", ({"body": "SomeQuery"}, {"body": b'{"query": "SomeQuery"}'}))
def test_make_case(graphql_schema, kwargs):
    case = graphql_schema["Query"]["getBooks"].make_case(**kwargs)
    assert isinstance(case, GraphQLCase)
    assert_requests_call(case)


def test_no_query(graphql_url):
    # When GraphQL schema does not contain the `Query` type
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    raw_schema = decoded["data"]
    raw_schema["__schema"]["queryType"] = None
    raw_schema["__schema"]["mutationType"] = None
    schema = schemathesis.graphql.from_dict(raw_schema)
    # Then no operations should be collected
    assert list(schema.get_all_operations()) == []
    assert schema.operations_count == 0


@pytest.mark.parametrize("with_data_key", (True, False))
def test_data_key(graphql_url, with_data_key):
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    if not with_data_key:
        decoded = decoded["data"]
    schema = schemathesis.graphql.from_dict(decoded)
    assert schema.operations_count == 4


def test_malformed_response(graphql_url):
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    response._content += b"42"
    with pytest.raises(SchemaError, match="Received unsupported content while expecting a JSON payload for GraphQL"):
        extract_schema_from_response(response)


def test_operations_count(graphql_url):
    response = requests.post(graphql_url, json={"query": get_introspection_query()}, timeout=1)
    decoded = response.json()
    raw_schema = decoded["data"]
    schema = schemathesis.graphql.from_dict(raw_schema)
    assert schema.operations_count == 4


CUSTOM_QUERY_NAME = "MyQuery"
CUSTOM_MUTATION_NAME = "MyMutation"


@pytest.mark.parametrize("name", (CUSTOM_QUERY_NAME, CUSTOM_MUTATION_NAME))
def test_type_names(name):
    # When the user gives custom names to query types
    raw_schema = f"""
    schema {{
       query: {CUSTOM_QUERY_NAME}
       mutation: {CUSTOM_MUTATION_NAME}
    }}

    type {CUSTOM_QUERY_NAME} {{
       v: String
    }}
    type {CUSTOM_MUTATION_NAME} {{
       v(i: Int): String
    }}
    """
    # Then the schema should be loaded without errors
    schema = schemathesis.graphql.from_file(raw_schema)
    # And requests should be properly generated

    @given(case=schema[name]["v"].as_strategy())
    @settings(max_examples=1, deadline=None)
    def test(case):
        pass

    test()


@pytest.mark.parametrize(
    "schema, extension",
    (
        (
            """
type Query {
  func(created: Unknown!): Int!
}""",
            ".gql",
        ),
        (
            """
type Query {
  123(created: Int!): Int!
}""",
            ".whatever",
        ),
    ),
)
def test_schema_error(testdir, cli, snapshot_cli, schema, extension):
    schema_file = testdir.make_graphql_schema_file(schema, extension=extension)
    assert cli.run(str(schema_file), "--dry-run") == snapshot_cli


def test_unknown_type_name(graphql_schema):
    with pytest.raises(KeyError, match="`Qwery` type not found. Did you mean `Query`?"):
        graphql_schema["Qwery"]["getBooks"]
